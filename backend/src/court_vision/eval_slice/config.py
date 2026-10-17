"""Что входит в Eval-срез: конфиг, сезоны из Золотого набора, проверка покрытия.

Срез собирается из трёх источников сезонов: последний сезон seed (берётся из базы — «свежее»
меняется вместе с seed), сезоны из `slice.yaml` (опорные: например, один сезон до трёхочковых)
и сезоны, о которых спрашивают вопросы Золотого набора. Третий источник выводится из самого
набора, а не дублируется в конфиге: добавили вопрос про 2018-19 — срез при пересборке
подтянет сезон сам.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict

from court_vision.answer_eval.golden import EVALS_DIR, GoldenAnswer

DEFAULT_CONFIG_PATH: Final = EVALS_DIR / "slice.yaml"
DEFAULT_SLICE_DIR: Final = EVALS_DIR / "slice"

# Сезон в формате базы: "2015-16". Границы слов — чтобы не вытащить кусок из «1961-1962».
_SEASON_PATTERN: Final = re.compile(r"\b(\d{4}-\d{2})\b")


class SliceConfig(BaseModel):
    """`backend/evals/slice.yaml`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Опорные сезоны сверх тех, что называет Золотой набор. Сейчас — последний сезон перед
    # введением трёхочковых (они появились в 1979-80): на нём живёт ветка «метрики не было →
    # null, не ноль».
    seasons: tuple[str, ...] = ()
    # Последний сезон seed — «сегодняшний» сезон, на котором спрашивают чаще всего.
    include_latest_season: bool = True


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> SliceConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = SliceConfig.model_validate(raw)
    bad = [season for season in config.seasons if not _SEASON_PATTERN.fullmatch(season)]
    if bad:
        raise ValueError(f"сезоны в формате ГГГГ-ГГ, получено: {bad}")
    return config


def seasons_in_questions(answers: Iterable[GoldenAnswer]) -> set[str]:
    """Сезоны, о которых вопросы спрашивают. Эталон не смотрим: «1973-74» в эталоне про блоки
    — справка к ответу, данные того сезона для ответа не нужны."""
    return {match for answer in answers for match in _SEASON_PATTERN.findall(answer.question)}


def wikipedia_titles(answers: Iterable[GoldenAnswer]) -> list[str]:
    """Статьи Wikipedia, на которые ссылается Золотой набор, без повторов, в порядке набора."""
    titles: dict[str, None] = {}
    for answer in answers:
        for title in answer.expect_documents:
            titles.setdefault(title)
    return list(titles)


def wanted_seasons(
    config: SliceConfig,
    answers: Iterable[GoldenAnswer],
    *,
    available: Iterable[str],
) -> list[str]:
    """Сезоны среза: конфиг + последний + названные в вопросах — но только те, что есть в
    полной базе. Названный вопросом сезон, которого нет и в полной базе («2035-36»), в срез
    не просится: честный ответ там и на полной базе — «данных нет»."""
    in_source = set(available)
    chosen = set(config.seasons) | seasons_in_questions(answers)
    if config.include_latest_season and in_source:
        chosen.add(max(in_source))
    return sorted(chosen & in_source)


def coverage_problems(
    answers: Iterable[GoldenAnswer],
    *,
    slice_seasons: Iterable[str],
    source_seasons: Iterable[str],
    slice_documents: Iterable[str],
) -> list[str]:
    """Вопросы Золотого набора, на которые срез ответить не сможет.

    Две причины: вопрос про сезон, который есть в полной базе, но не попал в срез, и вопрос,
    ждущий статью, которой в срезе нет. Сезон, которого нет и в полной базе, — не проблема.
    Проверка идёт по манифесту среза, поэтому ловит и устаревший срез: набор дописали, а срез
    не пересобрали.
    """
    seasons = set(slice_seasons)
    known = set(source_seasons)
    documents = set(slice_documents)
    problems: list[str] = []
    for answer in answers:
        for season in sorted(set(_SEASON_PATTERN.findall(answer.question))):
            if season in known and season not in seasons:
                problems.append(f"{answer.id}: сезона {season} нет в срезе")
        problems.extend(
            f"{answer.id}: статьи {title!r} нет в срезе"
            for title in answer.expect_documents
            if title not in documents
        )
    return problems
