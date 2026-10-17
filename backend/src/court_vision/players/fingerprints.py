"""Сборка Статистических отпечатков игрок-сезонов (см. CONTEXT.md).

Отпечаток — девять признаков сезона, каждый приведён к z-score среди Квалифицированных
игроков того же сезона. Нормализация внутри сезона и делает сравнимыми эпохи: игрок 90-х,
который был на два сигма выше лиги по передачам, и игрок 2020-х с тем же превышением —
один и тот же вектор, хотя сырые цифры у них разные.

Таблица производная и пересобирается целиком (`rebuild_fingerprints`): z-score сезона
зависит от всей выборки сезона, поэтому точечного обновления нет. Сервис не коммитит — границу
транзакции держит вызывающий (CLI `rebuild_fingerprints.py`, тест).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import fmean, pstdev
from typing import Any, Final

import structlog
from sqlalchemy import Select, case, delete, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.models import (
    FINGERPRINT_DIMENSIONS,
    REGULAR_SEASON,
    BoxScore,
    Game,
    PlayerFingerprint,
)
from court_vision.players.percentiles import (
    FREE_THROW_POSSESSION_WEIGHT,
    MIN_GAMES_SHARE,
    MIN_MINUTES_PER_GAME,
)
from court_vision.players.service import counted_games
from court_vision.rounding import round_half_up

log = structlog.get_logger(__name__)

# Объёмные признаки пересчитываются на 36 минут: игрок на 24 минутах и на 36 с одинаковой
# продуктивностью должны получить один отпечаток.
_PER_MINUTES: Final = 36
# z-score хранится с четырьмя знаками: достаточно для косинуса и объяснения, и пересборка
# даёт побайтово то же содержимое (идемпотентность не зависит от хвоста float).
_Z_PLACES: Final = 4
_BATCH_SIZE: Final = 5000


@dataclass(frozen=True, slots=True)
class Feature:
    key: str
    label: str


# Порядок — порядок компонент вектора. Менять его или состав — значит новая миграция
# (размерность колонки) и пересборка: старые векторы станут бессмысленными.
FEATURES: Final = (
    Feature("points_per36", "Pts/36"),
    Feature("rebounds_per36", "Reb/36"),
    Feature("assists_per36", "Ast/36"),
    Feature("steals_per36", "Stl/36"),
    Feature("blocks_per36", "Blk/36"),
    Feature("turnovers_per36", "TOV/36"),
    Feature("three_point_rate", "3PA/FGA"),
    Feature("free_throw_rate", "FTA/FGA"),
    Feature("true_shooting_percentage", "TS%"),
)
FEATURE_LABELS: Final = {feature.key: feature.label for feature in FEATURES}

if len(FEATURES) != FINGERPRINT_DIMENSIONS:  # pragma: no cover — защита от рассинхрона
    raise RuntimeError("FEATURES разошлись с FINGERPRINT_DIMENSIONS: нужна миграция")


@dataclass(frozen=True, slots=True)
class SeasonLine:
    """Сезонные суммы Квалифицированного игрока; `None` — метрика в сезоне не считалась."""

    player_id: int
    season: str
    minutes: float | None
    points: float
    rebounds: float
    assists: float
    steals: float | None
    blocks: float | None
    turnovers: float | None
    field_goals_attempted: float
    three_pointers_attempted: float | None
    free_throws_attempted: float


@dataclass(frozen=True, slots=True)
class FingerprintRow:
    player_id: int
    season: str
    z_scores: dict[str, float]

    @property
    def embedding(self) -> list[float]:
        return [self.z_scores[feature.key] for feature in FEATURES]


def raw_features(line: SeasonLine) -> dict[str, float] | None:
    """Сырые признаки сезона; `None` — отпечатка нет.

    Отпечаток требует всех признаков и минут: сезон, где хоть что-то не считалось или нет
    знаменателя (ни одного броска), нельзя честно сравнивать с остальными — нули вместо
    «не считалось» увели бы его вектор к лиге-в-среднем и сделали «похожим» на всех.
    """
    minutes = line.minutes
    fga = line.field_goals_attempted
    if (
        minutes is None
        or line.steals is None
        or line.blocks is None
        or line.turnovers is None
        or line.three_pointers_attempted is None
        or minutes <= 0
        or fga <= 0
    ):
        return None
    free_throws = line.free_throws_attempted
    shooting_possessions = 2 * (fga + float(FREE_THROW_POSSESSION_WEIGHT) * free_throws)

    def per36(total: float) -> float:
        return total / minutes * _PER_MINUTES

    return {
        "points_per36": per36(line.points),
        "rebounds_per36": per36(line.rebounds),
        "assists_per36": per36(line.assists),
        "steals_per36": per36(line.steals),
        "blocks_per36": per36(line.blocks),
        "turnovers_per36": per36(line.turnovers),
        "three_point_rate": line.three_pointers_attempted / fga,
        "free_throw_rate": free_throws / fga,
        "true_shooting_percentage": line.points / shooting_possessions,
    }


def build_fingerprints(lines: Iterable[SeasonLine]) -> list[FingerprintRow]:
    """z-score признаков внутри сезона по выборке строк, у которых признаки есть.

    Чистая функция: вход уже отфильтрован до Квалифицированных игроков (это делает SQL), а
    нормализация и отбор по признакам живут здесь, чтобы тестироваться без базы. Порядок
    результата детерминирован — пересборка по тем же данным даёт то же содержимое.
    """
    by_season: dict[str, list[tuple[int, dict[str, float]]]] = {}
    for line in lines:
        features = raw_features(line)
        if features is not None:
            by_season.setdefault(line.season, []).append((line.player_id, features))

    rows: list[FingerprintRow] = []
    for season in sorted(by_season):
        sample = sorted(by_season[season], key=lambda item: item[0])
        stats = {
            feature.key: _mean_and_deviation([item[1][feature.key] for item in sample])
            for feature in FEATURES
        }
        for player_id, features in sample:
            z_scores = {
                key: _z(features[key], *stats[key]) for key in (feature.key for feature in FEATURES)
            }
            # Нулевой вектор не имеет направления: косинус до него не определён.
            if all(value == 0 for value in z_scores.values()):
                continue
            rows.append(FingerprintRow(player_id=player_id, season=season, z_scores=z_scores))
    return rows


def _mean_and_deviation(values: Sequence[float]) -> tuple[float, float]:
    return fmean(values), pstdev(values)


def _z(value: float, mean: float, deviation: float) -> float:
    # Признак, одинаковый у всей выборки сезона, ничего не различает: z = 0, а не деление на 0.
    if deviation < 1e-12:
        return 0.0
    return round_half_up((value - mean) / deviation, _Z_PLACES) + 0.0  # + 0.0: «-0.0» → 0.0


async def rebuild_fingerprints(session: AsyncSession) -> int:
    """Полная замена таблицы отпечатков; возвращает число строк.

    Удаление и вставка идут в транзакции вызывающего: читатель видит либо старую таблицу,
    либо новую, но не пустую посередине.
    """
    lines = await load_season_lines(session)
    rows = build_fingerprints(lines)
    await session.execute(delete(PlayerFingerprint))
    for start in range(0, len(rows), _BATCH_SIZE):
        await session.execute(
            insert(PlayerFingerprint),
            [
                {
                    "player_id": row.player_id,
                    "season": row.season,
                    "embedding": row.embedding,
                    "z_scores": row.z_scores,
                }
                for row in rows[start : start + _BATCH_SIZE]
            ],
        )
    log.info("fingerprints.rebuilt", qualified_lines=len(lines), fingerprints=len(rows))
    return len(rows)


async def load_season_lines(session: AsyncSession) -> list[SeasonLine]:
    result = await session.execute(_season_lines_statement())
    return [
        SeasonLine(
            player_id=row.player_id,
            season=row.season,
            minutes=_number(row.minutes),
            points=float(row.points),
            rebounds=float(row.rebounds),
            assists=float(row.assists),
            steals=_number(row.steals),
            blocks=_number(row.blocks),
            turnovers=_number(row.turnovers),
            field_goals_attempted=float(row.fga),
            three_pointers_attempted=_number(row.tpa),
            free_throws_attempted=float(row.fta),
        )
        for row in result
    ]


def _number(value: int | Decimal | None) -> float | None:
    return None if value is None else float(value)


def _season_lines_statement() -> Select[Any]:
    """Квалифицированные игрок-сезоны всех сезонов одним запросом.

    Тот же отбор матчей (`counted_games`) и то же определение Квалифицированного игрока, что
    у Перцентилей (`percentiles.py`), плюс требование минут: без них per-36 не считается.
    """

    def complete_sum(column: Any) -> Any:
        # Сумма, только если метрика есть в каждой строке: частично заполненный сезон дал
        # бы заниженный тотал вместо «не считалась».
        return case((func.count(column) == func.count(), func.sum(column)), else_=None)

    totals = (
        select(
            BoxScore.player_id,
            BoxScore.season,
            func.count().label("gp"),
            complete_sum(BoxScore.minutes).label("minutes"),
            func.sum(BoxScore.points).label("points"),
            func.sum(BoxScore.rebounds_total).label("rebounds"),
            func.sum(BoxScore.assists).label("assists"),
            complete_sum(BoxScore.steals).label("steals"),
            complete_sum(BoxScore.blocks).label("blocks"),
            complete_sum(BoxScore.turnovers).label("turnovers"),
            func.sum(BoxScore.field_goals_attempted).label("fga"),
            complete_sum(BoxScore.three_pointers_attempted).label("tpa"),
            func.sum(BoxScore.free_throws_attempted).label("fta"),
        )
        .where(counted_games())
        .group_by(BoxScore.player_id, BoxScore.season)
        .cte("totals")
    )
    t = totals.c

    # Матчей в сезоне — по команде с наибольшим числом матчей (как в Перцентилях).
    team_games = (
        select(Game.season, Game.home_team_id.label("team_id"))
        .where(Game.game_type == REGULAR_SEASON)
        .union_all(select(Game.season, Game.away_team_id).where(Game.game_type == REGULAR_SEASON))
        .subquery()
    )
    per_team = (
        select(team_games.c.season, func.count().label("n"))
        .group_by(team_games.c.season, team_games.c.team_id)
        .subquery()
    )
    season_games = (
        select(per_team.c.season, func.max(per_team.c.n).label("games"))
        .group_by(per_team.c.season)
        .subquery()
    )

    return (
        select(
            t.player_id,
            t.season,
            t.minutes,
            t.points,
            t.rebounds,
            t.assists,
            t.steals,
            t.blocks,
            t.turnovers,
            t.fga,
            t.tpa,
            t.fta,
        )
        .join(season_games, season_games.c.season == t.season)
        .where(
            t.gp >= season_games.c.games * literal(MIN_GAMES_SHARE),
            t.minutes.is_not(None),
            t.minutes >= MIN_MINUTES_PER_GAME * t.gp,
        )
        .order_by(t.season, t.player_id)
    )
