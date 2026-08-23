"""Гибридный поиск по чанкам: векторные и полнотекстовые кандидаты, слитые RRF.

Обе половины считает Postgres одним запросом: top-50 по cosine и top-50 по ts_rank
соединяются FULL OUTER JOIN и сливаются Reciprocal Rank Fusion — чанк, найденный обеими
половинами, поднимается выше найденного одной. Выдача не короче 30: этот запас ест
реранкер (`rerank.py`), а при выключенном реранкере лишнее отрезает вызывающая сторона.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.corpus.models import Chunk, Document, SourceType


@dataclass(frozen=True)
class ChunkHit:
    """Найденный чанк вместе со всем, что нужно цитате, — вызывающей стороне не приходится
    ходить в БД второй раз."""

    chunk_id: int
    document_title: str
    source_type: SourceType
    locator: str
    text: str
    url: str | None
    score: float


# Константа RRF из оригинальной статьи (Cormack et al.): сглаживает вклад ранга так, что
# разница между 1-м и 2-м местом не подавляет сигнал от второй половины поиска.
RRF_K: Final = 60
# Кандидатов с каждой половины. Больше — дороже HNSW-обход и ts_rank без заметного
# выигрыша: то, что не попало в top-50 ни одной половины, в top-30 слияния не пробьётся.
CANDIDATES: Final = 50
# Нижняя граница выдачи: столько кандидатов уезжает в реранкер (см. `rerank.maybe_rerank`).
RERANK_POOL: Final = 30


async def search(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    query_text: str,
    source: SourceType | None = None,
    top_k: int,
) -> list[ChunkHit]:
    """Гибридный поиск одним SQL; выдача упорядочена по RRF-баллу.

    `source` сужает обе половины до одного источника ещё на этапе кандидатов — фильтр
    после слияния молча съедал бы глубину выдачи.
    """
    tsquery = func.plainto_tsquery("english", query_text)

    distance = Chunk.embedding.cosine_distance(query_embedding)
    by_vector = _candidates(distance.asc(), source).cte("by_vector")
    # Полнотекстовая половина ограничена совпадением с запросом: ts_rank без `@@` ранжировал
    # бы и чанки, не содержащие ни одного слова запроса.
    by_text = (
        _candidates(func.ts_rank(Chunk.ts, tsquery).desc(), source)
        .where(Chunk.ts.op("@@")(tsquery))
        .cte("by_text")
    )

    rrf_score = (
        func.coalesce(1.0 / (RRF_K + by_vector.c.rank), 0.0)
        + func.coalesce(1.0 / (RRF_K + by_text.c.rank), 0.0)
    ).label("score")
    fused = (
        select(func.coalesce(by_vector.c.chunk_id, by_text.c.chunk_id).label("chunk_id"), rrf_score)
        .select_from(
            by_vector.outerjoin(by_text, by_vector.c.chunk_id == by_text.c.chunk_id, full=True)
        )
        .cte("fused")
    )

    rows = (
        await session.execute(
            select(
                Chunk.id,
                Document.title,
                Document.source_type,
                Chunk.locator,
                Chunk.text,
                Document.url,
                fused.c.score,
            )
            .join_from(fused, Chunk, Chunk.id == fused.c.chunk_id)
            .join(Document, Chunk.document_id == Document.id)
            # id как tie-breaker: равные баллы (обе половины дали соседние ранги зеркально)
            # не должны менять порядок между прогонами.
            .order_by(fused.c.score.desc(), Chunk.id)
            .limit(max(top_k, RERANK_POOL))
        )
    ).all()
    return [
        ChunkHit(
            chunk_id=chunk_id,
            document_title=title,
            source_type=cast(SourceType, source_type),
            locator=locator,
            text=text,
            url=url,
            score=score,
        )
        for chunk_id, title, source_type, locator, text, url, score in rows
    ]


def _candidates(order_by: ColumnElement[Any], source: SourceType | None) -> Select[tuple[int, int]]:
    """Top-50 чанков по заданному порядку: `(chunk_id, rank)` — вход одной половины RRF."""
    query = (
        select(Chunk.id.label("chunk_id"), func.row_number().over(order_by=order_by).label("rank"))
        .order_by(order_by)
        .limit(CANDIDATES)
    )
    if source is not None:
        query = query.join(Document, Chunk.document_id == Document.id).where(
            Document.source_type == source
        )
    return query
