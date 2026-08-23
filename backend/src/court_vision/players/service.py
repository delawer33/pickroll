"""Сезонные агрегаты игрока.

Считаются на лету из строк Box Score — единственного источника правды; предагрегатов и
materialized view в системе нет (см. docs/sprints/sprint-1.md). GROUP BY идёт по индексу
`ix_box_scores_player_season`.

Сервис не коммитит: unit of work равен HTTP-запросу, граница транзакции — в `db.py`.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import Row, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.errors import AmbiguousPlayerName, PlayerNotFound, SeasonDataMissing
from court_vision.players.models import REGULAR_SEASON, BoxScore, Player
from court_vision.players.resolver import (
    AmbiguousName,
    ResolvedPlayer,
    UnknownName,
    resolve_player_name,
)
from court_vision.players.schemas import (
    PlayerIdentity,
    PlayerStatsResponse,
    SeasonAverages,
    SeasonStats,
    SeasonTotals,
)
from court_vision.rounding import round_half_up
from court_vision.seed.rows import METRIC_INTRODUCED


async def get_player_stats(
    session: AsyncSession, *, player_name: str, season: str | None = None
) -> PlayerStatsResponse:
    """Резолвит имя и отдаёт агрегаты: за один сезон или за всю карьеру.

    Неуверенная резолюция и отсутствие данных — доменные ошибки, а не пустой ответ:
    вызывающая сторона должна различать «ноль очков» и «мы не знаем».
    """
    resolution = await resolve_player_name(session, player_name)
    match resolution:
        case UnknownName(query=query, nearest=nearest):
            raise PlayerNotFound(query, nearest)
        case AmbiguousName(query=query, candidates=candidates):
            raise AmbiguousPlayerName(query, candidates)
        case ResolvedPlayer(player=player):
            pass

    seasons = await season_stats(session, player_id=player.id, season=season)
    if not seasons:
        raise SeasonDataMissing(player.id, player.full_name, season)

    return PlayerStatsResponse(player=_identity(player), game_type=REGULAR_SEASON, seasons=seasons)


async def season_stats(
    session: AsyncSession, *, player_id: int, season: str | None = None
) -> list[SeasonStats]:
    """Сезоны игрока по возрастанию. Пустой список — данных нет; что это значит, решает
    вызывающий: у резолвнутого игрока это отсутствие сезона, а не ноль очков."""
    result = await session.execute(_aggregate_statement(player_id, season))
    return [_season_stats(row) for row in result]


def _aggregate_statement(player_id: int, season: str | None) -> Select[Any]:
    statement = (
        select(
            BoxScore.season,
            func.count().label("games_played"),
            func.sum(BoxScore.minutes).label("minutes"),
            func.sum(BoxScore.points).label("points"),
            func.sum(BoxScore.rebounds_total).label("rebounds_total"),
            func.sum(BoxScore.rebounds_offensive).label("rebounds_offensive"),
            func.sum(BoxScore.rebounds_defensive).label("rebounds_defensive"),
            func.sum(BoxScore.assists).label("assists"),
            func.sum(BoxScore.steals).label("steals"),
            func.sum(BoxScore.blocks).label("blocks"),
            func.sum(BoxScore.turnovers).label("turnovers"),
            func.sum(BoxScore.fouls_personal).label("fouls_personal"),
            func.sum(BoxScore.field_goals_made).label("field_goals_made"),
            func.sum(BoxScore.field_goals_attempted).label("field_goals_attempted"),
            func.sum(BoxScore.three_pointers_made).label("three_pointers_made"),
            func.sum(BoxScore.three_pointers_attempted).label("three_pointers_attempted"),
            func.sum(BoxScore.free_throws_made).label("free_throws_made"),
            func.sum(BoxScore.free_throws_attempted).label("free_throws_attempted"),
        )
        .where(
            BoxScore.player_id == player_id,
            # Только регулярный чемпионат: без фильтра сезонные средние молча смешали бы
            # предсезонку и Матч всех звёзд с регуляркой.
            BoxScore.game_type == REGULAR_SEASON,
            # Строка без минут — это DND/NWT: игрок был в заявке, но не выходил. Считать
            # такие матчи проведёнными значит занижать все средние. До введения минут
            # (1951-52) NULL означает «метрика не считалась», а не DND — те сезоны
            # фильтр пропускает целиком, иначе они исчезли бы из карьеры.
            or_(BoxScore.minutes.is_not(None), BoxScore.season < METRIC_INTRODUCED["minutes"]),
        )
        .group_by(BoxScore.season)
        .order_by(BoxScore.season)
    )
    return statement.where(BoxScore.season == season) if season is not None else statement


def _season_stats(row: Row[Any]) -> SeasonStats:
    totals = SeasonTotals(
        # minutes лежат в numeric — sum() по ним приезжает Decimal'ом. SUM по NULL — NULL:
        # для сезонов до введения метрики он остаётся None, а не превращается в ноль.
        minutes=float(row.minutes) if row.minutes is not None else None,
        points=row.points,
        rebounds_total=row.rebounds_total,
        rebounds_offensive=row.rebounds_offensive,
        rebounds_defensive=row.rebounds_defensive,
        assists=row.assists,
        steals=row.steals,
        blocks=row.blocks,
        turnovers=row.turnovers,
        fouls_personal=row.fouls_personal,
        field_goals_made=row.field_goals_made,
        field_goals_attempted=row.field_goals_attempted,
        three_pointers_made=row.three_pointers_made,
        three_pointers_attempted=row.three_pointers_attempted,
        free_throws_made=row.free_throws_made,
        free_throws_attempted=row.free_throws_attempted,
    )
    return SeasonStats(
        season=row.season,
        games_played=row.games_played,
        totals=totals,
        averages=_averages(totals, row.games_played),
    )


def _identity(player: Player) -> PlayerIdentity:
    return PlayerIdentity(
        player_id=player.id,
        full_name=player.full_name,
        position=player.position,
        birth_date=player.birth_date,
        country=player.country,
        draft_year=player.draft_year,
    )


def _averages(totals: SeasonTotals, games_played: int) -> SeasonAverages:
    def per_game(total: float) -> float:
        return round_half_up(Decimal(str(total)) / games_played, 1)

    def per_game_if_tracked(total: float | None) -> float | None:
        """None тотала — метрика не считалась в ту эпоху: среднее тоже None, не ноль."""
        return per_game(total) if total is not None else None

    return SeasonAverages(
        minutes=per_game_if_tracked(totals.minutes),
        points=per_game(totals.points),
        rebounds_total=per_game(totals.rebounds_total),
        rebounds_offensive=per_game_if_tracked(totals.rebounds_offensive),
        rebounds_defensive=per_game_if_tracked(totals.rebounds_defensive),
        assists=per_game(totals.assists),
        steals=per_game_if_tracked(totals.steals),
        blocks=per_game_if_tracked(totals.blocks),
        turnovers=per_game_if_tracked(totals.turnovers),
        fouls_personal=per_game(totals.fouls_personal),
        field_goals_made=per_game(totals.field_goals_made),
        field_goals_attempted=per_game(totals.field_goals_attempted),
        three_pointers_made=per_game_if_tracked(totals.three_pointers_made),
        three_pointers_attempted=per_game_if_tracked(totals.three_pointers_attempted),
        free_throws_made=per_game(totals.free_throws_made),
        free_throws_attempted=per_game(totals.free_throws_attempted),
        field_goal_percentage=_percentage(totals.field_goals_made, totals.field_goals_attempted),
        three_point_percentage=_percentage(
            totals.three_pointers_made, totals.three_pointers_attempted
        ),
        free_throw_percentage=_percentage(totals.free_throws_made, totals.free_throws_attempted),
    )


def _percentage(made: int | None, attempted: int | None) -> float | None:
    """None, а не ноль: не бросавший из-за дуги игрок — это не игрок с нулевым процентом.
    None во входе — метрика той эпохи не считалась, и процент считать не из чего."""
    if made is None or not attempted:
        return None
    return round_half_up(Decimal(made) / attempted, 3)
