"""Инструмент `compare_players` и Проекция `comparison` — на синтетических сезонах.

Сезон и игроки те же, что у тестов Перцентилей: сами Перцентили там уже сверены с независимым
расчётом, здесь проверяется то, что добавляет сравнение, — резолюция имён, 2–4 игрока,
разные сезоны, отказы и форма карточки.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.agent.evidence import BLANK, ComparisonView
from court_vision.agent.loop import ToolContext
from court_vision.agent.tools import evidence_for, execute_tool
from court_vision.players.percentiles import METRICS, get_player_percentiles
from court_vision.players.service import get_player_stats
from tests.test_player_percentiles import OLD_SEASON, PLAYERS, SEASON, _insert_season

pytestmark = pytest.mark.usefixtures("clean_database")


async def _compare(session: AsyncSession, players: list[dict[str, Any]]) -> dict[str, Any]:
    return await execute_tool(
        session,
        name="compare_players",
        arguments={"players": players},
        ctx=cast(ToolContext, None),  # Инструмент контекст хода не использует
    )


async def test_сравнение_отдаёт_перцентили_каждого_в_порядке_запроса(
    session: AsyncSession,
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session,
        [
            {"player_name": "Marvin Telgast", "season": SEASON},
            {"player_name": "Quentin Zorbax", "season": SEASON},
        ],
    )

    assert "error" not in result
    json.dumps(result)  # результат уходит модели
    names = [item["player"]["full_name"] for item in result["players"]]
    assert names == ["Marvin Telgast", "Quentin Zorbax"]
    for item in result["players"]:
        alone = await get_player_percentiles(
            session, player_name=item["player"]["full_name"], season=SEASON
        )
        assert item == alone.model_dump(mode="json")


async def test_без_сезона_берётся_последний_сезон_каждого(session: AsyncSession) -> None:
    await _insert_season(session, OLD_SEASON, PLAYERS[:2])
    await _insert_season(session, SEASON, PLAYERS[1:3])

    result = await _compare(
        session, [{"player_name": "Marvin Telgast"}, {"player_name": "Quentin Zorbax"}]
    )

    # Телгаст играл в обоих сезонах — берётся поздний; Зорбакс только в раннем.
    assert [item["season"] for item in result["players"]] == [SEASON, OLD_SEASON]


@pytest.mark.parametrize("count", [2, 3, 4])
async def test_принимает_от_двух_до_четырёх_игроков(session: AsyncSession, count: int) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session, [{"player_name": player.name, "season": SEASON} for player in PLAYERS[:count]]
    )

    assert len(result["players"]) == count


@pytest.mark.parametrize("count", [0, 1, 5])
async def test_число_игроков_вне_двух_четырёх_это_невалидные_аргументы(
    session: AsyncSession, count: int
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session, [{"player_name": player.name, "season": SEASON} for player in PLAYERS[:count]]
    )

    assert result["error"]["kind"] == "invalid-arguments"


async def test_неуверенная_резолюция_любого_имени_роняет_всё_сравнение(
    session: AsyncSession,
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session,
        [
            {"player_name": "Quentin Zorbax", "season": SEASON},
            {"player_name": "Zzzzzz Qqqqqq", "season": SEASON},
        ],
    )

    failure = result["error"]
    assert failure["kind"] == "player-not-found"
    assert failure["entry"] == 1  # чьё имя поправить
    assert "players" not in result  # частичного сравнения нет
    assert evidence_for(name="compare_players", result=result).failure is not None


async def test_нет_матчей_в_сезоне_одного_из_игроков_это_отказ_с_номером_записи(
    session: AsyncSession,
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session,
        [
            {"player_name": "Quentin Zorbax", "season": SEASON},
            {"player_name": "Marvin Telgast", "season": "2001-02"},
        ],
    )

    assert result["error"]["kind"] == "season-data-missing"
    assert result["error"]["entry"] == 1


async def test_один_игрок_в_одном_сезоне_дважды_это_отказ(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session,
        [{"player_name": "Quentin Zorbax"}, {"player_name": "Quentin Zorbax", "season": SEASON}],
    )

    assert result["error"]["kind"] == "duplicate-players"


async def test_проекция_это_игроки_на_метрики_со_значениями_и_перцентилями(
    session: AsyncSession,
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session,
        [
            {"player_name": "Quentin Zorbax", "season": SEASON},
            {"player_name": "Ignatz Brollo", "season": SEASON},
        ],
    )
    evidence = evidence_for(name="compare_players", result=result)

    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, ComparisonView)
    assert [(p.name, p.season, p.qualified) for p in view.players] == [
        ("Quentin Zorbax", SEASON, True),
        ("Ignatz Brollo", SEASON, False),  # звезда без нужного числа матчей
    ]
    assert [metric.label for metric in view.metrics] == [m.label for m in METRICS]
    points = view.metrics[0]
    assert [cell.value for cell in points.cells] == ["20.0", "30.0"]
    assert all(len(metric.cells) == len(view.players) for metric in view.metrics)
    # Проценты в карточке — проценты, а не доли 0–1.
    field_goal = next(metric for metric in view.metrics if metric.label == "FG%")
    assert field_goal.cells[0].value == "50.0"


async def test_цифры_карточки_совпадают_с_get_player_stats(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)
    result = await _compare(
        session,
        [
            {"player_name": "Osmund Draveck", "season": SEASON},
            {"player_name": "Hector Vandross", "season": SEASON},
        ],
    )
    evidence = evidence_for(name="compare_players", result=result)
    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, ComparisonView)

    for column, name in enumerate(["Osmund Draveck", "Hector Vandross"]):
        stats = await get_player_stats(session, player_name=name, season=SEASON)
        averages = stats.seasons[0].averages
        shown = {metric.label: metric.cells[column].value for metric in view.metrics}
        assert shown["Pts"] == f"{averages.points:.1f}"
        assert shown["Reb"] == f"{averages.rebounds_total:.1f}"
        assert shown["Ast"] == f"{averages.assists:.1f}"
        assert averages.field_goal_percentage is not None
        assert shown["FG%"] == f"{averages.field_goal_percentage * 100:.1f}"


async def test_метрика_которой_нет_в_эпохе_это_прочерк_а_не_ноль(session: AsyncSession) -> None:
    await _insert_season(session, OLD_SEASON, PLAYERS)
    await _insert_season(session, SEASON, PLAYERS)

    result = await _compare(
        session,
        [
            {"player_name": "Quentin Zorbax", "season": OLD_SEASON},
            {"player_name": "Quentin Zorbax", "season": SEASON},
        ],
    )
    evidence = evidence_for(name="compare_players", result=result)

    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, ComparisonView)
    by_label = {metric.label: metric.cells for metric in view.metrics}
    old, new = by_label["3P%"]
    assert (old.value, old.percentile) == (BLANK, None)
    assert new.percentile is not None
    assert by_label["Pts"][0].percentile is not None  # очки считались и тогда
    assert len(view.metrics) == len(METRICS)
