"""Общая тестовая инфраструктура.

Постгрес поднимается настоящий (testcontainers) — подмена на SQLite запрещена
конвенциями: проект опирается на pg_trgm и pgvector.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from court_vision.db import create_engine, create_session_factory
from court_vision.seed.__main__ import asyncpg_dsn

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = BACKEND_ROOT / "tests" / "fixtures" / "dataset"


@pytest.fixture(scope="session")
def dataset_fixture_dir() -> Path:
    """Директория с маленькой копией датасета — её грузит тот же seed-код, что и боевые данные."""
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Один контейнер на прогон: старт Postgres дороже, чем очистка таблиц."""
    with PostgresContainer("pgvector/pgvector:pg17", driver="asyncpg") as container:
        url = container.get_connection_url()
        _run_migrations(url)
        yield url


def _run_migrations(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


@pytest.fixture
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    """Движок на тест, а не на сессию: pytest-asyncio даёт каждому тесту свой event loop,
    а соединения в пуле остаются привязанными к тому, в котором были открыты."""
    db_engine = create_engine(postgres_url)
    try:
        yield db_engine
    finally:
        await db_engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def db_connection(postgres_url: str) -> AsyncIterator[asyncpg.Connection[Any]]:
    """Сырое подключение asyncpg — им пользуется seed (COPY идёт мимо ORM)."""
    connection = await asyncpg.connect(asyncpg_dsn(postgres_url))
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def clean_database(db_connection: asyncpg.Connection[Any]) -> None:
    await db_connection.execute(
        "TRUNCATE box_scores, games, players, teams RESTART IDENTITY CASCADE"
    )


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session
