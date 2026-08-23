"""Реранкер — выключаемая ступень поиска по корпусу.

Пустой `RERANK_API_KEY` = ступени нет: выдача остаётся в порядке RRF-слияния (см.
`.env.example`). С ключом топ-30 кандидатов пересортировывает Jina; её отказ — это
потеря качества ранжирования, а не поиска, поэтому он логируется и глушится.
"""

from __future__ import annotations

from dataclasses import replace

import litellm
import structlog

from court_vision.config import Settings
from court_vision.corpus.search import RERANK_POOL, ChunkHit

log = structlog.get_logger(__name__)


async def maybe_rerank(hits: list[ChunkHit], *, query: str, settings: Settings) -> list[ChunkHit]:
    """Пересортировка топ-30 реранкером до `rag_top_k`; без ключа — `hits` как есть.

    Любая ошибка реранкера (сеть, ключ, кривой ответ) возвращает порядок RRF: поиск не
    имеет права упасть из-за необязательной ступени.
    """
    if not settings.rerank_api_key:
        return hits

    pool = hits[:RERANK_POOL]
    try:
        response = await litellm.arerank(
            model=settings.rerank_model,
            query=query,
            documents=[hit.text for hit in pool],
            top_n=settings.rag_top_k,
            api_key=settings.rerank_api_key,
        )
        # Порядок ответа провайдер не гарантирует — сортируем по релевантности сами.
        # Разбор ответа внутри try: кривой ответ (индекс мимо пула) — тоже отказ Jina.
        results = sorted(
            response.results, key=lambda result: float(result["relevance_score"]), reverse=True
        )
        return [
            replace(pool[int(result["index"])], score=float(result["relevance_score"]))
            for result in results[: settings.rag_top_k]
        ]
    except Exception:
        # Широкий except намеренно: иерархия исключений принадлежит litellm и меняется
        # от версии к версии (см. agent/transport.py).
        log.exception("corpus.rerank_failed", model=settings.rerank_model)
        return hits
