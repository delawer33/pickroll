"""Интеграционные тесты резолюции игрока и сезонных агрегатов — через HTTP, на настоящем
Postgres с загруженной фикстурой датасета.

Фикстура собрана под эти тесты: в ней десятки игроков с фамилией James и Curry (для
неоднозначности), но строки Box Score есть только у четырёх звёзд.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.resolver import (
    AmbiguousName,
    ResolvedPlayer,
    UnknownName,
    resolve_player_name,
)

pytestmark = pytest.mark.usefixtures("seeded_database")

STATS_URL = "/api/v1/players/stats"

LEBRON_ID = 2544
PROBLEM_CONTENT_TYPE = "application/problem+json"


async def test_точное_имя_резолвится_в_игрока(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(STATS_URL, params={"player_name": "LeBron James"})

    assert response.status_code == 200
    player = response.json()["player"]
    assert player["player_id"] == LEBRON_ID
    assert player["full_name"] == "LeBron James"


async def test_опечатка_резолвится_в_того_же_игрока(api_client: httpx.AsyncClient) -> None:
    # Именно за этим фаззи-поиск: LLM ошибается в написании имён, и «Lebron» обязан
    # доехать до LeBron James, а не до Bronny James.
    response = await api_client.get(STATS_URL, params={"player_name": "Lebron"})

    assert response.status_code == 200
    assert response.json()["player"]["player_id"] == LEBRON_ID


async def test_кириллица_даёт_структурную_ошибку(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(STATS_URL, params={"player_name": "Леброн Джеймс"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    problem = response.json()
    assert problem["type"] == "urn:court-vision:problem:player-not-found"
    # На другом алфавите pg_trgm даёт ровный ноль: подсовывать «ближайших» тут значило бы
    # выдумывать.
    assert problem["nearest"] == []


async def test_неоднозначное_имя_возвращает_кандидатов(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(STATS_URL, params={"player_name": "James"})

    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == "urn:court-vision:problem:ambiguous-player-name"
    candidates = problem["candidates"]
    assert len(candidates) > 1
    # Произвольный выбор одного из тезоименинников — худший из возможных ответов.
    assert len({candidate["player_id"] for candidate in candidates}) == len(candidates)


async def test_несуществующий_игрок_возвращает_ближайших(api_client: httpx.AsyncClient) -> None:
    # Майкла Джордана в фикстуре нет, но есть Michael Curry — по нему видно, что ответ
    # структурный и агент может самокорректироваться вторым вызовом.
    response = await api_client.get(STATS_URL, params={"player_name": "Michael Jordan"})

    assert response.status_code == 404
    problem = response.json()
    assert problem["type"] == "urn:court-vision:problem:player-not-found"
    assert problem["nearest"]
    assert all(0 < candidate["similarity"] < 1 for candidate in problem["nearest"])


async def test_сезон_отдаёт_средние_тоталы_и_число_игр(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(
        STATS_URL, params={"player_name": "LeBron James", "season": "2012-13"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["game_type"] == "Regular Season"
    assert len(body["seasons"]) == 1

    season = body["seasons"][0]
    assert season["season"] == "2012-13"
    # Сверено с фикстурой напрямую: 76 матчей регулярного чемпионата (матчи с пустыми
    # минутами — DND — не в счёт), 2036 очков, 551 передача.
    assert season["games_played"] == 76
    assert season["totals"]["points"] == 2036
    assert season["totals"]["assists"] == 551
    assert season["averages"]["points"] == 26.8
    # Половина округляется вверх: 551 / 76 = 7.25, и NBA публикует 7.3.
    assert season["averages"]["assists"] == 7.3
    assert season["averages"]["field_goal_percentage"] == 0.565


async def test_без_сезона_отдаётся_вся_карьера(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(STATS_URL, params={"player_name": "LeBron James"})

    assert response.status_code == 200
    seasons = [season["season"] for season in response.json()["seasons"]]

    assert seasons == sorted(seasons)
    # Историческая эра начинается с 2005-06 — более ранние сезоны в систему не грузятся.
    assert seasons[0] == "2005-06"
    assert len(seasons) == 9
    assert all(season["games_played"] > 0 for season in response.json()["seasons"])


async def test_пустой_сезон_отличается_от_нулевой_статистики(
    api_client: httpx.AsyncClient,
) -> None:
    # Карри задрафтован в 2009-м: в сезоне 2005-06 у него не нулевые показатели, а нет
    # данных вообще, и ответ обязан говорить именно это.
    response = await api_client.get(
        STATS_URL, params={"player_name": "Stephen Curry", "season": "2005-06"}
    )

    assert response.status_code == 404
    problem = response.json()
    assert problem["type"] == "urn:court-vision:problem:season-data-missing"
    assert problem["season"] == "2005-06"
    assert problem["player"]["full_name"] == "Stephen Curry"


async def test_агрегаты_считаются_только_по_регулярному_чемпионату(
    api_client: httpx.AsyncClient,
) -> None:
    # У Кобе в фикстуре есть плей-офф, предсезонка и Матч всех звёзд сезона 2012-13;
    # смешать их с регуляркой значило бы соврать в каждом среднем.
    response = await api_client.get(
        STATS_URL, params={"player_name": "Kobe Bryant", "season": "2012-13"}
    )

    assert response.status_code == 200
    assert response.json()["seasons"][0]["games_played"] == 78


async def test_невалидный_сезон_тоже_problem_details(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(
        STATS_URL, params={"player_name": "LeBron James", "season": "2012/13"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    problem = response.json()
    assert problem["type"] == "urn:court-vision:problem:invalid-request"
    assert problem["errors"]


async def test_резольвер_вызывается_без_http_слоя(session: AsyncSession) -> None:
    """Резольвер — самостоятельная функция: инструменты спринта 3 вызывают её напрямую,
    минуя HTTP, и разбирают исход структурно."""
    resolution = await resolve_player_name(session, "  lebron   james ")

    assert isinstance(resolution, ResolvedPlayer)
    assert resolution.player.id == LEBRON_ID


@pytest.mark.parametrize(
    ("query", "expected_full_name"),
    [
        ("Stephen Curry", "Stephen Curry"),
        # Сокращения и опечатки — обычный вход от LLM.
        ("Steph Curry", "Stephen Curry"),
        ("Kobe Briant", "Kobe Bryant"),
        ("Harden", "James Harden"),
    ],
)
async def test_узнаваемое_имя_резолвится(
    session: AsyncSession, query: str, expected_full_name: str
) -> None:
    resolution = await resolve_player_name(session, query)

    assert isinstance(resolution, ResolvedPlayer)
    assert resolution.player.full_name == expected_full_name


@pytest.mark.parametrize(
    "query",
    [
        # Одна фамилия на нескольких игроков: сходство лидера отличается от остальных лишь
        # длиной имени, поэтому выбирать нельзя.
        "Bryant",
        "Curry",
        # Полные тёзки — сходство совпадает до цифры.
        "Mike James",
    ],
)
async def test_неоднозначное_имя_не_выбирает_за_вызывающего(
    session: AsyncSession, query: str
) -> None:
    resolution = await resolve_player_name(session, query)

    assert isinstance(resolution, AmbiguousName)
    assert len(resolution.candidates) > 1


@pytest.mark.parametrize(
    ("query", "expects_nearest"),
    [
        ("Леброн", False),
        ("", False),
        # Имя из другой эпохи: похожие в датасете есть, но ни один не узнаваем.
        ("Michael Jordan", True),
    ],
)
async def test_ниже_порога_сходства_имя_не_найдено(
    session: AsyncSession, query: str, expects_nearest: bool
) -> None:
    resolution = await resolve_player_name(session, query)

    assert isinstance(resolution, UnknownName)
    assert bool(resolution.nearest) is expects_nearest
