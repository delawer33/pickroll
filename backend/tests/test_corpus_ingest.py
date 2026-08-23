"""Идемпотентность ingest'а: diff по хешу, переиндексация, отказ от чужой модели.

Сеть не трогается: источники подменяются фикстурными `RawDocument`, эмбеддер мокается
на границе litellm (`litellm.aembedding`) — механика ingest'а работает настоящая,
включая учёт в llm_usage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import litellm
import pytest
from litellm.types.utils import EmbeddingResponse, Usage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import court_vision.corpus.ingest as ingest_module
from court_vision.agent.models import LlmUsage
from court_vision.config import Settings
from court_vision.corpus.ingest import ForeignEmbeddingModelError, ingest
from court_vision.corpus.models import Chunk, Document
from court_vision.corpus.sources import RawDocument

RULEBOOK_TEXT = """\
RULE NO. 1—COURT DIMENSIONS—EQUIPMENT
Section I—Court and Dimensions
a. The playing court shall be measured and marked as shown in the court diagram.
Section II—Equipment
a. The backboard shall be a rectangle measuring 6' horizontally.
"""

CHANGED_RULEBOOK_TEXT = (
    RULEBOOK_TEXT
    + """\
Section III—Ball
a. The ball shall be an officially approved NBA ball.
"""
)

WIKI_TEXT = """\
'''LeBron James''' is an American professional basketball player.

== Playing career ==
James was selected first overall in the 2003 draft.
"""


def rulebook(text: str = RULEBOOK_TEXT) -> RawDocument:
    return RawDocument(
        source_type="rulebook", title="NBA Official Playing Rules", url="https://r", text=text
    )


@dataclass
class FakeEmbedder:
    """Подставной `litellm.aembedding`: детерминированные векторы, запоминает вызовы."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, **kwargs: Any) -> EmbeddingResponse:
        self.calls.append(kwargs)
        texts: list[str] = kwargs["input"]
        data = [
            # Обратный порядок index — ingest обязан пересортировать по нему сам.
            {"embedding": _vector(text), "index": index, "object": "embedding"}
            for index, text in enumerate(texts)
        ][::-1]
        return EmbeddingResponse(
            model="gemini-embedding-001",
            data=data,
            usage=Usage(prompt_tokens=7 * len(texts), completion_tokens=0, total_tokens=0),
        )


def _vector(text: str) -> list[float]:
    """768 float'ов, детерминированных текстом, — векторы разных чанков различимы."""
    seed = (len(text) % 97) / 100
    return [seed] + [0.001] * 767


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    fake = FakeEmbedder()
    monkeypatch.setattr(litellm, "aembedding", fake)
    return fake


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> dict[str, RawDocument]:
    """Фикстурные источники вместо сети; тест меняет словарь, чтобы «изменить» документ."""
    docs = {"rulebook": rulebook()}

    async def fake_titles(session: AsyncSession) -> list[str]:
        return ["LeBron James"]

    monkeypatch.setattr(ingest_module, "fetch_rulebook", lambda cache_dir: docs["rulebook"])
    monkeypatch.setattr(
        ingest_module,
        "iter_wikipedia",
        lambda titles: iter(
            [
                RawDocument(source_type="wikipedia", title=title, url="https://w", text=WIKI_TEXT)
                for title in titles
            ]
        ),
    )
    monkeypatch.setattr(ingest_module, "wikipedia_titles", fake_titles)
    return docs


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def _counts(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with session_factory() as session:
        documents = (await session.execute(select(func.count(Document.id)))).scalar_one()
        chunks = (await session.execute(select(func.count(Chunk.id)))).scalar_one()
    return documents, chunks


class TestIngest:
    async def test_indexes_all_sources(
        self,
        clean_database: None,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sources: dict[str, RawDocument],
        embedder: FakeEmbedder,
    ) -> None:
        stats = await ingest(session_factory, settings)

        assert stats.indexed == 2
        assert stats.skipped == 0
        documents, chunks = await _counts(session_factory)
        assert documents == 2
        assert chunks > 0
        async with session_factory() as session:
            models = (
                (await session.execute(select(Chunk.embedding_model).distinct())).scalars().all()
            )
            operations = (
                (await session.execute(select(LlmUsage.operation).distinct())).scalars().all()
            )
        assert models == [settings.embedding_model]
        assert operations == ["corpus.embed"]

    async def test_repeat_ingest_is_noop(
        self,
        clean_database: None,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sources: dict[str, RawDocument],
        embedder: FakeEmbedder,
    ) -> None:
        await ingest(session_factory, settings, source="rulebook")
        calls_after_first = len(embedder.calls)
        _, chunks_after_first = await _counts(session_factory)

        stats = await ingest(session_factory, settings, source="rulebook")

        assert stats.indexed == 0
        assert stats.skipped == 1
        assert len(embedder.calls) == calls_after_first
        assert await _counts(session_factory) == (1, chunks_after_first)

    async def test_changed_hash_reindexes_document(
        self,
        clean_database: None,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sources: dict[str, RawDocument],
        embedder: FakeEmbedder,
    ) -> None:
        await ingest(session_factory, settings, source="rulebook")
        sources["rulebook"] = rulebook(CHANGED_RULEBOOK_TEXT)

        stats = await ingest(session_factory, settings, source="rulebook")

        assert stats.indexed == 1
        async with session_factory() as session:
            locators = (await session.execute(select(Chunk.locator))).scalars().all()
            documents = (await session.execute(select(func.count(Document.id)))).scalar_one()
        # Чанки заменены целиком: появилась новая секция, документ остался один.
        assert documents == 1
        assert "Rule 1 — Sec. III" in locators

    async def test_foreign_embedding_model_refused_without_reindex(
        self,
        clean_database: None,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sources: dict[str, RawDocument],
        embedder: FakeEmbedder,
    ) -> None:
        await ingest(session_factory, settings, source="rulebook")
        async with session_factory() as session:
            await session.execute(update(Chunk).values(embedding_model="other/embedder"))
            await session.commit()

        with pytest.raises(ForeignEmbeddingModelError):
            await ingest(session_factory, settings, source="rulebook")

    async def test_reindex_replaces_foreign_embeddings(
        self,
        clean_database: None,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sources: dict[str, RawDocument],
        embedder: FakeEmbedder,
    ) -> None:
        await ingest(session_factory, settings, source="rulebook")
        async with session_factory() as session:
            await session.execute(update(Chunk).values(embedding_model="other/embedder"))
            await session.commit()

        stats = await ingest(session_factory, settings, source="rulebook", reindex=True)

        assert stats.indexed == 1
        async with session_factory() as session:
            models = (
                (await session.execute(select(Chunk.embedding_model).distinct())).scalars().all()
            )
        assert models == [settings.embedding_model]
