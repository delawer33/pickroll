"""Скаутинг-отчёты: производство, чтение, поиск по резюме.

Все чтения привязаны к владельцу одним условием запроса (`clerk_user_id`), как у Диалогов:
забыть добавить `and` к существующему запросу труднее, чем забыть вызвать проверку.

Сервис читающих функций не коммитит: unit of work равен HTTP-запросу. Производство
(`produce_report`) — исключение: оно длится дольше запроса, как ответ агента, и берёт свои
короткие транзакции само.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.transport import embed
from court_vision.config import Settings
from court_vision.pagination import InvalidCursor, Page, decode_cursor, encode_cursor
from court_vision.players.models import Player
from court_vision.reports.errors import ReportNotFound
from court_vision.reports.generate import GeneratedReport, ReportSnapshot, generate_report
from court_vision.reports.inputs import ReportInput
from court_vision.reports.models import ScoutingReport
from court_vision.reports.schemas import (
    SCHEMA_VERSION,
    ReportBrief,
    ReportPlayer,
    ReportResponse,
    ReportSummary,
    ScoutingReportContent,
)

EMBED_OPERATION: Final = "reports.embed"
SEARCH_LIMIT: Final = 5


def report_url(report_id: UUID) -> str:
    """Путь страницы отчёта во фронте — на него ведёт карточка в чате."""
    return f"/reports/{report_id}"


def embedding_text(player_name: str, season: str, content: ScoutingReportContent) -> str:
    """Что эмбеддится: имя и сезон вместе с резюме — иначе «что я писал про Брансона» искало
    бы по смыслу текста, в котором фамилии может не быть."""
    return f"{player_name}, {season}. {content.archetype}. {content.summary}"


async def produce_report(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    clerk_user_id: str,
    report_input: ReportInput,
    trace_metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[ReportSnapshot | ScoutingReport]:
    """Генерирует отчёт, считает эмбеддинг резюме и сохраняет; последним отдаёт сохранённый.

    Сохраняется только отчёт, прошедший проверку: невалидный не попадает в базу, а доменная
    ошибка генерации доезжает до вызывающего.
    """
    async for item in generate_report(
        session_factory,
        settings=settings,
        report_input=report_input,
        trace_metadata=trace_metadata,
    ):
        if isinstance(item, ReportSnapshot):
            yield item
            continue
        assert isinstance(item, GeneratedReport)  # других типов в потоке генерации нет
        name, season = report_input.player.full_name, report_input.season
        (embedding,) = await embed(
            session_factory,
            model=settings.embedding_model,
            texts=[embedding_text(name, season, item.content)],
            dimensions=settings.embedding_dimensions,
            timeout=settings.llm_timeout_seconds,
            operation=EMBED_OPERATION,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
        )
        async with session_factory() as session:
            report = ScoutingReport(
                clerk_user_id=clerk_user_id,
                player_id=report_input.player.player_id,
                season=report_input.season,
                content=item.content.model_dump(mode="json"),
                schema_version=SCHEMA_VERSION,
                model=item.model,
                summary_embedding=embedding,
            )
            session.add(report)
            await session.commit()
        yield report


async def get_report(
    session: AsyncSession, *, report_id: UUID, clerk_user_id: str
) -> ReportResponse:
    """Отчёт по идентификатору — только свой, чужой неотличим от несуществующего."""
    row = (
        await session.execute(
            select(ScoutingReport, Player.full_name)
            .join(Player, Player.id == ScoutingReport.player_id)
            .where(ScoutingReport.id == report_id, ScoutingReport.clerk_user_id == clerk_user_id)
        )
    ).one_or_none()
    if row is None:
        raise ReportNotFound()
    report, player_name = row
    return ReportResponse(
        **_summary(report, player_name).model_dump(),
        content=ScoutingReportContent.model_validate(report.content),
        schema_version=report.schema_version,
        model=report.model,
    )


async def list_reports(
    session: AsyncSession,
    *,
    clerk_user_id: str,
    limit: int,
    cursor: str | None,
    player_id: int | None = None,
) -> Page[ReportSummary]:
    """Свои отчёты, свежие сверху; необязательный фильтр по игроку.

    Ключ сортировки — `(created_at, id)`, обе колонки неизменны (отчёт неизменяем целиком),
    так что граница страницы не плывёт, что бы ни появилось рядом.
    """
    statement = (
        select(ScoutingReport, Player.full_name)
        .join(Player, Player.id == ScoutingReport.player_id)
        .where(ScoutingReport.clerk_user_id == clerk_user_id)
        .order_by(ScoutingReport.created_at.desc(), ScoutingReport.id.desc())
    )
    if player_id is not None:
        statement = statement.where(ScoutingReport.player_id == player_id)
    if cursor is not None:
        created_at, report_id = _report_cursor(cursor)
        statement = statement.where(
            tuple_(ScoutingReport.created_at, ScoutingReport.id) < (created_at, report_id)
        )

    # Запрашивается на один больше: лишняя строка и есть ответ «есть ли следующая страница».
    rows = (await session.execute(statement.limit(limit + 1))).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1][0].created_at.isoformat(), page[-1][0].id)
        if len(rows) > limit and page
        else None
    )
    return Page(items=[_summary(report, name) for report, name in page], next_cursor=next_cursor)


async def search_reports(
    session: AsyncSession,
    *,
    clerk_user_id: str,
    query_embedding: list[float],
    player_id: int | None = None,
    limit: int = SEARCH_LIMIT,
) -> list[ReportBrief]:
    """Свои отчёты, ближайшие по косинусу к запросу; чужих не бывает ни при каком запросе.

    Точный скан, а не HNSW: отчётов у одного пользователя десятки, а фильтр по владельцу
    после обхода графа молча терял бы выдачу (то же решение, что у Похожих игроков).
    """
    distance = ScoutingReport.summary_embedding.cosine_distance(query_embedding)
    statement = (
        select(ScoutingReport, Player.full_name, distance.label("distance"))
        .join(Player, Player.id == ScoutingReport.player_id)
        .where(ScoutingReport.clerk_user_id == clerk_user_id)
        .order_by(distance, ScoutingReport.id)
        .limit(limit)
    )
    if player_id is not None:
        statement = statement.where(ScoutingReport.player_id == player_id)
    rows = (await session.execute(statement)).all()
    return [
        ReportBrief(
            **_summary(report, name).model_dump(),
            url=report_url(report.id),
            similarity=round(1.0 - float(dist), 3),
        )
        for report, name, dist in rows
    ]


def brief(report: ScoutingReport, player_name: str, *, with_content: bool) -> ReportBrief:
    """Отчёт в форме результата Инструмента."""
    return ReportBrief(
        **_summary(report, player_name).model_dump(),
        url=report_url(report.id),
        content=ScoutingReportContent.model_validate(report.content) if with_content else None,
    )


def _summary(report: ScoutingReport, player_name: str) -> ReportSummary:
    return ReportSummary(
        report_id=report.id,
        player=ReportPlayer(player_id=report.player_id, full_name=player_name),
        season=report.season,
        archetype=report.content["archetype"],
        summary=report.content["summary"],
        created_at=report.created_at,
    )


def _report_cursor(cursor: str) -> tuple[datetime, UUID]:
    created_at, report_id = decode_cursor(cursor, parts=2)
    try:
        parsed = datetime.fromisoformat(created_at)
        identifier = UUID(report_id)
    except ValueError as error:
        raise InvalidCursor() from error
    if parsed.tzinfo is None:
        # Метку без зоны драйвер истолковал бы по часам процесса — граница страницы поплыла бы.
        raise InvalidCursor()
    return parsed, identifier
