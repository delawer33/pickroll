"""Статистический отпечаток и Похожие игроки — на синтетических сезонах, где ответ известен.

Каст сезона 2015-16 — шесть Квалифицированных игроков разных амплуа и один, не набравший
матчей. Сезон 1985-86 — тот же каст другими людьми, у которых все объёмы вдвое выше: сырые
цифры другие, относительно лиги профили те же.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.agent.evidence import SimilarView
from court_vision.agent.loop import ToolContext
from court_vision.agent.tools import evidence_for, execute_tool
from court_vision.players.errors import FingerprintMissing
from court_vision.players.fingerprints import FEATURES, rebuild_fingerprints
from court_vision.players.models import (
    REGULAR_SEASON,
    BoxScore,
    Game,
    Player,
    PlayerFingerprint,
    Team,
)
from court_vision.players.similar import explain, find_similar_players, pair_similarity

pytestmark = pytest.mark.usefixtures("clean_database")

SEASON_GAMES = 10


@dataclass(frozen=True)
class Line:
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    minutes: float
    fga: int
    tpa: int
    fta: int

    def scaled(self, factor: int) -> Line:
        return Line(
            self.points * factor,
            self.rebounds * factor,
            self.assists * factor,
            self.steals * factor,
            self.blocks * factor,
            self.turnovers * factor,
            self.minutes,
            self.fga * factor,
            self.tpa * factor,
            self.fta * factor,
        )


SCORER = Line(28, 4, 3, 1, 0, 3, 34, 22, 10, 8)
BIG = Line(14, 11, 2, 1, 3, 2, 30, 10, 0, 5)
GUARD = Line(12, 3, 9, 2, 0, 3, 32, 10, 5, 3)
WING = Line(16, 6, 4, 1, 1, 2, 33, 13, 6, 4)
ROLE = Line(8, 4, 2, 1, 0, 1, 20, 7, 4, 1)
SCORER_TWO = Line(26, 5, 4, 1, 0, 3, 35, 20, 9, 7)

# (id, имя, линия) каста; id + 100 — тот же амплуа в другой эпохе.
CAST = (
    (101, "Quentin Zorbax", SCORER),
    (102, "Marvin Telgast", BIG),
    (103, "Osmund Draveck", GUARD),
    (104, "Pavel Nutterby", WING),
    (105, "Ignatz Brollo", ROLE),
    (106, "Lucan Fenwrick", SCORER_TWO),
)
OLD_CAST = (
    (201, "Wendell Quarrick", SCORER),
    (202, "Basil Corvane", BIG),
    (203, "Dmitri Hallowell", GUARD),
    (204, "Eamon Tressler", WING),
    (205, "Fergus Ollivant", ROLE),
    (206, "Gideon Marlowe", SCORER_TWO),
)


async def _insert_season(
    session: AsyncSession,
    season: str,
    cast: tuple[tuple[int, str, Line], ...],
    *,
    games: int = SEASON_GAMES,
    only_games: dict[int, int] | None = None,
) -> None:
    """Сезон на десять матчей двух команд; `only_games` — сколько матчей сыграл игрок."""
    for team_id in (1, 2):
        if await session.get(Team, team_id) is None:
            session.add(Team(id=team_id, city="City", name=f"Team {team_id}", abbreviation="TM"))
    for player_id, name, _ in cast:
        if await session.get(Player, player_id) is None:
            first, last = name.split()
            session.add(Player(id=player_id, first_name=first, last_name=last, full_name=name))
    await session.flush()

    base = int(season[:4]) * 100
    for number in range(games):
        session.add(
            Game(
                id=base + number,
                season=season,
                game_date=date(int(season[:4]) + 1, 1, 1 + number),
                game_type=REGULAR_SEASON,
                home_team_id=1,
                away_team_id=2,
            )
        )
    await session.flush()

    modern = season >= "1979-80"
    for player_id, _, line in cast:
        for number in range((only_games or {}).get(player_id, games)):
            session.add(
                BoxScore(
                    game_id=base + number,
                    player_id=player_id,
                    season=season,
                    game_date=date(int(season[:4]) + 1, 1, 1),
                    game_type=REGULAR_SEASON,
                    team_id=1,
                    opponent_team_id=2,
                    is_home=True,
                    is_win=True,
                    minutes=line.minutes,
                    points=line.points,
                    assists=line.assists,
                    rebounds_total=line.rebounds,
                    steals=line.steals if modern else None,
                    blocks=line.blocks if modern else None,
                    turnovers=line.turnovers if modern else None,
                    fouls_personal=0,
                    field_goals_made=line.fga // 2,
                    field_goals_attempted=line.fga,
                    three_pointers_made=line.tpa // 3 if modern else None,
                    three_pointers_attempted=line.tpa if modern else None,
                    free_throws_made=line.fta // 2,
                    free_throws_attempted=line.fta,
                )
            )
    await session.flush()


async def _fingerprint_keys(session: AsyncSession) -> set[tuple[int, str]]:
    result = await session.execute(select(PlayerFingerprint.player_id, PlayerFingerprint.season))
    return {(row.player_id, row.season) for row in result}


async def test_отпечатки_только_у_квалифицированных_сезонов_с_полными_метриками(
    session: AsyncSession,
) -> None:
    unqualified = (107, "Hector Vandross", SCORER)
    await _insert_season(session, "2015-16", (*CAST, unqualified), only_games={107: 3})
    # 1975-76: минуты считались, а перехваты, блоки, потери и трёхочковые — ещё нет.
    await _insert_season(session, "1975-76", CAST)

    count = await rebuild_fingerprints(session)

    keys = await _fingerprint_keys(session)
    assert count == len(keys) == len(CAST)
    assert keys == {(player_id, "2015-16") for player_id, _, _ in CAST}


async def test_сезон_до_минут_отпечатка_не_имеет(session: AsyncSession) -> None:
    await _insert_season(session, "1950-51", CAST)
    await session.execute(text("UPDATE box_scores SET minutes = NULL"))

    assert await rebuild_fingerprints(session) == 0


async def test_пересборка_идемпотентна(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)

    async def snapshot() -> list[tuple[int, str, list[float], dict[str, float]]]:
        result = await session.execute(
            select(
                PlayerFingerprint.player_id,
                PlayerFingerprint.season,
                PlayerFingerprint.embedding,
                PlayerFingerprint.z_scores,
            ).order_by(PlayerFingerprint.player_id, PlayerFingerprint.season)
        )
        return [(row[0], row[1], [float(v) for v in row[2]], row[3]) for row in result]

    first_count = await rebuild_fingerprints(session)
    first = await snapshot()
    second_count = await rebuild_fingerprints(session)

    assert first_count == second_count == len(CAST)
    assert await snapshot() == first


async def test_пересборка_убирает_устаревшие_отпечатки(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)
    await session.execute(text("DELETE FROM box_scores WHERE player_id = 101"))

    await rebuild_fingerprints(session)

    assert 101 not in {player_id for player_id, _ in await _fingerprint_keys(session)}


async def test_hnsw_индекс_по_косинусу_создан_миграцией(session: AsyncSession) -> None:
    definition = await session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_player_fingerprints_embedding_hnsw'"
        )
    )

    assert definition is not None
    assert "hnsw" in definition
    assert "vector_cosine_ops" in definition


async def test_одинаковые_относительные_профили_в_разных_эпохах_почти_совпадают(
    session: AsyncSession,
) -> None:
    await _insert_season(session, "2015-16", CAST)
    doubled = tuple((pid, name, line.scaled(2)) for pid, name, line in OLD_CAST)
    await _insert_season(session, "1985-86", doubled)
    await rebuild_fingerprints(session)

    result = await find_similar_players(session, player_name="Quentin Zorbax", season="2015-16")

    best = result.similar_players[0]
    assert best.player.full_name == "Wendell Quarrick"
    assert best.season == "1985-86"
    assert best.similarity == pytest.approx(1.0, abs=1e-3)


async def test_самоисключение_и_дедуп_по_игроку(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    # У Zorbax второй сезон, в котором он играет точно как Fenwrick в первом.
    await _insert_season(
        session, "2016-17", tuple((pid, n, SCORER_TWO if pid == 101 else ln) for pid, n, ln in CAST)
    )
    await rebuild_fingerprints(session)

    result = await find_similar_players(
        session, player_name="Lucan Fenwrick", season="2015-16", limit=10
    )

    names = [item.player.full_name for item in result.similar_players]
    assert "Lucan Fenwrick" not in names
    assert len(names) == len(set(names)) == len(CAST) - 1
    zorbax = next(item for item in result.similar_players if item.player.player_id == 101)
    # Из двух сезонов Zorbax берётся ближайший, и он первым в списке.
    assert zorbax.season == "2016-17"
    assert result.similar_players[0].player.player_id == 101
    similarities = [item.similarity for item in result.similar_players]
    assert similarities == sorted(similarities, reverse=True)


async def test_без_сезона_берётся_последний_сезон_с_отпечатком(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    await _insert_season(session, "2016-17", CAST)
    await _insert_season(session, "2017-18", CAST, only_games={101: 2})  # не квалифицирован
    await rebuild_fingerprints(session)

    result = await find_similar_players(session, player_name="Quentin Zorbax")

    assert result.season == "2016-17"


async def test_объяснение_сходства_называет_ближайшие_и_дальнейшие_признаки(
    session: AsyncSession,
) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)

    result = await find_similar_players(session, player_name="Quentin Zorbax", season="2015-16")

    assert [item.metric for item in result.target_profile] == [f.key for f in FEATURES]
    target = {item.metric: item.z for item in result.target_profile}
    for item in result.similar_players:
        assert len(item.matching) == len(item.diverging) == 3
        keys = [feature.metric for feature in (*item.matching, *item.diverging)]
        assert len(set(keys)) == 6  # ни один признак не назван и сходством, и различием
        for feature in (*item.matching, *item.diverging):
            assert feature.label
            # z искомого игрока в объяснении — тот самый, что в его профиле.
            assert feature.target_z == target[feature.metric]


def test_объяснение_ставит_в_сходство_общие_отклонения_а_в_различие_наибольшие_разрывы() -> None:
    keys = [feature.key for feature in FEATURES]
    # Оба заметно выше лиги по очкам и передачам; блоки — оба на уровне лиги (ничего не
    # говорят); по подборам и штрафным — противоположные стороны.
    target = dict.fromkeys(keys, 0.0) | {"points_per36": 2.0, "assists_per36": 1.5}
    other = dict.fromkeys(keys, 0.0) | {
        "points_per36": 1.8,
        "assists_per36": 1.6,
        "rebounds_per36": -2.0,
        "free_throw_rate": 1.5,
        "steals_per36": 0.4,
    }

    matching, diverging = explain(target, other)

    assert matching[:2] == ["points_per36", "assists_per36"]
    assert diverging[:2] == ["rebounds_per36", "free_throw_rate"]
    assert not set(matching) & set(diverging)


async def test_лимит_ограничивает_выдачу(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)

    result = await find_similar_players(
        session, player_name="Quentin Zorbax", season="2015-16", limit=2
    )

    assert len(result.similar_players) == 2


async def test_сезон_без_отпечатка_это_явная_ошибка_с_доступными_сезонами(
    session: AsyncSession,
) -> None:
    await _insert_season(session, "2015-16", CAST)
    await _insert_season(session, "1975-76", CAST)
    await rebuild_fingerprints(session)

    with pytest.raises(FingerprintMissing) as raised:
        await find_similar_players(session, player_name="Quentin Zorbax", season="1975-76")

    assert raised.value.extensions()["available_seasons"] == ["2015-16"]
    assert raised.value.extensions()["season"] == "1975-76"


async def test_игрок_без_единого_отпечатка(session: AsyncSession) -> None:
    await _insert_season(session, "1975-76", CAST)
    await rebuild_fingerprints(session)

    with pytest.raises(FingerprintMissing) as raised:
        await find_similar_players(session, player_name="Quentin Zorbax")

    assert raised.value.extensions()["available_seasons"] == []


async def test_близость_пары_игрок_сезонов(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)

    close = await pair_similarity(
        session,
        player_a="Quentin Zorbax",
        season_a="2015-16",
        player_b="Lucan Fenwrick",
        season_b="2015-16",
    )
    far = await pair_similarity(
        session,
        player_a="Quentin Zorbax",
        season_a="2015-16",
        player_b="Marvin Telgast",
        season_b="2015-16",
    )

    assert close > far
    assert close > 0.5


async def test_инструмент_отдаёт_список_и_карточку(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)

    result = await execute_tool(
        session,
        name="find_similar_players",
        arguments={"player_name": "Quentin Zorbax", "season": "2015-16", "limit": 3},
        ctx=cast(ToolContext, None),
    )
    evidence = evidence_for(name="find_similar_players", result=result)

    assert "error" not in result
    json.dumps(result)  # уходит модели
    assert len(result["similar_players"]) == 3
    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, SimilarView)
    assert len(view.items) == 3
    assert view.items[0].name == "Lucan Fenwrick"
    assert "2015-16" in view.caption


async def test_инструмент_на_сезоне_без_отпечатка_возвращает_структурную_ошибку(
    session: AsyncSession,
) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)

    result = await execute_tool(
        session,
        name="find_similar_players",
        arguments={"player_name": "Quentin Zorbax", "season": "1990-91"},
        ctx=cast(ToolContext, None),
    )
    evidence = evidence_for(name="find_similar_players", result=result)

    assert result["error"]["kind"] == "fingerprint-missing"
    assert result["error"]["available_seasons"] == ["2015-16"]
    assert evidence.failure is not None
    assert evidence.failure.kind == "fingerprint-missing"


async def test_инструмент_отвергает_лимит_вне_диапазона(session: AsyncSession) -> None:
    result = await execute_tool(
        session,
        name="find_similar_players",
        arguments={"player_name": "Quentin Zorbax", "limit": 50},
        ctx=cast(ToolContext, None),
    )

    assert result["error"]["kind"] == "invalid-arguments"
