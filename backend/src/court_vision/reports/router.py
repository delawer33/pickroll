"""HTTP-поверхность Скаутинг-отчётов: заказ потоком, список своих, отчёт по идентификатору.

Роутер тонкий: чтение — в `service.py`, генерация — в `streaming.py`.
"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.sse import EventSourceResponse

from court_vision.auth.dependencies import CurrentUserId
from court_vision.config import Settings
from court_vision.db import SessionDep, SessionFactoryDep
from court_vision.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page
from court_vision.reports.events import ReportStreamEvent
from court_vision.reports.inputs import ReportInput, build_report_input
from court_vision.reports.schemas import ReportCreate, ReportResponse, ReportSummary
from court_vision.reports.service import get_report, list_reports
from court_vision.reports.streaming import report_events

router = APIRouter(prefix="/reports", tags=["reports"])

ReportId = Annotated[UUID, Path(description="Идентификатор отчёта.")]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Размер страницы.")]
# Пустая строка приравнивается к отсутствию курсора (см. conversations/router.py).
Cursor = Annotated[
    str | None,
    Query(description="Курсор из `next_cursor` предыдущей страницы. Опаковый — не разбирать."),
]


async def _report_input(session_factory: SessionFactoryDep, payload: ReportCreate) -> ReportInput:
    """Собирает вход отчёта до начала потока.

    Именно зависимость, а не первые строки генератора: неопознанный игрок, неоднозначное имя
    и сезон без данных уходят обычным Problem Details со статусом, а не событием внутри
    `200 OK`. Транзакция короткая и закрывается до начала генерации.
    """
    async with session_factory() as session:
        return await build_report_input(
            session,
            player_name=payload.player_name,
            season=payload.season,
            language=payload.language,
        )


@router.post(
    "",
    response_class=EventSourceResponse,
    summary="Заказать отчёт и получить его потоком снапшотов",
)
async def order_report(
    request: Request,
    session_factory: SessionFactoryDep,
    user_id: CurrentUserId,
    report_input: Annotated[ReportInput, Depends(_report_input)],
) -> AsyncIterator[ReportStreamEvent]:
    """Стримит генерацию событиями SSE: `snapshot` (частичный отчёт, заменяет предыдущий),
    затем `done` с идентификатором сохранённого отчёта — либо `error` вместо `done`.

    Сессия запроса не берётся намеренно: тело потока генерируется после возврата из
    обработчика, и транзакция запроса висела бы открытой всё время ответа модели.
    """
    settings: Settings = request.app.state.settings
    async for event in report_events(
        session_factory,
        settings=settings,
        clerk_user_id=user_id,
        report_input=report_input,
        instance=request.url.path,
    ):
        yield event


@router.get("", response_model=Page[ReportSummary])
async def list_own_reports(
    session: SessionDep,
    user_id: CurrentUserId,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: Cursor = None,
    player_id: Annotated[int | None, Query(description="Только отчёты по этому игроку.")] = None,
) -> Page[ReportSummary]:
    """Свои отчёты, свежие сверху. Чужих в списке не бывает."""
    return await list_reports(
        session, clerk_user_id=user_id, limit=limit, cursor=cursor or None, player_id=player_id
    )


@router.get("/{report_id}", response_model=ReportResponse)
async def read_report(
    session: SessionDep, user_id: CurrentUserId, report_id: ReportId
) -> ReportResponse:
    """Отчёт целиком. Чужой отчёт — 404."""
    return await get_report(session, report_id=report_id, clerk_user_id=user_id)
