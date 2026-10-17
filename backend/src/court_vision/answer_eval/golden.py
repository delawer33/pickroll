"""Золотой набор ответов: вопросы к прод-агенту и то, что от ответа ожидается.

Рядом с ретривал-набором (`evals/golden.yaml`) лежит `evals/answers.yaml`. Ретривал-набор
проверяет поиск (документ + локатор), этот — готовый ответ: поэтому элемент описывает
свойства ответа, а не место в корпусе.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal[
    "stats", "rules", "biography", "old_era", "ambiguity", "comparison", "similar", "reports"
]
SourceName = Literal["rulebook", "wikipedia"]

# Из src/court_vision/answer_eval/ три уровня вверх — backend/. Как у ретривал-eval: файлы
# нужны только в dev-чекауте, в собранном пакете их нет.
EVALS_DIR: Final = Path(__file__).resolve().parents[3] / "evals"
DEFAULT_ANSWERS_PATH: Final = EVALS_DIR / "answers.yaml"
DEFAULT_THRESHOLDS_PATH: Final = EVALS_DIR / "answers_thresholds.yaml"


class GoldenAnswer(BaseModel):
    """Один вопрос Золотого набора ответов."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    category: Category
    question: str = Field(min_length=1)
    # Эталонные факты списком, с числами: судья сверяет ответ с ними, а не с собственной
    # памятью. Числа берутся из данных (см. комментарий в YAML), а не из головы автора.
    reference: tuple[str, ...] = Field(min_length=1)
    # Подмножество Инструментов, которые обязаны быть вызваны; лишние вызовы не штрафуются.
    expect_tools: tuple[str, ...] = ()
    # Источники, цитаты из которых обязаны быть в ответе.
    expect_sources: tuple[SourceName, ...] = ()
    # False — вопрос, на который честный ответ и есть отказ (нет такого игрока, нет данных).
    must_not_refuse: bool = True

    @model_validator(mode="after")
    def _sources_need_citations(self) -> Self:
        # Источник цитируется только из корпуса: вопрос с expect_sources без вызова
        # search_corpus невыполним по построению, и ловить это лучше при загрузке набора.
        if self.expect_sources and "search_corpus" not in self.expect_tools:
            raise ValueError(f"{self.id}: expect_sources требует search_corpus в expect_tools")
        return self


def load_answers(path: Path = DEFAULT_ANSWERS_PATH) -> list[GoldenAnswer]:
    """Золотой набор ответов из YAML; id уникальны — по ним работают `--id` и Langfuse."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    answers = [GoldenAnswer.model_validate(entry) for entry in raw]
    ids = [answer.id for answer in answers]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"повторяющиеся id в Золотом наборе ответов: {duplicates}")
    return answers


def select(
    answers: list[GoldenAnswer], *, categories: list[str] | None, ids: list[str] | None
) -> list[GoldenAnswer]:
    """Фильтры CLI `--category` и `--id`: оба заданы — пересечение. Неизвестное значение —
    ошибка, а не пустой прогон: пустой прогон с нулевым кодом выглядел бы успехом."""
    known_ids = {answer.id for answer in answers}
    known_categories = {answer.category for answer in answers}
    for item in ids or []:
        if item not in known_ids:
            raise ValueError(f"нет вопроса с id {item!r}")
    for item in categories or []:
        if item not in known_categories:
            raise ValueError(f"нет вопросов категории {item!r}")
    return [
        answer
        for answer in answers
        if (not categories or answer.category in categories) and (not ids or answer.id in ids)
    ]
