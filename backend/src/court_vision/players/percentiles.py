"""Перцентили игрока в сезоне среди Квалифицированных игроков.

Считаются на лету одним SQL из строк Box Score, как и сезонные агрегаты (см. `service.py`):
предагрегатов нет. Бюджет — 300 мс на полный сезон; выйдет за него — расчёт материализуется
таблицей сезонных агрегатов. Пока цена — один проход по строкам сезона.

Сервис не коммитит: unit of work равен HTTP-запросу, граница транзакции — в `db.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import Numeric, Select, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.errors import SeasonDataMissing
from court_vision.players.models import REGULAR_SEASON, BoxScore, Game
from court_vision.players.schemas import MetricPercentile, PlayerPercentilesResponse
from court_vision.players.service import counted_games, player_identity, resolve_player
from court_vision.rounding import round_half_up

# Квалифицированный игрок (CONTEXT.md): сыграл не меньше половины матчей сезона и, если
# минуты в ту эпоху считались, не меньше 15 минут за игру.
MIN_GAMES_SHARE: Final = Decimal("0.5")
MIN_MINUTES_PER_GAME: Final = 15

# Вес штрафных в знаменателе True Shooting: 0.44 — стандартная оценка доли владений,
# закрытых штрафными.
FREE_THROW_POSSESSION_WEIGHT: Final = Decimal("0.44")


@dataclass(frozen=True, slots=True)
class _Metric:
    key: str
    label: str
    # Знаков после запятой в публикуемом значении: за игру — 1, доли — 3 (как в SeasonAverages).
    places: int
    # Потери: меньше — лучше, Перцентиль считается с конца.
    lower_is_better: bool = False


METRICS: Final = (
    _Metric("points", "Pts", 1),
    _Metric("rebounds", "Reb", 1),
    _Metric("assists", "Ast", 1),
    _Metric("steals", "Stl", 1),
    _Metric("blocks", "Blk", 1),
    _Metric("turnovers", "TOV (inv.)", 1, lower_is_better=True),
    _Metric("minutes", "Min", 1),
    _Metric("field_goal_percentage", "FG%", 3),
    _Metric("three_point_percentage", "3P%", 3),
    _Metric("free_throw_percentage", "FT%", 3),
    _Metric("true_shooting_percentage", "TS%", 3),
)


async def get_player_percentiles(
    session: AsyncSession, *, player_name: str, season: str | None = None
) -> PlayerPercentilesResponse:
    """Резолвит имя и отдаёт Перцентили за сезон; без сезона — за последний сезон игрока.

    Неквалифицированный игрок не ошибка: Перцентили считаются относительно выборки, а флаг
    `qualified=False` говорит, что доверять им вслепую нельзя.
    """
    player = await resolve_player(session, player_name)

    if season is None:
        latest = select(func.max(BoxScore.season)).where(
            BoxScore.player_id == player.id, counted_games()
        )
        season = await session.scalar(latest)
        if season is None:
            raise SeasonDataMissing(player.id, player.full_name, None)

    row = (await session.execute(_percentiles_statement(player.id, season))).one_or_none()
    if row is None:
        raise SeasonDataMissing(player.id, player.full_name, season)

    return PlayerPercentilesResponse(
        player=player_identity(player),
        season=season,
        game_type=REGULAR_SEASON,
        games_played=row.games_played,
        qualified=row.qualified,
        sample_size=row.sample_size,
        metrics=[_metric_percentile(row, metric) for metric in METRICS],
    )


def _metric_percentile(row: Any, metric: _Metric) -> MetricPercentile:
    raw = row._mapping[metric.key]
    if raw is None:
        # Метрика не считалась (или считать не из чего): значения нет, Перцентиля тоже.
        return MetricPercentile(
            metric=metric.key,
            label=metric.label,
            value=None,
            lower_is_better=metric.lower_is_better,
        )
    percent_rank = row._mapping[f"{metric.key}_rank"]
    return MetricPercentile(
        metric=metric.key,
        label=metric.label,
        value=round_half_up(Decimal(raw), metric.places),
        percentile=int(round_half_up(percent_rank * 100, 0)),
        lower_is_better=metric.lower_is_better,
    )


def _percentiles_statement(player_id: int, season: str) -> Select[Any]:
    # Сезонные суммы каждого игрока — тот же отбор матчей, что у `get_player_stats`.
    totals = (
        select(
            BoxScore.player_id,
            func.count().label("gp"),
            func.sum(BoxScore.minutes).label("minutes"),
            func.sum(BoxScore.points).label("points"),
            func.sum(BoxScore.rebounds_total).label("rebounds"),
            func.sum(BoxScore.assists).label("assists"),
            func.sum(BoxScore.steals).label("steals"),
            func.sum(BoxScore.blocks).label("blocks"),
            func.sum(BoxScore.turnovers).label("turnovers"),
            func.sum(BoxScore.field_goals_made).label("fgm"),
            func.sum(BoxScore.field_goals_attempted).label("fga"),
            func.sum(BoxScore.three_pointers_made).label("tpm"),
            func.sum(BoxScore.three_pointers_attempted).label("tpa"),
            func.sum(BoxScore.free_throws_made).label("ftm"),
            func.sum(BoxScore.free_throws_attempted).label("fta"),
        )
        .where(BoxScore.season == season, counted_games())
        .group_by(BoxScore.player_id)
        .cte("totals")
    )
    t = totals.c

    # Матчей в сезоне — по команде с наибольшим числом матчей: игроки сравниваются с
    # календарём, а не друг с другом (лига сокращённого сезона не обнуляет всех).
    team_games = (
        select(Game.home_team_id.label("team_id"))
        .where(Game.season == season, Game.game_type == REGULAR_SEASON)
        .union_all(
            select(Game.away_team_id).where(Game.season == season, Game.game_type == REGULAR_SEASON)
        )
        .subquery()
    )
    games_per_team = (
        select(func.count().label("n")).select_from(team_games).group_by(team_games.c.team_id)
    ).subquery()
    season_games = select(func.max(games_per_team.c.n)).scalar_subquery()

    def num(column: Any) -> Any:
        return cast(column, Numeric)

    def per_game(column: Any) -> Any:
        """NULL тотала (метрика не считалась) остаётся NULL — не ноль."""
        return num(column) / t.gp

    def share(made: Any, attempted: Any) -> Any:
        # Нет бросков — нет процента, а не 0%.
        return num(made) / func.nullif(attempted, 0)

    qualified = (
        (t.gp >= season_games * MIN_GAMES_SHARE)
        & or_(t.minutes.is_(None), t.minutes >= MIN_MINUTES_PER_GAME * t.gp)
    ).label("qualified")

    lines = select(
        t.player_id,
        t.gp.label("games_played"),
        func.coalesce(qualified, False).label("qualified"),
        per_game(t.points).label("points"),
        per_game(t.rebounds).label("rebounds"),
        per_game(t.assists).label("assists"),
        per_game(t.steals).label("steals"),
        per_game(t.blocks).label("blocks"),
        per_game(t.turnovers).label("turnovers"),
        per_game(t.minutes).label("minutes"),
        share(t.fgm, t.fga).label("field_goal_percentage"),
        share(t.tpm, t.tpa).label("three_point_percentage"),
        share(t.ftm, t.fta).label("free_throw_percentage"),
        (
            num(t.points)
            / func.nullif(2 * (t.fga + literal(FREE_THROW_POSSESSION_WEIGHT) * t.fta), 0)
        ).label("true_shooting_percentage"),
    ).cte("lines")
    c = lines.c

    # Выборка — Квалифицированные игроки плюс сам искомый игрок: неквалифицированный в неё
    # не входит по определению, но percent_rank по выборке с ним даёт ровно «доля
    # Квалифицированных ниже него». Игрок с NULL-метрикой уходит в свой раздел окна и не
    # разбавляет ранжирование остальных.
    ranks = [
        func.percent_rank()
        .over(
            partition_by=c[m.key].is_(None),
            order_by=c[m.key].desc() if m.lower_is_better else c[m.key].asc(),
        )
        .label(f"{m.key}_rank")
        for m in METRICS
    ]
    pool = (
        select(
            c.player_id,
            c.games_played,
            c.qualified,
            func.count().filter(c.qualified).over().label("sample_size"),
            *(c[m.key] for m in METRICS),
            *ranks,
        )
        .where(or_(c.qualified, c.player_id == player_id))
        .cte("pool")
    )
    return select(pool).where(pool.c.player_id == player_id)
