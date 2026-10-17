"""Проекция результата Инструмента — чистая логика, поэтому юнит-тестами.

Механику события проверяет `test_agent_loop.py` на настоящих данных; здесь проверяются
ветки формата, до которых настоящий Инструмент доходит редко: сезон без попыток, пустая
карьера, результат не той формы.
"""

from __future__ import annotations

from typing import Any

from court_vision.agent.evidence import (
    BLANK,
    QuotesView,
    TableView,
    project_player_stats,
    project_search_corpus,
)
from court_vision.agent.tools import evidence_for


def stats_result(*, seasons: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "player": {"player_id": 1, "full_name": "LeBron James"},
        "game_type": "regular",
        "seasons": seasons,
    }


def season(*, field_goal_percentage: float | None = 0.565) -> dict[str, Any]:
    averages = {
        "minutes": 37.9,
        "points": 26.8,
        "rebounds_total": 8.0,
        "rebounds_offensive": 1.3,
        "rebounds_defensive": 6.7,
        "assists": 7.3,
        "steals": 1.7,
        "blocks": 0.9,
        "turnovers": 3.0,
        "fouls_personal": 1.4,
        "field_goals_made": 10.1,
        "field_goals_attempted": 17.8,
        "three_pointers_made": 1.4,
        "three_pointers_attempted": 3.3,
        "free_throws_made": 5.3,
        "free_throws_attempted": 7.0,
        "field_goal_percentage": field_goal_percentage,
    }
    return {
        "season": "2012-13",
        "games_played": 76,
        "totals": {key: int(value) for key, value in averages.items() if value is not None},
        "averages": averages,
    }


def table(result: dict[str, Any]) -> TableView:
    view = project_player_stats(result)
    assert view is not None
    assert isinstance(view.root, TableView)
    return view.root


def test_сезон_становится_строкой_таблицы() -> None:
    assert table(stats_result(seasons=[season()])).rows == [
        ["2012-13", "76", "26.8", "8.0", "7.3", "56.5"]
    ]


def test_процент_без_попыток_прочерк_а_не_ноль() -> None:
    """`0.0` в карточке читается как «бросал и не попал» — это разные утверждения."""
    assert table(stats_result(seasons=[season(field_goal_percentage=None)])).rows[0][-1] == BLANK


def test_пустая_карьера_проекции_не_даёт() -> None:
    """Таблица с шапкой и без строк — это «данных нет», сказанное видом таблицы с данными."""
    assert project_player_stats(stats_result(seasons=[])) is None


def test_результат_не_той_формы_не_роняет_ответ() -> None:
    """Проекции не будет, но ответ Агента из-за неё падать не должен: карточка обойдётся
    штампом источника."""
    assert project_player_stats({"unexpected": True}) is None


def test_выдача_поиска_становится_списком_цитат_с_номерами() -> None:
    view = project_search_corpus(
        {
            "results": [
                {
                    "n": 3,
                    "title": "NBA Official Playing Rules",
                    "locator": "Rule 11 — Sec. I",
                    "text": "Goaltending is touching the ball on its downward flight.",
                    "url": "https://r",
                }
            ]
        }
    )

    assert view is not None
    assert isinstance(view.root, QuotesView)
    (quote,) = view.root.quotes
    assert (quote.index, quote.locator) == (3, "Rule 11 — Sec. I")


def test_пустая_выдача_поиска_проекции_не_даёт() -> None:
    """Карточка с пустым списком цитат — «данных нет», сказанное видом данных."""
    assert project_search_corpus({"results": []}) is None


def test_выдача_не_той_формы_не_роняет_ответ() -> None:
    assert project_search_corpus({"chunks": [1]}) is None


def test_отказ_инструмента_становится_причиной_а_не_проекцией() -> None:
    evidence = evidence_for(
        name="get_player_stats",
        result={"error": {"kind": "season-data-missing", "detail": "Данных за сезон нет."}},
    )

    assert evidence.view is None
    assert evidence.failure is not None
    assert evidence.failure.kind == "season-data-missing"
    assert evidence.failure.detail == "Данных за сезон нет."


def test_неизвестный_инструмент_проекции_не_имеет() -> None:
    """До сюда такой вызов не доходит — `execute_tool` отвечает на него отказом, — но
    падать на нём нельзя: имя Инструмента приходит от модели."""
    evidence = evidence_for(name="no_such_tool", result={"chunks": []})

    assert evidence.failure is None
    assert evidence.view is None
