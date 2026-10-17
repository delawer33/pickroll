"""Золотые пары для Похожих игроков: детерминированная проверка без LLM.

Пара — два игрок-сезона, про которые баскетбольно известно, похожи они или нет («Гобер-2021 и
Мутомбо-1997 — похожи», «Гобер-2021 и Карри-2016 — нет»). Проверка считает косинусную близость
их Статистических отпечатков на живой базе и сверяет с порогами из файла. Изменение признаков
или нормализации отпечатка, после которого известные пары разъехались, ловится здесь, а не
жалобой скаута.

Бесплатно и быстро (никаких вызовов модели), поэтому отдельным режимом команды eval ответов:
`uv run python -m court_vision.answer_eval --similar-pairs`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.answer_eval.golden import EVALS_DIR
from court_vision.errors import DomainError
from court_vision.players.similar import pair_similarity

DEFAULT_PAIRS_PATH: Final = EVALS_DIR / "similar_pairs.yaml"


class GoldenPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    player_a: str = Field(min_length=1)
    season_a: str = Field(pattern=r"^\d{4}-\d{2}$")
    player_b: str = Field(min_length=1)
    season_b: str = Field(pattern=r"^\d{4}-\d{2}$")
    expect: Literal["similar", "dissimilar"]
    # Почему пара в наборе: пара без объяснения со временем превращается в «так исторически
    # сложилось», и порог под неё двигают, не понимая, что ломается.
    why: str = Field(min_length=1)


class GoldenPairs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Похожая пара обязана дать близость не ниже порога, непохожая — не выше своего.
    similar_min: float
    dissimilar_max: float
    pairs: tuple[GoldenPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _thresholds_do_not_overlap(self) -> GoldenPairs:
        if self.dissimilar_max >= self.similar_min:
            raise ValueError("dissimilar_max должен быть строго меньше similar_min")
        return self


@dataclass(frozen=True, slots=True)
class PairResult:
    pair: GoldenPair
    # None — близость посчитать не удалось (игрок не найден, у сезона нет отпечатка).
    similarity: float | None
    passed: bool
    detail: str


def load_pairs(path: Path = DEFAULT_PAIRS_PATH) -> GoldenPairs:
    return GoldenPairs.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


async def check_pair(session: AsyncSession, pair: GoldenPair, golden: GoldenPairs) -> PairResult:
    try:
        similarity = await pair_similarity(
            session,
            player_a=pair.player_a,
            season_a=pair.season_a,
            player_b=pair.player_b,
            season_b=pair.season_b,
        )
    except DomainError as error:
        # Пара, которую нельзя посчитать, провалена: молча пропущенная проверка выглядела бы
        # зелёной ровно тогда, когда отпечатков в базе нет.
        return PairResult(pair, None, passed=False, detail=error.detail)

    if pair.expect == "similar":
        passed = similarity >= golden.similar_min
        detail = f"нужно ≥ {golden.similar_min}"
    else:
        passed = similarity <= golden.dissimilar_max
        detail = f"нужно ≤ {golden.dissimilar_max}"
    return PairResult(pair, similarity, passed=passed, detail=detail)


async def check_pairs(
    session_factory: async_sessionmaker[AsyncSession], golden: GoldenPairs
) -> list[PairResult]:
    async with session_factory() as session:
        return [await check_pair(session, pair, golden) for pair in golden.pairs]


def format_results(results: list[PairResult]) -> str:
    """Таблица для stdout: результат и есть вывод команды."""
    lines = []
    for result in results:
        pair = result.pair
        mark = "ok  " if result.passed else "FAIL"
        value = "n/a" if result.similarity is None else f"{result.similarity:.3f}"
        lines.append(
            f"{mark} {pair.expect:10} {value:>6}  "
            f"{pair.player_a} {pair.season_a} / {pair.player_b} {pair.season_b}  "
            f"({result.detail})"
        )
    passed = sum(result.passed for result in results)
    lines.append(f"золотые пары: {passed}/{len(results)}")
    return "\n".join(lines)
