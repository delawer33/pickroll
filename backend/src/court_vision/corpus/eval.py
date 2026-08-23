"""Ретривал-eval: recall@10 и MRR гибридного поиска по золотым запросам.

Один и тот же `evaluate` работает в двух режимах: CI гоняет его на фикстурном корпусе с
предвычисленными векторами (см. `backend/evals/`), локальный `--eval` — на живом индексе
с живыми эмбеддингами запросов. Сам `evaluate` эмбеддингов не считает: вектор обязан
приехать в `GoldenQuery` — кто его посчитал (генератор фикстуры или API), решает
вызывающая сторона.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.config import Settings
from court_vision.corpus.models import SourceType
from court_vision.corpus.rerank import maybe_rerank
from court_vision.corpus.search import ChunkHit, search

# Глубина метрик: recall@10 — «нашлось ли вообще», MRR по тем же 10 позициям — «насколько
# высоко». Глубже смотреть незачем: агент цитирует из верхушки выдачи (rag_top_k=8).
CUTOFF: Final = 10


@dataclass(frozen=True)
class GoldenQuery:
    """Один золотой запрос: текст, ожидаемое место в корпусе и вектор.

    `source` — метка для разбивки `by_source`, а не фильтр поиска: eval гоняет запрос по
    всему корпусу, иначе он не увидел бы перетекание выдачи между источниками.
    `expect_locator` опционален: для wiki-статей попадание в документ уже сигнал, а вот у
    rulebook документ один, и без локатора запрос был бы тривиален.
    """

    query: str
    source: SourceType | None
    expect_document: str
    expect_locator: str | None
    embedding: list[float] | None  # None -> посчитать через API (локальный режим)


@dataclass(frozen=True)
class EvalReport:
    """Метрики прогона: общие и в разбивке по источникам `{source: (recall@10, mrr)}`."""

    recall_at_10: float
    mrr: float
    by_source: dict[str, tuple[float, float]]


async def evaluate(
    session: AsyncSession,
    queries: Sequence[GoldenQuery],
    *,
    settings: Settings,
    rerank: bool,
) -> EvalReport:
    """Прогоняет золотые запросы через гибридный поиск (и реранкер, если `rerank`).

    Ранг запроса — позиция первого чанка ожидаемого документа (и локатора, если задан)
    в топ-10; не нашлось — вклад в recall и MRR нулевой.
    """
    ranks: list[tuple[GoldenQuery, int | None]] = []
    for query in queries:
        if query.embedding is None:
            raise ValueError(f"query {query.query!r} has no embedding; compute it before evaluate")
        hits = await search(
            session, query_embedding=query.embedding, query_text=query.query, top_k=CUTOFF
        )
        if rerank:
            hits = await maybe_rerank(hits, query=query.query, settings=settings)
        ranks.append((query, _rank(query, hits[:CUTOFF])))

    recall, mrr = _metrics([rank for _, rank in ranks])
    by_source: dict[str, tuple[float, float]] = {
        source: _metrics([rank for query, rank in ranks if query.source == source])
        for source in sorted({query.source for query, _ in ranks if query.source is not None})
    }
    return EvalReport(recall_at_10=recall, mrr=mrr, by_source=by_source)


def load_golden(path: Path) -> list[GoldenQuery]:
    """Золотые запросы из YAML (формат — см. генератор `scripts/build_eval_fixture.py`)."""
    entries = cast(list[dict[str, object]], yaml.safe_load(path.read_text(encoding="utf-8")))
    return [
        GoldenQuery(
            query=cast(str, entry["query"]),
            source=cast(SourceType | None, entry["source"]),
            expect_document=cast(str, entry["expect_document"]),
            expect_locator=cast(str | None, entry["expect_locator"]),
            embedding=cast(list[float] | None, entry.get("embedding")),
        )
        for entry in entries
    ]


def _rank(query: GoldenQuery, hits: list[ChunkHit]) -> int | None:
    """1-based позиция первого попадания в ожидание; None — не нашлось в топе."""
    for position, hit in enumerate(hits, start=1):
        if hit.document_title != query.expect_document:
            continue
        if query.expect_locator is None or hit.locator == query.expect_locator:
            return position
    return None


def _metrics(ranks: Sequence[int | None]) -> tuple[float, float]:
    """(recall@10, MRR) по списку рангов; пустой список даёт нули, а не деление на ноль."""
    if not ranks:
        return (0.0, 0.0)
    recall = sum(1 for rank in ranks if rank is not None) / len(ranks)
    mrr = sum(1.0 / rank for rank in ranks if rank is not None) / len(ranks)
    return (recall, mrr)
