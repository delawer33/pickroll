"""Золотые пары Похожих игроков: загрузка набора и механика проверки."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.answer_eval.similar_pairs import (
    GoldenPair,
    GoldenPairs,
    check_pair,
    format_results,
    load_pairs,
)
from court_vision.players.fingerprints import rebuild_fingerprints
from tests.test_similar_players import CAST, _insert_season


def test_набор_золотых_пар_валиден_и_содержит_оба_вида() -> None:
    golden = load_pairs()

    assert len(golden.pairs) >= 8
    assert {pair.expect for pair in golden.pairs} == {"similar", "dissimilar"}
    assert golden.dissimilar_max < golden.similar_min


def test_пороги_не_должны_пересекаться() -> None:
    with pytest.raises(ValueError, match="dissimilar_max"):
        GoldenPairs(similar_min=0.5, dissimilar_max=0.5, pairs=(_pair("similar"),))


def _pair(expect: str, other: str = "Lucan Fenwrick") -> GoldenPair:
    return GoldenPair.model_validate(
        {
            "player_a": "Quentin Zorbax",
            "season_a": "2015-16",
            "player_b": other,
            "season_b": "2015-16",
            "expect": expect,
            "why": "тест",
        }
    )


@pytest.mark.usefixtures("clean_database")
async def test_проверка_пары_сверяет_близость_с_порогом(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    await rebuild_fingerprints(session)
    golden = GoldenPairs(similar_min=0.6, dissimilar_max=0.2, pairs=(_pair("similar"),))

    close = await check_pair(session, _pair("similar", "Lucan Fenwrick"), golden)
    far = await check_pair(session, _pair("dissimilar", "Marvin Telgast"), golden)
    wrong = await check_pair(session, _pair("dissimilar", "Lucan Fenwrick"), golden)

    assert close.passed
    assert far.passed
    assert not wrong.passed
    assert "FAIL" in format_results([wrong])


@pytest.mark.usefixtures("clean_database")
async def test_пара_без_отпечатка_провалена_а_не_пропущена(session: AsyncSession) -> None:
    await _insert_season(session, "2015-16", CAST)
    golden = GoldenPairs(similar_min=0.6, dissimilar_max=0.2, pairs=(_pair("similar"),))

    # Отпечатки не пересобраны: в базе их нет вовсе.
    result = await check_pair(session, _pair("similar"), golden)

    assert not result.passed
    assert result.similarity is None
