"""Генерация отчёта как SSE-поток.

Транзакциями модуль владеет сам, по той же причине, что и ответ агента
(`conversations/streaming.py`): тело SSE-ответа генерируется после того, как обработчик
вернул ответ, и транзакция запроса висела бы открытой всё время, пока отвечает модель.

Любая ошибка становится событием `error`, а не обрывом соединения: заголовки ушли ещё до
первого снапшота, статус уже `200`, и оборванный поток клиент не отличил бы от сетевого сбоя.
"""

from collections.abc import AsyncIterator
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.config import Settings
from court_vision.conversations.schemas import Problem
from court_vision.errors import DomainError
from court_vision.problems import INTERNAL_PROBLEM, domain_problem, problem_body
from court_vision.reports.events import (
    ReportDoneEvent,
    ReportErrorEvent,
    ReportSnapshotEvent,
    ReportStreamEvent,
)
from court_vision.reports.generate import ReportSnapshot
from court_vision.reports.inputs import ReportInput
from court_vision.reports.service import produce_report

log = structlog.get_logger(__name__)


async def report_events(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    clerk_user_id: str,
    report_input: ReportInput,
    instance: str,
) -> AsyncIterator[ReportStreamEvent]:
    """`snapshot` по мере генерации, затем `done` с идентификатором — либо `error`."""
    try:
        async for item in produce_report(
            session_factory,
            settings=settings,
            clerk_user_id=clerk_user_id,
            report_input=report_input,
            trace_metadata={"trace_user_id": clerk_user_id},
        ):
            if isinstance(item, ReportSnapshot):
                yield ReportStreamEvent(ReportSnapshotEvent(report=item.report))
            else:
                yield ReportStreamEvent(ReportDoneEvent(report_id=item.id))
    except DomainError as error:
        log.warning("reports.stream_failed", problem_type=error.problem_type)
        yield ReportStreamEvent(
            ReportErrorEvent(problem=_problem(domain_problem(error, instance=instance)))
        )
    except Exception:
        log.exception("reports.stream_crashed")
        yield ReportStreamEvent(
            ReportErrorEvent(
                problem=_problem(
                    problem_body(
                        status=500,
                        title="Internal server error",
                        detail="Внутренняя ошибка сервера.",
                        instance=instance,
                        problem_type=INTERNAL_PROBLEM,
                    )
                )
            )
        )


def _problem(body: dict[str, Any]) -> Problem:
    return Problem.model_validate(body)
