"""Схема Скаутинг-отчёта и wire-модели модуля.

`ScoutingReportContent` — одновременно JSON Schema для structured output модели и модель
валидации того, что модель вернула. Ограничений вроде `min_length` или `pattern` в ней нет
намеренно: шлюзы со strict-режимом структурированного вывода отвергают такие ключевые слова,
а смысловые проверки (метрика есть во входе, сопоставимый игрок из входа) всё равно живут в
`generate.check_grounding` — схема их выразить не может.
"""

from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from court_vision.players.schemas import SEASON_PATTERN

# Версия схемы содержимого, записываемая в каждый отчёт. Растёт при несовместимом изменении
# `ScoutingReportContent`; отчёты неизменны, поэтому старые версии читаются как есть.
SCHEMA_VERSION: Final = 1

ReportLanguage = Literal["ru", "en"]


class ReportPoint(BaseModel):
    """Сильная или слабая сторона: утверждение и метрики входа, на которых оно держится."""

    model_config = ConfigDict(extra="forbid")

    claim: str
    # Ключи метрик из входа (`allowed_metric_keys`): утверждение без метрики не проверить.
    metrics: list[str]


class Comparable(BaseModel):
    """Сопоставимый игрок — строго из результатов поиска Похожих игроков во входе."""

    model_config = ConfigDict(extra="forbid")

    player_name: str
    season: str
    # Чем похож и чем нет — по признакам сходства из входа, без выдуманных причин.
    reason: str


class ScoutingReportContent(BaseModel):
    """Содержимое отчёта: фиксированные разделы, чтобы отчёты были сравнимы между собой."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    archetype: str
    strengths: list[ReportPoint]
    weaknesses: list[ReportPoint]
    stat_profile: str
    comparables: list[Comparable]
    outlook: str


class PartialReportPoint(BaseModel):
    claim: str | None = None
    metrics: list[str] = Field(default_factory=list)


class PartialComparable(BaseModel):
    player_name: str | None = None
    season: str | None = None
    reason: str | None = None


class PartialScoutingReport(BaseModel):
    """Снапшот отчёта по ходу генерации: то, что модель успела дописать к этому моменту.

    Каждое поле необязательно — раздел появляется, когда модель его закончила. Снапшот
    всегда целиком заменяет предыдущий, а не дополняет его: после повтора генерации поток
    начинается с пустого снапшота.
    """

    summary: str | None = None
    archetype: str | None = None
    strengths: list[PartialReportPoint] | None = None
    weaknesses: list[PartialReportPoint] | None = None
    stat_profile: str | None = None
    comparables: list[PartialComparable] | None = None
    outlook: str | None = None


class ReportCreate(BaseModel):
    """Заказ отчёта: игрок и сезон. Без сезона берётся последний сезон игрока."""

    player_name: str = Field(
        min_length=1,
        max_length=128,
        description="Имя игрока латиницей, например `Jalen Brunson`. Опечатки допустимы.",
    )
    season: str | None = Field(
        default=None,
        pattern=SEASON_PATTERN,
        description="Сезон вида `2023-24`. Без него берётся последний сезон игрока.",
    )
    language: ReportLanguage = Field(
        default="en", description="Язык текста отчёта; ключи метрик и имена не переводятся."
    )


class ReportPlayer(BaseModel):
    player_id: int
    full_name: str


class ReportSummary(BaseModel):
    """Строка списка отчётов."""

    report_id: UUID
    player: ReportPlayer
    season: str
    archetype: str
    summary: str
    created_at: datetime


class ReportResponse(ReportSummary):
    """Отчёт целиком."""

    content: ScoutingReportContent
    schema_version: int
    model: str


class ReportBrief(ReportSummary):
    """Отчёт в результате Инструмента: резюме и ссылка на страницу отчёта."""

    # Путь страницы во фронте: карточка в чате ведёт по нему.
    url: str
    # Только у `search_reports`: косинусная близость резюме запросу.
    similarity: float | None = None
    # Только у `create_scouting_report`: модель отвечает пользователю по содержимому.
    content: ScoutingReportContent | None = None


class ReportsToolResult(BaseModel):
    """Результат `create_scouting_report` и `search_reports` — форма, которую собирают
    Инструменты и читает проекция."""

    reports: list[ReportBrief]


def content_json_schema() -> dict[str, Any]:
    """JSON Schema содержимого без служебного заголовка pydantic."""
    schema = ScoutingReportContent.model_json_schema()
    schema.pop("title", None)
    return schema
