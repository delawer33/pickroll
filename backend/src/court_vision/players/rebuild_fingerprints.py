"""CLI пересборки Статистических отпечатков.

Запуск: `uv run python -m court_vision.players.rebuild_fingerprints`. Полная замена таблицы в
одной транзакции; повторный запуск по тем же данным даёт то же содержимое. Вызывается после
seed (его CLI делает это сам) и после любой загрузки Box Score.
"""

from __future__ import annotations

import asyncio

import structlog

from court_vision.config import get_settings
from court_vision.db import create_engine, create_session_factory
from court_vision.logging import configure_logging
from court_vision.players.fingerprints import rebuild_fingerprints

log = structlog.get_logger(__name__)


async def run(database_url: str) -> int:
    """Пересобирает отпечатки в базе по `database_url`; возвращает число строк."""
    engine = create_engine(database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            return await rebuild_fingerprints(session)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging()
    count = asyncio.run(run(get_settings().database_url))
    log.info("fingerprints.done", fingerprints=count)


if __name__ == "__main__":
    main()
