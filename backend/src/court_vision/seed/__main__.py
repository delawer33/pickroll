"""CLI seed'а: `uv run python -m court_vision.seed`.

По умолчанию скачивает запиненную версию датасета с Kaggle и грузит сезоны с 2005-06.
`--source-dir` подставляет локальную директорию с CSV того же формата — так грузится
тестовая фикстура, и тем же кодом.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncpg
import structlog

from court_vision.config import get_settings
from court_vision.logging import configure_logging
from court_vision.seed.loader import seed_database
from court_vision.seed.source import DatasetSource, download_source, local_source

DEFAULT_CACHE_DIR = Path("data/raw")

log = structlog.get_logger(__name__)


def asyncpg_dsn(database_url: str) -> str:
    """SQLAlchemy-URL → DSN для asyncpg: COPY идёт мимо ORM, напрямую драйвером."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def run(source: DatasetSource, database_url: str) -> None:
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        await seed_database(connection, source)
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="court_vision.seed", description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="директория с CSV-файлами датасета вместо скачивания с Kaggle",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"куда скачивать датасет (по умолчанию {DEFAULT_CACHE_DIR})",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()

    source = local_source(args.source_dir) if args.source_dir else download_source(args.cache_dir)
    log.info("seed.start", source=str(source.directory))

    asyncio.run(run(source, settings.database_url))


if __name__ == "__main__":
    main()
