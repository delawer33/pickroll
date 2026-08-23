"""CLI индексации корпуса: `uv run python -m court_vision.corpus`.

По умолчанию индексирует оба источника; `--source` ограничивает одним. Повторный прогон
по неизменным источникам — no-op (diff по хешу), `--reindex` пересчитывает всё, в том
числе после смены модели эмбеддингов.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import structlog

from court_vision.config import Settings, get_settings
from court_vision.corpus.ingest import DEFAULT_CACHE_DIR, ingest
from court_vision.corpus.models import SourceType
from court_vision.db import create_engine, create_session_factory
from court_vision.logging import configure_logging

log = structlog.get_logger(__name__)


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
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    log.info("corpus.start", source=args.source or "all", reindex=args.reindex)
    asyncio.run(run(settings, source=args.source, reindex=args.reindex, cache_dir=args.cache_dir))


if __name__ == "__main__":
    main()
