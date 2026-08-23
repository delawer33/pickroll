"""Гибридный поиск и реранк на мини-корпусе с готовыми векторами — без вызовов API.

Векторы — разреженные базисные (768 float'ов, единица в одной координате): cosine-ранжирование
на них детерминировано, и тест управляет тем, какую половину поиска «убеждает» каждый чанк.
Реранкер мокается на границе litellm (`litellm.arerank`) — как и везде по конвенциям.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import litellm
import pytest
from litellm.types.rerank import RerankResponse, RerankResponseResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.config import Settings
from court_vision.corpus.models import Chunk, Document
from court_vision.corpus.rerank import maybe_rerank
from court_vision.corpus.search import ChunkHit, search

# Тексты подобраны под запрос "shot clock": PARAPHRASE не содержит обоих слов запроса
# (полнотекстовая половина его не видит), EXACT_RULE и WIKI содержат точный термин.
PARAPHRASE = "Teams have twenty-four seconds to attempt a field goal."
EXACT_RULE = "The offensive team must attempt a field goal before the shot clock expires."
WIKI = "The shot clock was introduced in the NBA in 1954 to speed up play."


def _basis(index: int) -> list[float]:
    """Базисный вектор: единица в `index`, нули дальше — попарно ортогональны."""
    vector = [0.0] * 768
    vector[index] = 1.0
    return vector


# Запрос «смотрит» вдоль нулевой оси: PARAPHRASE совпадает с ним (cosine-дистанция 0),
# остальные чанки ортогональны — векторная половина находит именно парафраз.
QUERY_EMBEDDING = _basis(0)


@pytest.fixture
async def mini_corpus(
    clean_database: None, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Два документа, три чанка: rulebook (парафраз + точный термин) и wikipedia (термин)."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        rulebook = Document(
            source_type="rulebook",
            title="NBA Official Playing Rules",
            url="https://r",
            content_hash="hash-rulebook",
            fetched_at=now,
        )
        wikipedia = Document(
            source_type="wikipedia",
            title="Shot clock",
            url="https://w",
            content_hash="hash-wiki",
            fetched_at=now,
        )
        session.add_all([rulebook, wikipedia])
        await session.flush()
        session.add_all(
            [
                Chunk(
                    document_id=rulebook.id,
                    locator="Rule 7 — Sec. I",
                    text=PARAPHRASE,
                    embedding=_basis(0),
                    embedding_model="test/embedder",
                ),
                Chunk(
                    document_id=rulebook.id,
                    locator="Rule 7 — Sec. II",
                    text=EXACT_RULE,
                    embedding=_basis(1),
                    embedding_model="test/embedder",
                ),
                Chunk(
                    document_id=wikipedia.id,
                    locator="History",
                    text=WIKI,
                    embedding=_basis(2),
                    embedding_model="test/embedder",
                ),
            ]
        )
        await session.commit()


class TestSearch:
    async def test_rrf_contains_vector_and_fulltext_finds(
        self, mini_corpus: None, session: AsyncSession
    ) -> None:
        hits = await search(
            session, query_embedding=QUERY_EMBEDDING, query_text="shot clock", top_k=8
        )

        texts = [hit.text for hit in hits]
        # Векторная половина: парафраз без слов запроса найден по близости эмбеддинга.
        assert PARAPHRASE in texts
        # Полнотекстовая половина: чанк с точным термином найден по tsvector.
        assert EXACT_RULE in texts
        # Балл RRF положителен и невозрастает — выдача упорядочена слиянием.
        scores = [hit.score for hit in hits]
        assert all(score > 0 for score in scores)
        assert scores == sorted(scores, reverse=True)

    async def test_hit_carries_citation_fields(
        self, mini_corpus: None, session: AsyncSession
    ) -> None:
        hits = await search(
            session, query_embedding=QUERY_EMBEDDING, query_text="shot clock", top_k=8
        )

        paraphrase = next(hit for hit in hits if hit.text == PARAPHRASE)
        assert paraphrase.document_title == "NBA Official Playing Rules"
        assert paraphrase.source_type == "rulebook"
        assert paraphrase.locator == "Rule 7 — Sec. I"
        assert paraphrase.url == "https://r"

    async def test_source_filter_excludes_other_source(
        self, mini_corpus: None, session: AsyncSession
    ) -> None:
        hits = await search(
            session,
            query_embedding=QUERY_EMBEDDING,
            query_text="shot clock",
            source="rulebook",
            top_k=8,
        )

        assert hits, "фильтр по источнику не должен опустошать выдачу"
        assert all(hit.source_type == "rulebook" for hit in hits)
        assert WIKI not in [hit.text for hit in hits]


def _hit(chunk_id: int, text: str, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=chunk_id,
        document_title="NBA Official Playing Rules",
        source_type="rulebook",
        locator=f"Rule {chunk_id}",
        text=text,
        url=None,
        score=score,
    )


HITS = [_hit(index, f"chunk {index}", 1.0 / (60 + index)) for index in range(1, 6)]


@dataclass
class FakeReranker:
    """Подставной `litellm.arerank`: отдаёт заготовку или падает, запоминает вызовы."""

    response: RerankResponse | Exception
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, **kwargs: Any) -> RerankResponse:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TestMaybeRerank:
    async def test_without_key_order_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _explode(**kwargs: Any) -> None:
            raise AssertionError("без ключа реранкер не должен вызываться")

        monkeypatch.setattr(litellm, "arerank", _explode)
        settings = Settings(rerank_api_key=None)

        assert await maybe_rerank(HITS, query="shot clock", settings=settings) == HITS

    async def test_with_key_reorders_by_relevance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeReranker(
            RerankResponse(
                id="rr-1",
                results=[
                    RerankResponseResult(index=3, relevance_score=0.9),
                    RerankResponseResult(index=0, relevance_score=0.4),
                ],
            )
        )
        monkeypatch.setattr(litellm, "arerank", fake)
        settings = Settings(rerank_api_key="jina-key", rag_top_k=2)

        reranked = await maybe_rerank(HITS, query="shot clock", settings=settings)

        assert [hit.chunk_id for hit in reranked] == [4, 1]
        assert [hit.score for hit in reranked] == [0.9, 0.4]
        # Провайдеру уехали тексты кандидатов и параметры из настроек.
        (call,) = fake.calls
        assert call["documents"] == [hit.text for hit in HITS]
        assert call["model"] == settings.rerank_model
        assert call["top_n"] == 2
        assert call["api_key"] == "jina-key"

    async def test_provider_failure_keeps_rrf_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(litellm, "arerank", FakeReranker(RuntimeError("jina is down")))
        settings = Settings(rerank_api_key="jina-key")

        assert await maybe_rerank(HITS, query="shot clock", settings=settings) == HITS
