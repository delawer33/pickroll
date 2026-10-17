"""События SSE-потока `POST /reports`.

Отдельным модулем, а не рядом со схемой отчёта: событие `error` несёт `Problem` из модуля
Диалогов, а тот через проекции Evidence импортирует схему отчёта — общий файл замкнул бы
импорты в кольцо. Имена с префиксом `Report`: в OpenAPI схемы живут в одном пространстве
имён, и одноимённые `DoneEvent` Диалогов и отчётов получили бы модульные суффиксы.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel

from court_vision.conversations.schemas import Problem
from court_vision.reports.schemas import PartialScoutingReport


class ReportSnapshotEvent(BaseModel):
    """Снапшот отчёта по ходу генерации; заменяет предыдущий целиком."""

    type: Literal["snapshot"] = "snapshot"
    report: PartialScoutingReport


class ReportDoneEvent(BaseModel):
    """Отчёт сгенерирован, провалидирован и сохранён."""

    type: Literal["done"] = "done"
    report_id: UUID


class ReportErrorEvent(BaseModel):
    """Отчёт не получился и не сохранён. Приходит вместо `done` — поток закрывается штатно."""

    type: Literal["error"] = "error"
    problem: Problem


class ReportStreamEvent(
    RootModel[
        Annotated[
            ReportSnapshotEvent | ReportDoneEvent | ReportErrorEvent,
            Field(discriminator="type"),
        ]
    ]
):
    """Событие SSE-потока `POST /reports`. Дискриминатор — поле `type`."""
