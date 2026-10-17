"""Перцентили игрока в сезоне — на синтетическом сезоне, где всё известно заранее.

Ожидания считаются здесь же, на чистом Python по исходным строкам, а не берутся из кода под
тестом: SQL с `percent_rank` и набор из десяти строк на игрока должны сойтись независимо.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.agent.evidence import BarsView
from court_vision.agent.loop import ToolContext
from court_vision.agent.tools import evidence_for, execute_tool
from court_vision.players.errors import SeasonDataMissing
from court_vision.players.models import REGULAR_SEASON, BoxScore, Game, Player, Team
from court_vision.players.percentiles import METRICS, get_player_percentiles

pytestmark = pytest.mark.usefixtures("clean_database")

SEASON = "2015-16"
OLD_SEASON = "1950-51"  # до минут, перехватов, блоков, потерь и трёхочковых
SEASON_GAMES = 10  # у обеих команд синтетического сезона


@dataclass(frozen=True)
class Line:
    """Статистика игрока за один матч; в синтетическом сезоне она одинакова во всех его матчах."""

    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    minutes: float
    fgm: int
    fga: int
    tpm: int
    tpa: int
    ftm: int
    fta: int


@dataclass(frozen=True)
class Synthetic:
    player_id: int
    name: str
    games: int
    line: Line
    dnd_games: int = 0  # матчи в заявке без выхода на площадку — минуты NULL


# Игроки подобраны под граничные случаи: у 102 и 103 ничья по очкам, у 101 и 103 — по
# потерям, 104 ровно на границе (5 из 10 матчей, 15.0 минут), 105 — звезда, не набравшая
# матчей, 106 — набравший матчи, но не минуты, 102 не бросал трёхочковые вовсе.
PLAYERS = (
    Synthetic(101, "Quentin Zorbax", 10, Line(20, 5, 5, 1, 1, 3, 30, 8, 16, 2, 5, 2, 2)),
    Synthetic(102, "Marvin Telgast", 10, Line(10, 8, 2, 1, 2, 1, 32, 4, 10, 0, 0, 2, 4)),
    Synthetic(103, "Osmund Draveck", 8, Line(10, 3, 7, 2, 0, 3, 20, 4, 8, 1, 2, 4, 4)),
    Synthetic(104, "Pavel Nutterby", 5, Line(5, 2, 1, 0, 0, 2, 15, 2, 6, 1, 3, 1, 2)),
    Synthetic(105, "Ignatz Brollo", 4, Line(30, 9, 9, 3, 3, 5, 36, 12, 20, 4, 8, 2, 3)),
    Synthetic(106, "Lucan Fenwrick", 10, Line(4, 1, 1, 0, 0, 0, 10, 2, 5, 0, 2, 0, 0)),
    Synthetic(107, "Hector Vandross", 9, Line(15, 6, 3, 1, 1, 2, 25, 6, 13, 2, 6, 1, 2), 1),
)
QUALIFIED = {101, 102, 103, 104, 107}
BY_ID = {player.player_id: player for player in PLAYERS}


async def _insert_season(
    session: AsyncSession, season: str, players: tuple[Synthetic, ...]
) -> None:
    for team_id in (1, 2):
        if await session.get(Team, team_id) is None:
            session.add(Team(id=team_id, city="City", name=f"Team {team_id}", abbreviation="TM"))
    for player in players:
        if await session.get(Player, player.player_id) is None:
            first, last = player.name.split()
            session.add(
                Player(id=player.player_id, first_name=first, last_name=last, full_name=player.name)
            )
    await session.flush()

    base = 1000 if season == SEASON else 2000
    for number in range(SEASON_GAMES):
        session.add(
            Game(
                id=base + number,
                season=season,
                game_date=date(2016, 1, 1 + number),
                game_type=REGULAR_SEASON,
                home_team_id=1,
                away_team_id=2,
            )
        )
    await session.flush()

    for player in players:
        for number in range(player.games + player.dnd_games):
            dnd = number >= player.games
            session.add(_box_score(base + number, season, player, dnd=dnd))
    await session.flush()


def _box_score(game_id: int, season: str, player: Synthetic, *, dnd: bool) -> BoxScore:
    line = player.line
    # Сезон до введения метрик: эра-зависимые поля NULL, а не нули.
    modern = season >= "1979-80"
    return BoxScore(
        game_id=game_id,
        player_id=player.player_id,
        season=season,
        game_date=date(2016, 1, 1),
        game_type=REGULAR_SEASON,
        team_id=1,
        opponent_team_id=2,
        is_home=True,
        is_win=True,
        minutes=None if dnd or season < "1951-52" else line.minutes,
        points=0 if dnd else line.points,
        assists=0 if dnd else line.assists,
        rebounds_total=0 if dnd else line.rebounds,
        steals=None if not modern else (0 if dnd else line.steals),
        blocks=None if not modern else (0 if dnd else line.blocks),
        turnovers=None if not modern else (0 if dnd else line.turnovers),
        fouls_personal=0,
        field_goals_made=0 if dnd else line.fgm,
        field_goals_attempted=0 if dnd else line.fga,
        three_pointers_made=None if not modern else (0 if dnd else line.tpm),
        three_pointers_attempted=None if not modern else (0 if dnd else line.tpa),
        free_throws_made=0 if dnd else line.ftm,
        free_throws_attempted=0 if dnd else line.fta,
    )


def _expected_values(player: Synthetic) -> dict[str, float | None]:
    """Показатели игрока за игру — независимо от SQL: исходные строки, деление в Python."""
    line = player.line
    return {
        "points": line.points,
        "rebounds": line.rebounds,
        "assists": line.assists,
        "steals": line.steals,
        "blocks": line.blocks,
        "turnovers": line.turnovers,
        "minutes": line.minutes,
        "field_goal_percentage": line.fgm / line.fga if line.fga else None,
        "three_point_percentage": line.tpm / line.tpa if line.tpa else None,
        "free_throw_percentage": line.ftm / line.fta if line.fta else None,
        "true_shooting_percentage": line.points / (2 * (line.fga + 0.44 * line.fta)),
    }


def _expected_percentile(metric: str, subject: Synthetic) -> int | None:
    """Доля Квалифицированных игроков хуже субъекта; сам субъект из знаменателя исключён,
    если он в выборке. Метрика None у субъекта — Перцентиля нет, у соперника — он вне выборки."""
    mine = _expected_values(subject)[metric]
    if mine is None:
        return None
    lower_is_better = metric == "turnovers"
    sample = [
        value
        for player_id in QUALIFIED
        if player_id != subject.player_id
        and (value := _expected_values(BY_ID[player_id])[metric]) is not None
    ]
    worse = sum(1 for value in sample if (value > mine if lower_is_better else value < mine))
    # Половина вверх, целыми числами: без float-ничьих на границе округления.
    return (200 * worse + len(sample)) // (2 * len(sample))


@pytest.mark.parametrize("subject", PLAYERS, ids=lambda player: str(player.player_id))
async def test_перцентили_совпадают_с_независимым_расчётом(
    session: AsyncSession, subject: Synthetic
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await get_player_percentiles(session, player_name=subject.name, season=SEASON)

    assert [metric.metric for metric in result.metrics] == [m.key for m in METRICS]
    expected_values = _expected_values(subject)
    for metric in result.metrics:
        assert metric.percentile == _expected_percentile(metric.metric, subject), metric.metric
        want = expected_values[metric.metric]
        if want is None:
            assert metric.value is None
        else:
            assert metric.value == pytest.approx(want, abs=0.001 if want < 1 else 0.05)


async def test_квалификация_и_размер_выборки(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    flags = {
        player.player_id: await get_player_percentiles(
            session, player_name=player.name, season=SEASON
        )
        for player in PLAYERS
    }

    # 104 ровно на границе (5 из 10 матчей, 15.0 минут) — квалифицирован; 105 не добрал
    # матчей, 106 — минут; 107 с DND в заявке: его девять выходов, а не десять строк.
    assert {pid for pid, r in flags.items() if r.qualified} == QUALIFIED
    assert flags[107].games_played == 9
    assert all(result.sample_size == len(QUALIFIED) for result in flags.values())


async def test_неквалифицированный_игрок_получает_перцентили_относительно_выборки(
    session: AsyncSession,
) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await get_player_percentiles(session, player_name="Ignatz Brollo", season=SEASON)

    assert result.qualified is False
    points = next(metric for metric in result.metrics if metric.metric == "points")
    # 30 очков выше всех Квалифицированных; сам он в выборку не входит.
    assert points.percentile == 100


async def test_потери_инвертированы(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await get_player_percentiles(session, player_name="Marvin Telgast", season=SEASON)

    turnovers = next(metric for metric in result.metrics if metric.metric == "turnovers")
    # У 102 меньше всех потерь (1 за игру) — лучший Перцентиль, а не худший.
    assert turnovers.value == 1.0
    assert turnovers.lower_is_better is True
    assert turnovers.percentile == 100


async def test_ничьи_дают_одинаковый_перцентиль(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    tied = [
        await get_player_percentiles(session, player_name=name, season=SEASON)
        for name in ("Marvin Telgast", "Osmund Draveck")
    ]

    points = [next(m for m in r.metrics if m.metric == "points").percentile for r in tied]
    assert points[0] == points[1]


async def test_не_считавшаяся_метрика_без_значения_и_перцентиля(session: AsyncSession) -> None:
    old_players = tuple(player for player in PLAYERS if player.player_id in (101, 102, 103))
    await _insert_season(session, OLD_SEASON, old_players)

    result = await get_player_percentiles(session, player_name="Quentin Zorbax", season=OLD_SEASON)

    by_metric = {metric.metric: metric for metric in result.metrics}
    for untracked in ("minutes", "steals", "blocks", "turnovers", "three_point_percentage"):
        assert by_metric[untracked].value is None, untracked
        assert by_metric[untracked].percentile is None, untracked
    # Очки считались всегда: у Квалифицированных по матчам (минут нет — правило минут не
    # применяется) они есть, и Перцентиль на месте.
    assert by_metric["points"].percentile == 100
    assert result.qualified is True


async def test_нулевые_минуты_это_неотслеженные_а_не_ноль(session: AsyncSession) -> None:
    season = "1955-56"
    star = Synthetic(201, "Rolf Pettwick", 10, Line(25, 12, 3, 0, 0, 0, 36, 9, 20, 0, 0, 7, 9))
    untracked = Synthetic(202, "Gunnar Oakley", 10, Line(8, 4, 2, 0, 0, 0, 20, 3, 9, 0, 0, 2, 3))
    peers = (
        Synthetic(203, "Edric Vosk", 10, Line(12, 6, 2, 0, 0, 0, 30, 5, 12, 0, 0, 2, 3)),
        Synthetic(204, "Silas Marrow", 10, Line(14, 5, 4, 0, 0, 0, 32, 6, 13, 0, 0, 2, 2)),
    )
    await _insert_season(session, season, (star, untracked, *peers))
    # У star минуты проставлены в 4 строках из 10 (среднее по ним 36), у untracked — ни в одной.
    for player_id, tracked in ((201, 4), (202, 0)):
        games = (
            await session.scalars(
                select(BoxScore.game_id)
                .where(BoxScore.player_id == player_id)
                .order_by(BoxScore.game_id)
            )
        ).all()
        await session.execute(
            update(BoxScore)
            .where(BoxScore.player_id == player_id, BoxScore.game_id.in_(games[tracked:]))
            .values(minutes=0)
        )
    await session.flush()

    result = await get_player_percentiles(session, player_name="Rolf Pettwick", season=season)
    minutes = next(m for m in result.metrics if m.metric == "minutes")
    # Сумма минут по 10 строкам дала бы 14.4 < 15 и сняла квалификацию; среднее по
    # отслеженным — 36.
    assert result.qualified is True
    assert result.sample_size == 4
    assert minutes.value == 36

    absent = await get_player_percentiles(session, player_name="Gunnar Oakley", season=season)
    absent_minutes = next(m for m in absent.metrics if m.metric == "minutes")
    # Минут нет вовсе: правило минут не применяется, метрика без значения и Перцентиля.
    assert absent.qualified is True
    assert absent_minutes.value is None
    assert absent_minutes.percentile is None


async def test_бросавший_ноль_раз_без_процента(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await get_player_percentiles(session, player_name="Marvin Telgast", season=SEASON)

    three = next(m for m in result.metrics if m.metric == "three_point_percentage")
    # 0 попыток — это «нечего показать», а не 0% и не худший Перцентиль.
    assert three.value is None
    assert three.percentile is None


async def test_без_сезона_берётся_последний_сезон_игрока(session: AsyncSession) -> None:
    await _insert_season(session, OLD_SEASON, PLAYERS[:3])
    await _insert_season(session, SEASON, PLAYERS)

    result = await get_player_percentiles(session, player_name="Quentin Zorbax")

    assert result.season == SEASON


async def test_нет_матчей_в_сезоне_это_доменная_ошибка(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    with pytest.raises(SeasonDataMissing):
        await get_player_percentiles(session, player_name="Quentin Zorbax", season="2001-02")


async def test_инструмент_отдаёт_полосы_с_прочерком_для_не_считавшейся_метрики(
    session: AsyncSession,
) -> None:
    await _insert_season(session, SEASON, PLAYERS)
    ctx = cast(ToolContext, None)  # Инструмент контекст хода не использует

    result = await execute_tool(
        session,
        name="get_player_percentiles",
        arguments={"player_name": "Marvin Telgast", "season": SEASON},
        ctx=ctx,
    )
    evidence = evidence_for(name="get_player_percentiles", result=result)

    assert "error" not in result
    json.dumps(result)  # результат обязан быть JSON-сериализуем: он уходит модели
    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, BarsView)
    by_label: dict[str, Any] = {bar.label: bar.percentile for bar in view.bars}
    assert by_label["3P%"] is None
    assert by_label["TOV (inv.)"] == 100
    assert len(view.bars) == len(METRICS)
    assert view.caption is not None
    assert SEASON in view.caption
    assert str(len(QUALIFIED)) in view.caption


async def test_инструмент_помечает_неквалифицированного_в_подписи(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await execute_tool(
        session,
        name="get_player_percentiles",
        arguments={"player_name": "Ignatz Brollo"},
        ctx=cast(ToolContext, None),
    )
    evidence = evidence_for(name="get_player_percentiles", result=result)

    assert result["qualified"] is False
    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, BarsView)
    assert view.caption is not None
    assert "not qualified" in view.caption.lower()


async def test_инструмент_возвращает_отказ_а_не_исключение(session: AsyncSession) -> None:
    await _insert_season(session, SEASON, PLAYERS)

    result = await execute_tool(
        session,
        name="get_player_percentiles",
        arguments={"player_name": "Quentin Zorbax", "season": "2001-02"},
        ctx=cast(ToolContext, None),
    )

    assert result["error"]["kind"] == "season-data-missing"
