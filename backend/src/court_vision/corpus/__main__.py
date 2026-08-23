"""CLI индексации корпуса: `uv run python -m court_vision.corpus`.

По умолчанию индексирует оба источника; `--source` ограничивает одним. Повторный прогон
по неизменным источникам — no-op (diff по хешу), `--reindex` пересчитывает всё, в том
числе после смены модели эмбеддингов.

`--eval` вместо индексации гоняет ретривал-eval по живому индексу: запросы берутся из
`backend/evals/golden.yaml`, их векторы из файла игнорируются и считаются живым API,
а прогонов два — с реранкером и без; таблица сравнения печатается в stdout. По ней
принимается решение, включать ли Jina по умолчанию.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path

import structlog

from court_vision.agent.transport import embed
from court_vision.config import Settings, get_settings
from court_vision.corpus.eval import EvalReport, evaluate, load_golden
from court_vision.corpus.ingest import DEFAULT_CACHE_DIR, ingest
from court_vision.corpus.models import SourceType
from court_vision.db import create_engine, create_session_factory
from court_vision.logging import configure_logging

log = structlog.get_logger(__name__)

# Из src/court_vision/corpus/ три уровня вверх — backend/. Файл нужен только в dev-чекауте:
# в собранном пакете eval-фикстур нет, как нет и самого CLI-сценария.
DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parents[3] / "evals" / "golden.yaml"


async def run(
    settings: Settings, *, source: SourceType | None, reindex: bool, cache_dir: Path
) -> None:
    engine = create_engine(settings.database_url)
    try:
        await ingest(
            create_session_factory(engine),
            settings,
            source=source,
            reindex=reindex,
            cache_dir=cache_dir,
        )
    finally:
        await engine.dispose()


async def run_eval(settings: Settings, *, golden_path: Path) -> None:
    """Живой eval: эмбеддинги запросов через API, поиск по боевому индексу, реранкер on/off."""
    queries = load_golden(golden_path)
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        vectors = await embed(
            session_factory,
            model=settings.embedding_model,
            texts=[query.query for query in queries],
            dimensions=settings.embedding_dimensions,
            timeout=settings.llm_timeout_seconds,
            operation="corpus.eval_embed",
            api_key=settings.llm_api_key or None,
            api_base=settings.llm_api_base or None,
        )
        live = [
            replace(query, embedding=vector) for query, vector in zip(queries, vectors, strict=True)
        ]
        async with session_factory() as session:
            without = await evaluate(session, live, settings=settings, rerank=False)
            with_rerank = await evaluate(session, live, settings=settings, rerank=True)
    finally:
        await engine.dispose()

    _print_comparison(without, with_rerank, rerank_enabled=bool(settings.rerank_api_key))


def _print_comparison(
    without: EvalReport, with_rerank: EvalReport, *, rerank_enabled: bool
) -> None:
    """Таблица on/off реранкера — print, а не лог: это и есть результат команды."""
    print(f"{'':24}{'recall@10':>12}{'MRR':>8}")
    for label, report in (("reranker off", without), ("reranker on", with_rerank)):
        print(f"{label:24}{report.recall_at_10:>12.3f}{report.mrr:>8.3f}")
        for source, (recall, mrr) in report.by_source.items():
            print(f"  {source:22}{recall:>12.3f}{mrr:>8.3f}")
    if not rerank_enabled:
        print("RERANK_API_KEY пуст — реранкер выключен, строки идентичны.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="court_vision.corpus", description=__doc__)
    parser.add_argument(
        "--source",
        choices=("rulebook", "wikipedia"),
        help="индексировать только один источник (по умолчанию оба)",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="пересчитать всё, игнорируя хеши — нужно после смены модели эмбеддингов",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"куда скачивать rulebook (по умолчанию {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        help="вместо индексации прогнать ретривал-eval по живому индексу (реранкер on/off)",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help=f"файл золотых запросов для --eval (по умолчанию {DEFAULT_GOLDEN_PATH})",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    if args.eval:
        log.info("corpus.eval_start", golden=str(args.golden))
        asyncio.run(run_eval(settings, golden_path=args.golden))
        return
    log.info("corpus.start", source=args.source or "all", reindex=args.reindex)
    asyncio.run(run(settings, source=args.source, reindex=args.reindex, cache_dir=args.cache_dir))


if __name__ == "__main__":
    main()
