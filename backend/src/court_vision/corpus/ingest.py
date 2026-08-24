"""Оркестрация индексации корпуса: выгрузка → diff по хешу → чанки → эмбеддинги → upsert.

Идемпотентность держится на `content_hash` документа: повторный прогон по неизменным
источникам не делает ни одного вызова эмбеддера и ни одной записи — поэтому прерванную
выгрузку безопасно просто перезапустить. Документ пишется одной транзакцией: наполовину
переиндексированных документов в БД не бывает.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.errors import ProviderRequestRejected, ProviderUnavailable
from court_vision.agent.transport import embed
from court_vision.config import Settings
from court_vision.corpus.chunking import chunk_document
from court_vision.corpus.models import Chunk, Document, SourceType
from court_vision.corpus.sources import (
    RawDocument,
    fetch_rulebook,
    iter_wikipedia,
    wikipedia_titles,
)

log = structlog.get_logger(__name__)

DEFAULT_CACHE_DIR: Final = Path("data/corpus")
_MAX_FAILED_STREAK: Final = 5


class ForeignEmbeddingModelError(Exception):
    """В индексе лежат векторы другой модели — дописывать к ним нельзя.

    Векторы разных моделей живут в разных пространствах, и поиск по их смеси молча
    отдавал бы мусор. Лечится только полной переиндексацией: `--reindex`.
    """

    def __init__(self, found: list[str], configured: str) -> None:
        self.found = found
        self.configured = configured
        super().__init__(
            f"index contains embeddings by {found}, configured model is {configured!r}; "
            "rerun with --reindex to rebuild the index"
        )


@dataclass(frozen=True)
class IngestStats:
    """Итог прогона: сколько документов переиндексировано и сколько пропущено по хешу."""

    indexed: int
    skipped: int


async def ingest(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    source: SourceType | None = None,
    reindex: bool = False,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> IngestStats:
    """Индексирует источники корпуса; `source=None` — все. `reindex` игнорирует хеши
    и перезаписывает векторы, в том числе посчитанные другой моделью."""
    await _refuse_foreign_index(session_factory, settings.embedding_model, reindex=reindex)

    indexed = 0
    skipped = 0
    failed_streak = 0
    async for raw in _documents(session_factory, source, cache_dir):
        try:
            done = await _ingest_document(session_factory, settings, raw, reindex=reindex)
        except (ProviderUnavailable, ProviderRequestRejected) as error:
            # Транзиентный сбой эмбеддингов не должен убивать многочасовой прогон:
            # документ скипаем (повторный запуск доберёт по хешу), но серия подряд —
            # провайдер лёг, продолжать бессмысленно.
            failed_streak += 1
            log.warning("corpus.document_failed", title=raw.title, cause=type(error).__name__)
            if failed_streak >= _MAX_FAILED_STREAK:
                raise
            continue
        failed_streak = 0
        if done:
            indexed += 1
        else:
            skipped += 1
    log.info("corpus.ingest_done", indexed=indexed, skipped=skipped, reindex=reindex)
    return IngestStats(indexed=indexed, skipped=skipped)


async def _documents(
    session_factory: async_sessionmaker[AsyncSession],
    source: SourceType | None,
    cache_dir: Path,
) -> AsyncIterator[RawDocument]:
    """Документы запрошенных источников. Wikipedia отдаётся потоком: статей тысячи,
    и держать их все в памяти незачем."""
    if source in (None, "rulebook"):
        yield fetch_rulebook(cache_dir)
    if source in (None, "wikipedia"):
        async with session_factory() as session:
            titles = await wikipedia_titles(session)
        for raw in iter_wikipedia(titles):
            yield raw


async def _refuse_foreign_index(
    session_factory: async_sessionmaker[AsyncSession], configured: str, *, reindex: bool
) -> None:
    async with session_factory() as session:
        models = (await session.execute(select(Chunk.embedding_model).distinct())).scalars().all()
    foreign = sorted(model for model in models if model != configured)
    if foreign and not reindex:
        raise ForeignEmbeddingModelError(foreign, configured)


async def _ingest_document(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    raw: RawDocument,
    *,
    reindex: bool,
) -> bool:
    """Индексирует один документ; False — пропущен по совпавшему хешу.

    Эмбеддинги считаются до открытия транзакции записи: вызовы провайдера долгие, и
    держать под них открытую транзакцию значило бы занимать соединение пула минутами.
    """
    content_hash = hashlib.sha256(raw.text.encode()).hexdigest()

    async with session_factory() as session:
        document = await _find_document(session, raw)
        if document is not None and document.content_hash == content_hash and not reindex:
            return False

    drafts = chunk_document(raw)
    vectors = await embed(
        session_factory,
        model=settings.embedding_model,
        texts=[draft.embed_text for draft in drafts],
        dimensions=settings.embedding_dimensions,
        timeout=settings.llm_timeout_seconds,
        operation="corpus.embed",
        api_key=settings.llm_api_key or None,
        api_base=settings.llm_api_base or None,
    )

    # Одна транзакция на документ: шапка и чанки меняются атомарно, delete/insert вместо
    # поштучного диффа чанков — их границы всё равно поплыли при любой правке текста.
    async with session_factory() as session:
        document = await _find_document(session, raw)
        if document is None:
            document = Document(source_type=raw.source_type, title=raw.title)
            session.add(document)
        document.url = raw.url
        document.content_hash = content_hash
        document.fetched_at = datetime.now(UTC)
        await session.flush()
        await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        session.add_all(
            Chunk(
                document_id=document.id,
                locator=draft.locator,
                text=draft.text,
                embedding=vector,
                embedding_model=settings.embedding_model,
            )
            for draft, vector in zip(drafts, vectors, strict=True)
        )
        await session.commit()
    log.info("corpus.document_indexed", title=raw.title, chunks=len(drafts))
    return True


async def _find_document(session: AsyncSession, raw: RawDocument) -> Document | None:
    return (
        await session.execute(
            select(Document).where(
                Document.source_type == raw.source_type, Document.title == raw.title
            )
        )
    ).scalar_one_or_none()
