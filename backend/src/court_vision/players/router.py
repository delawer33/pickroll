"""HTTP-поверхность модуля игроков.

Роутер тонкий: вся логика — в `service.py`, чтобы инструменты агента (спринт 3) вызывали
её напрямую, минуя HTTP.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from court_vision.db import SessionDep
from court_vision.players.schemas import SEASON_PATTERN, PlayerStatsResponse
from court_vision.players.service import get_player_stats

router = APIRouter(prefix="/players", tags=["players"])


@router.get("/stats", response_model=PlayerStatsResponse)
async def player_stats(
    session: SessionDep,
    player_name: Annotated[
        str,
        Query(
            min_length=1,
            max_length=128,
            description="Имя игрока свободным текстом, латиницей. Опечатки допустимы.",
        ),
    ],
    season: Annotated[
        str | None,
        Query(
            pattern=SEASON_PATTERN,
            description="Сезон вида `2012-13`. Без него возвращается вся карьера.",
        ),
    ] = None,
) -> PlayerStatsResponse:
    """Сезонные показатели игрока: средние, тоталы и число игр по регулярному чемпионату."""
    return await get_player_stats(session, player_name=player_name, season=season)
