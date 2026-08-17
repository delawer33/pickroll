"""Поведение запроса на границах: транзакция, обработчики ошибок, формат Problem Details.

Проверяется не логика конкретного среза, а общий каркас, на который опираются все:
закрывается ли транзакция вместе с запросом и доходит ли ошибка до браузера читаемой.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from court_vision.problems import PROBLEM_CONTENT_TYPE

pytestmark = pytest.mark.usefixtures("seeded_database")

STATS_URL = "/api/v1/players/stats"
CRASH_PATH = "/__crash"
ORIGIN = "http://localhost:5173"


@pytest.fixture
def crashing_app(app: FastAPI) -> FastAPI:
    """Маршрут, падающий необработанным исключением. Вне `/api/v1` — иначе до него не
    дошло бы дело: auth отклонил бы запрос раньше."""

    @app.get(CRASH_PATH)
    async def crash() -> None:
        raise RuntimeError("что-то сломалось внутри")

    return app


@pytest.fixture
def unit_of_work_calls(app: FastAPI, engine: AsyncEngine) -> list[str]:
    """Подменяет фабрику сессий на следящую: тест видит, чем закончилась транзакция.

    Проверять «нет открытых транзакций» через `pg_stat_activity` недостаточно — брошенный
    асинхронный генератор финализируется за такт цикла событий, и к моменту, когда httpx
    вернул ответ, следов уже не остаётся. Наблюдаемый контракт здесь — сам факт вызова
    commit или rollback до того, как запрос завершился.
    """
    calls: list[str] = []

    class SpySession(AsyncSession):
        async def commit(self) -> None:
            calls.append("commit")
            await super().commit()

        async def rollback(self) -> None:
            calls.append("rollback")
            await super().rollback()

    app.state.session_factory = async_sessionmaker(
        engine, class_=SpySession, expire_on_commit=False
    )
    return calls


async def test_доменная_ошибка_откатывает_транзакцию(
    api_client: httpx.AsyncClient, unit_of_work_calls: list[str]
) -> None:
    """Без отката соединение уходит в пул как `idle in transaction`: держит снапшот и
    блокирует VACUUM, пока его не подберёт сборщик мусора."""
    response = await api_client.get(STATS_URL, params={"player_name": "Леброн Джеймс"})

    assert response.status_code == 404
    assert unit_of_work_calls == ["rollback"]


async def test_успешный_запрос_коммитит(
    api_client: httpx.AsyncClient, unit_of_work_calls: list[str]
) -> None:
    response = await api_client.get(STATS_URL, params={"player_name": "LeBron James"})

    assert response.status_code == 200
    assert unit_of_work_calls == ["commit"]


async def test_запрос_не_оставляет_открытых_транзакций(
    api_client: httpx.AsyncClient,
    db_connection: asyncpg.Connection[Any],
) -> None:
    """Проверка конечного состояния в дополнение к контракту выше: соединение вернулось
    в пул закрытым."""
    await api_client.get(STATS_URL, params={"player_name": "Леброн Джеймс"})

    idle_in_transaction = await db_connection.fetchval(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND state = 'idle in transaction'"
    )

    assert idle_in_transaction == 0


async def test_пятисотка_доезжает_до_браузера_с_cors_заголовком(
    crashing_app: FastAPI,
) -> None:
    """Без CORS-заголовка браузер видит сетевую ошибку вместо тела Problem Details —
    именно тогда, когда диагностика нужнее всего."""
    transport = httpx.ASGITransport(app=crashing_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(CRASH_PATH, headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.json()["type"] == "urn:court-vision:problem:internal-error"
    # Наружу — ровное сообщение без внутренностей.
    assert "что-то сломалось внутри" not in response.text


async def test_доменная_ошибка_тоже_приходит_с_cors_заголовком(
    app: FastAPI, auth_headers: dict[str, str]
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            STATS_URL,
            params={"player_name": "Леброн Джеймс"},
            headers={**auth_headers, "Origin": ORIGIN},
        )

    assert response.status_code == 404
    assert response.headers.get("access-control-allow-origin") == ORIGIN


async def test_метод_не_поддержан_сохраняет_заголовок_allow(
    anonymous_client: httpx.AsyncClient,
) -> None:
    """405 без `Allow` нарушает RFC 9110 §15.5.6, а подменяя обработчик Starlette, мы
    обязаны сохранить заголовки, которые он отдавал."""
    response = await anonymous_client.post("/health")

    assert response.status_code == 405
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert "GET" in response.headers.get("allow", "")


async def test_расширяющие_члены_не_затирают_стандартные(
    api_client: httpx.AsyncClient,
) -> None:
    """`status` в теле обязан совпадать со статусной строкой: клиент, разбирающий тело,
    не должен получить от расширения другое значение."""
    response = await api_client.get(STATS_URL, params={"player_name": "James"})

    problem = response.json()
    assert response.status_code == 409
    assert problem["status"] == 409
    assert problem["type"] == "urn:court-vision:problem:ambiguous-player-name"
    # Расширения на месте — тест проверяет приоритет, а не их отсутствие.
    assert problem["candidates"]
