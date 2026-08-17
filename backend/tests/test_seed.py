"""Интеграционные тесты seed'а на настоящем Postgres.

Грузится та же фикстура тем же кодом, что и боевой датасет, — проверяется и сам seed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest

from court_vision.seed.loader import seed_database
from court_vision.seed.source import local_source

pytestmark = pytest.mark.usefixtures("clean_database")

LEBRON_ID = 2544
KOBE_ID = 977


async def test_фикстура_грузится_и_наполняет_все_таблицы(
    db_connection: asyncpg.Connection[Any],
    dataset_fixture_dir: Path,
) -> None:
    result = await seed_database(db_connection, local_source(dataset_fixture_dir))

    assert result.teams > 0
    assert result.players > 0
    assert result.games > 0
    assert result.box_scores > 0

    assert await db_connection.fetchval("SELECT count(*) FROM box_scores") == result.box_scores
    assert (
        await db_connection.fetchval("SELECT full_name FROM players WHERE id = $1", LEBRON_ID)
        == "LeBron James"
    )


async def test_повторный_запуск_не_создаёт_дублей(
    db_connection: asyncpg.Connection[Any],
    dataset_fixture_dir: Path,
) -> None:
    source = local_source(dataset_fixture_dir)

    first = await seed_database(db_connection, source)
    counts_after_first = await _table_counts(db_connection)

    second = await seed_database(db_connection, source)
    counts_after_second = await _table_counts(db_connection)

    assert counts_after_first == counts_after_second
    assert first.box_scores == second.box_scores


async def test_сезоны_до_исторической_эры_не_грузятся(
    db_connection: asyncpg.Connection[Any],
    dataset_fixture_dir: Path,
) -> None:
    await seed_database(db_connection, local_source(dataset_fixture_dir))

    earliest_game_season = await db_connection.fetchval("SELECT min(season) FROM games")
    earliest_box_score_season = await db_connection.fetchval("SELECT min(season) FROM box_scores")

    assert earliest_game_season == "2005-06"
    assert earliest_box_score_season == "2005-06"

    # В фикстуре у Кобе есть матчи сезона 2004-05 — они не должны были доехать.
    assert (
        await db_connection.fetchval(
            "SELECT count(*) FROM box_scores WHERE player_id = $1 AND season < '2005-06'",
            KOBE_ID,
        )
        == 0
    )


async def test_тип_матча_доезжает_до_строк_box_score(
    db_connection: asyncpg.Connection[Any],
    dataset_fixture_dir: Path,
) -> None:
    await seed_database(db_connection, local_source(dataset_fixture_dir))

    # Без типа на самой строке сезонные средние молча смешали бы предсезонку с регуляркой.
    assert (
        await db_connection.fetchval("SELECT count(*) FROM box_scores WHERE game_type IS NULL") == 0
    )
    assert (
        await db_connection.fetchval(
            "SELECT count(*) FROM box_scores WHERE game_type = 'Regular Season'"
        )
        > 0
    )

    mismatched = await db_connection.fetchval(
        "SELECT count(*) FROM box_scores b JOIN games g ON g.id = b.game_id "
        "WHERE b.game_type <> g.game_type OR b.season <> g.season"
    )
    assert mismatched == 0


async def test_строки_box_score_ссылаются_на_загруженные_матчи_и_игроков(
    db_connection: asyncpg.Connection[Any],
    dataset_fixture_dir: Path,
) -> None:
    await seed_database(db_connection, local_source(dataset_fixture_dir))

    orphans = await db_connection.fetchval(
        "SELECT count(*) FROM box_scores b "
        "LEFT JOIN games g ON g.id = b.game_id "
        "LEFT JOIN players p ON p.id = b.player_id "
        "WHERE g.id IS NULL OR p.id IS NULL"
    )

    assert orphans == 0


async def _table_counts(connection: asyncpg.Connection[Any]) -> dict[str, int]:
    return {
        table: await connection.fetchval(f"SELECT count(*) FROM {table}")
        for table in ("teams", "players", "games", "box_scores")
    }
