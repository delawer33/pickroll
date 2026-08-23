"""Проекция результата Инструмента в карточку Evidence.

Результат Инструмента написан для модели: у `get_player_stats` это 33 метрики на сезон, и
целиком в поток он не уезжает — карточка от такого становится дампом JSON, а не витриной.
Поэтому в поток идёт проекция: одна из форм, которые обещает дизайн-система (§7.2) —
таблица, полосы или цитата.

Проекция — presentation-модель: ячейки таблицы уже строки, потому что решение «сколько
знаков после запятой у процента попаданий» принимается там, где известно, что это процент,
а не в вёрстке, где это просто число.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, Field, RootModel, ValidationError

from court_vision.players.schemas import PlayerStatsResponse, SeasonStats

log = structlog.get_logger(__name__)

# Прочерк вместо пустой ячейки: пустое место читается как «забыли посчитать», а `0` — как
# посчитанный ноль. Процент попаданий без попыток — именно «нечего показать».
BLANK = "—"


class TableView(BaseModel):
    """Табличная проекция: колонки и уже отформатированные строки."""

    kind: Literal["table"] = "table"
    columns: list[str]
    rows: list[list[str]]


class Bar(BaseModel):
    """Метрика игрока относительно сравнимой выборки."""

    label: str
    percentile: int = Field(ge=0, le=100)


class BarsView(BaseModel):
    """Полосы перцентилей. Объявлена заранее: форму обещает §7.2, а наполнит её
    `get_player_percentiles` в спринте 3 — контракт потока от этого не поменяется."""

    kind: Literal["bars"] = "bars"
    bars: list[Bar]


class QuoteView(BaseModel):
    """Цитата из корпуса. Объявлена заранее, наполняется вместе с RAG в спринте 2."""

    kind: Literal["quote"] = "quote"
    text: str


class Quote(BaseModel):
    """Одна цитата из выдачи `search_corpus`: номер `[n]`, документ, локатор, текст."""

    index: int
    title: str
    locator: str
    text: str


class QuotesView(BaseModel):
    """Выдача `search_corpus` в карточке: список цитат с номерами цитирования.

    Отдельная форма, а не список `QuoteView`: у цитаты из поиска есть номер и локатор —
    именно они делают карточку провенансом, а не сборником выдержек.
    """

    kind: Literal["quotes"] = "quotes"
    quotes: list[Quote]


class EvidenceView(
    RootModel[Annotated[TableView | BarsView | QuoteView | QuotesView, Field(discriminator="kind")]]
):
    """Результат Инструмента в форме, пригодной для карточки. Дискриминатор — `kind`."""


class ToolFailure(BaseModel):
    """Отказ Инструмента, который увидела модель — и обязан увидеть пользователь.

    Без него удачный вызов и вернувшийся ни с чем в рейке выглядят одинаково: «Агент сходил
    за данными» там, где данных не нашлось.
    """

    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class Evidence:
    """Чем закончился вызов Инструмента с точки зрения карточки.

    Взаимоисключающие поля: у отказа проекции нет — показывать в карточке нечего, кроме
    самого отказа.
    """

    failure: ToolFailure | None = None
    view: EvidenceView | None = None


def project_player_stats(result: Mapping[str, Any]) -> EvidenceView | None:
    """Статистика игрока — таблица по сезону на строку.

    Метрики выбраны те, которыми сезон описывают в разговоре: остальные 27 в карточке
    превращаются в горизонтальный скролл, а в ответе Агента всё равно не участвуют.
    """
    try:
        stats = PlayerStatsResponse.model_validate(result)
    except ValidationError:
        # Успешный результат Инструмента — это дамп `PlayerStatsResponse`, так что сюда
        # попадает только рассогласование схемы с самой собой. Проекции нет, но ответ
        # Агента из-за неё падать не должен: карточка обойдётся штампом источника. В логе
        # это остаётся — молча пропавшая проекция иначе выглядит как «у Инструмента её нет».
        log.warning("agent.evidence_projection_failed", tool="get_player_stats", exc_info=True)
        return None
    if not stats.seasons:
        return None
    return EvidenceView(
        TableView(
            columns=["Season", "GP", "Pts", "Reb", "Ast", "FG%"],
            rows=[_season_row(season) for season in stats.seasons],
        )
    )


def _season_row(season: SeasonStats) -> list[str]:
    averages = season.averages
    return [
        season.season,
        str(season.games_played),
        _number(averages.points),
        _number(averages.rebounds_total),
        _number(averages.assists),
        _percentage(averages.field_goal_percentage),
    ]


class _SearchCorpusItem(BaseModel):
    """Строка результата `search_corpus` — форма, которую собирает `run_search_corpus`."""

    n: int
    title: str
    locator: str
    text: str
    url: str | None = None


class _SearchCorpusResult(BaseModel):
    results: list[_SearchCorpusItem]


def project_search_corpus(result: Mapping[str, Any]) -> EvidenceView | None:
    """Выдача поиска — список цитат с номерами `[n]` и локаторами."""
    try:
        found = _SearchCorpusResult.model_validate(result)
    except ValidationError:
        # См. project_player_stats: рассогласование схемы с самой собой — не повод ронять
        # ответ Агента, но молча пропасть проекция тоже не должна.
        log.warning("agent.evidence_projection_failed", tool="search_corpus", exc_info=True)
        return None
    if not found.results:
        return None
    return EvidenceView(
        QuotesView(
            quotes=[
                Quote(index=item.n, title=item.title, locator=item.locator, text=item.text)
                for item in found.results
            ]
        )
    )


def _number(value: float) -> str:
    return f"{value:.1f}"


def _percentage(value: float | None) -> str:
    """Доля 0–1 из wire-модели, а в карточке — проценты: `56.5`, как в любом протоколе."""
    return BLANK if value is None else f"{value * 100:.1f}"
