"""Общая тестовая инфраструктура.

Постгрес поднимается настоящий (testcontainers) — подмена на SQLite запрещена
конвенциями: проект опирается на pg_trgm и pgvector.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

import asyncpg
import httpx
import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jwt.algorithms import RSAAlgorithm
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from court_vision.config import Settings
from court_vision.db import create_engine, create_session_factory
from court_vision.main import create_app
from court_vision.seed.__main__ import asyncpg_dsn
from court_vision.seed.loader import seed_database
from court_vision.seed.source import local_source

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


@pytest.fixture
async def seeded_database(
    clean_database: None,
    db_connection: asyncpg.Connection[Any],
    dataset_fixture_dir: Path,
) -> None:
    """Фикстура датасета, загруженная боевым seed-кодом: тесты работают на тех же данных
    и в том же виде, что и приложение."""
    await seed_database(db_connection, local_source(dataset_fixture_dir))


@dataclass(frozen=True)
class SigningKey:
    """RSA-пара, которой тесты подписывают JWT вместо живого Clerk."""

    kid: str
    private_key: rsa.RSAPrivateKey


def _generate_signing_key(kid: str) -> SigningKey:
    return SigningKey(
        kid=kid, private_key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )


@pytest.fixture(scope="session")
def signing_key() -> SigningKey:
    """Ключ «нашего IdP» — его публичная половина раздаётся через локальный JWKS."""
    return _generate_signing_key("test-key")


@pytest.fixture(scope="session")
def foreign_signing_key() -> SigningKey:
    """Ключ «чужого IdP» с тем же kid: поиск ключа в JWKS проходит, а подпись — нет.

    Именно так проверяется криптография, а не только маршрутизация по kid.
    """
    return _generate_signing_key("test-key")


@pytest.fixture(scope="session")
def jwks_url(signing_key: SigningKey) -> Iterator[str]:
    """Локально поднятый JWKS: бэкенд проходит настоящий путь «скачай ключи по URL,
    найди по kid, проверь подпись» — без живого Clerk и без байпасов."""
    jwk = json.loads(RSAAlgorithm.to_jwk(signing_key.private_key.public_key()))
    jwk.update({"kid": signing_key.kid, "alg": "RS256", "use": "sig"})
    body = json.dumps({"keys": [jwk]}).encode()

    class JWKSHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Молчим: логи http.server замусоривают вывод pytest."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), JWKSHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/.well-known/jwks.json"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class TokenFactory(Protocol):
    def __call__(
        self,
        sub: str = ...,
        *,
        expires_in: timedelta = ...,
        key: SigningKey | None = ...,
    ) -> str: ...


@pytest.fixture(scope="session")
def make_token(signing_key: SigningKey) -> TokenFactory:
    """Фабрика тестовых JWT — общий инструмент для всех срезов, где нужен «залогиненный»
    запрос. Отрицательный `expires_in` даёт истёкший токен, `key` — подпись чужим ключом."""

    def _make(
        sub: str = "user_test",
        *,
        expires_in: timedelta = timedelta(hours=1),
        key: SigningKey | None = None,
    ) -> str:
        key = key or signing_key
        now = datetime.now(UTC)
        return jwt.encode(
            {"sub": sub, "iat": now, "exp": now + expires_in},
            key.private_key,
            algorithm="RS256",
            headers={"kid": key.kid},
        )

    return _make


@pytest.fixture
def auth_headers(make_token: TokenFactory) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def app(
    postgres_url: str,
    jwks_url: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> FastAPI:
    """Приложение поверх тестовых Postgres и JWKS.

    Фабрика сессий кладётся в `app.state` руками, а не через lifespan: ASGITransport его
    не проигрывает, да и движок в тестах обязан быть привязан к event loop текущего теста.
    """
    application = create_app(Settings(database_url=postgres_url, clerk_jwks_url=jwks_url))
    application.state.session_factory = session_factory
    return application


@pytest.fixture
async def api_client(
    app: FastAPI, auth_headers: dict[str, str]
) -> AsyncIterator[httpx.AsyncClient]:
    """Клиент от лица залогиненного пользователя — обычный случай интеграционных тестов."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    ) as client:
        yield client


@pytest.fixture
async def anonymous_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Клиент без токена — для тестов самого auth."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
