"""Тёзки без игровых данных не делают имя неоднозначным.

В датасете сотни полных тёзок, у которых игравший один, а остальные — строки справочника без
единого матча (так у Luka Doncic три записи, и без этого правила ни один Инструмент не
отвечал бы на вопрос о нём: имена кандидатов совпадают до символа).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.models import REGULAR_SEASON, BoxScore, Game, Player, Team
from court_vision.players.resolver import AmbiguousName, ResolvedPlayer, resolve_player_name

pytestmark = pytest.mark.usefixtures("clean_database")


async def _add_players(session: AsyncSession, played: set[int], ids: tuple[int, ...]) -> None:
    session.add_all(
        [
            Team(id=1, city="City", name="One", abbreviation="ONE"),
            Team(id=2, city="City", name="Two", abbreviation="TWO"),
        ]
    )
    await session.flush()
    session.add(
        Game(
            id=1,
            season="2015-16",
            game_date=date(2016, 1, 1),
            game_type=REGULAR_SEASON,
            home_team_id=1,
            away_team_id=2,
        )
    )
    for player_id in ids:
        session.add(
            Player(id=player_id, first_name="Dora", last_name="Quillby", full_name="Dora Quillby")
        )
    await session.flush()
    for player_id in played:
        session.add(
            BoxScore(
                game_id=1,
                player_id=player_id,
                season="2015-16",
                game_date=date(2016, 1, 1),
                game_type=REGULAR_SEASON,
                team_id=1,
                opponent_team_id=2,
                is_home=True,
                is_win=True,
                minutes=20,
                points=10,
                assists=1,
                rebounds_total=1,
                fouls_personal=0,
                field_goals_made=4,
                field_goals_attempted=8,
                free_throws_made=2,
                free_throws_attempted=2,
            )
        )
    await session.flush()


async def test_из_тёзок_играл_один_имя_резолвится_в_него(session: AsyncSession) -> None:
    await _add_players(session, played={2}, ids=(1, 2, 3))

    resolution = await resolve_player_name(session, "Dora Quillby")

    assert isinstance(resolution, ResolvedPlayer)
    assert resolution.player.id == 2


async def test_играли_двое_имя_остаётся_неоднозначным(session: AsyncSession) -> None:
    await _add_players(session, played={1, 2}, ids=(1, 2, 3))

    resolution = await resolve_player_name(session, "Dora Quillby")

    assert isinstance(resolution, AmbiguousName)
    assert {candidate.player_id for candidate in resolution.candidates} == {1, 2, 3}


async def test_никто_не_играл_имя_остаётся_неоднозначным(session: AsyncSession) -> None:
    await _add_players(session, played=set(), ids=(1, 2))

    assert isinstance(await resolve_player_name(session, "Dora Quillby"), AmbiguousName)
