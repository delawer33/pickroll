"""Нормализация Статистических отпечатков — чистая логика, без базы."""

from __future__ import annotations

from dataclasses import replace
from statistics import fmean, pstdev

import pytest

from court_vision.players.fingerprints import FEATURES, SeasonLine, build_fingerprints, raw_features


def _line(player_id: int, season: str, **overrides: float | None) -> SeasonLine:
    base = SeasonLine(
        player_id=player_id,
        season=season,
        minutes=300.0,
        points=100.0,
        rebounds=40.0,
        assists=30.0,
        steals=10.0,
        blocks=5.0,
        turnovers=20.0,
        field_goals_attempted=80.0,
        three_pointers_attempted=20.0,
        free_throws_attempted=25.0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _sample(season: str, first_id: int = 1) -> list[SeasonLine]:
    return [
        _line(first_id, season, points=200.0, three_pointers_attempted=40.0),
        _line(first_id + 1, season, rebounds=90.0, blocks=30.0, three_pointers_attempted=0.0),
        _line(first_id + 2, season, assists=80.0, free_throws_attempted=10.0),
        _line(first_id + 3, season, points=60.0, steals=25.0, turnovers=5.0),
    ]


def test_z_scores_внутри_сезона_имеют_нулевое_среднее_и_единичный_разброс() -> None:
    rows = build_fingerprints(_sample("2015-16"))

    assert len(rows) == 4
    for feature in FEATURES:
        values = [row.z_scores[feature.key] for row in rows]
        assert fmean(values) == pytest.approx(0, abs=1e-3)
        assert pstdev(values) == pytest.approx(1, abs=1e-3)


def test_одинаковые_относительные_профили_в_разных_эпохах_дают_один_вектор() -> None:
    modern = _sample("2015-16", first_id=1)
    # Эпоха «в два раза выше по всем объёмам»: счётные суммы удвоены, минуты те же. Per-36
    # удвоились, доли (3PA/FGA, FTA/FGA, TS%) не изменились — относительно лиги профили те же.
    old = [
        replace(
            line,
            player_id=line.player_id + 100,
            season="1985-86",
            points=line.points * 2,
            rebounds=line.rebounds * 2,
            assists=line.assists * 2,
            steals=(line.steals or 0) * 2,
            blocks=(line.blocks or 0) * 2,
            turnovers=(line.turnovers or 0) * 2,
            field_goals_attempted=line.field_goals_attempted * 2,
            three_pointers_attempted=(line.three_pointers_attempted or 0) * 2,
            free_throws_attempted=line.free_throws_attempted * 2,
        )
        for line in modern
    ]

    rows = {(row.player_id, row.season): row for row in build_fingerprints(modern + old)}

    for line in modern:
        now = rows[(line.player_id, "2015-16")]
        then = rows[(line.player_id + 100, "1985-86")]
        assert now.embedding == pytest.approx(then.embedding, abs=1e-3)


@pytest.mark.parametrize(
    "missing",
    [
        {"minutes": None},
        {"steals": None},
        {"blocks": None},
        {"turnovers": None},
        {"three_pointers_attempted": None},
        {"field_goals_attempted": 0.0},
    ],
    ids=lambda case: next(iter(case)),
)
def test_сезон_без_метрики_отпечатка_не_имеет(missing: dict[str, float | None]) -> None:
    broken = _line(99, "2015-16", **missing)

    assert raw_features(broken) is None
    rows = build_fingerprints([*_sample("2015-16"), broken])
    assert 99 not in {row.player_id for row in rows}
    # Выборка сезона от него не зависит: z остальных те же, что и без него.
    assert {row.player_id for row in rows} == {1, 2, 3, 4}


def test_признак_без_разброса_в_сезоне_даёт_нулевой_z() -> None:
    flat_blocks = [replace(line, blocks=5.0) for line in _sample("2015-16")]

    rows = build_fingerprints(flat_blocks)

    assert len(rows) == 4
    assert {row.z_scores["blocks_per36"] for row in rows} == {0.0}
    # А сезон из одинаковых игроков вектора не образует вовсе: нулевой вектор не имеет
    # направления, косинус до него не определён.
    twins = [_line(1, "2015-16"), _line(2, "2015-16")]
    assert build_fingerprints(twins) == []


def test_результат_детерминирован_и_не_зависит_от_порядка_входа() -> None:
    lines = _sample("2015-16") + _sample("1985-86", first_id=11)

    assert build_fingerprints(lines) == build_fingerprints(list(reversed(lines)))
