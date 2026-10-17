"""Инструменты агента.

Инструмент — типизированная функция: схема аргументов описана pydantic-моделью и из неё же
генерируется JSON Schema для провайдера, так что описание для модели и валидация на входе
не могут разъехаться.

Ключевое свойство: Инструмент не бросает исключений наружу. Любая неудача — не найдено,
неоднозначно, кривые аргументы — возвращается модели структурным результатом, и модель
самокорректируется следующим вызовом. Исключение оборвало бы цикл там, где у агента ещё
есть ход.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.agent.evidence import (
    Evidence,
    EvidenceView,
    ToolFailure,
    project_compare_players,
    project_player_percentiles,
    project_player_stats,
    project_reports,
    project_search_corpus,
    project_similar_players,
)
from court_vision.agent.prompts import load_prompt
from court_vision.agent.transport import embed
from court_vision.corpus.rerank import maybe_rerank
from court_vision.corpus.search import search
from court_vision.errors import DomainError
from court_vision.players.percentiles import get_player_percentiles
from court_vision.players.schemas import SEASON_PATTERN, PlayerComparisonResponse
from court_vision.players.service import get_player_stats, resolve_player
from court_vision.players.similar import DEFAULT_LIMIT, MAX_LIMIT, find_similar_players
from court_vision.problems import PROBLEM_TYPE_PREFIX
from court_vision.reports.inputs import build_report_input
from court_vision.reports.models import ScoutingReport
from court_vision.reports.schemas import ReportLanguage, ReportsToolResult
from court_vision.reports.service import brief, produce_report, search_reports

if TYPE_CHECKING:
    # Только аннотации: рантайм-импорт зациклил бы модули (loop импортирует tools).
    from court_vision.agent.loop import ToolContext

log = structlog.get_logger(__name__)

ToolResult = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Tool:
    """Инструмент, доступный агенту."""

    name: str
    description: str
    parameters: dict[str, Any]
    # Третий аргумент — контекст хода: реестр цитат и всё, что нужно Инструменту помимо
    # сессии. Инструменты без цитат (`get_player_stats`) его просто игнорируют.
    run: Callable[[AsyncSession, Mapping[str, Any], ToolContext], Awaitable[ToolResult]]
    # Проекция результата для карточки Evidence. Рядом с `run`, а не в отдельном реестре:
    # форма результата и его проекция меняются одним и тем же изменением Инструмента, и
    # разъехаться они могут только если живут в разных местах.
    project: Callable[[Mapping[str, Any]], EvidenceView | None]

    def definition(self) -> dict[str, Any]:
        """Описание в формате tools OpenAI — его litellm переводит под каждого провайдера."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# Аргументы `get_player_stats`. Пояснения в полях уезжают в JSON Schema — их читает модель,
# поэтому написаны они для неё. Докстринга у класса нет намеренно: pydantic положил бы её в
# схему верхним уровнем, и модель получала бы её вместо описания Инструмента.
class GetPlayerStatsArgs(BaseModel):
    player_name: str = Field(
        min_length=1,
        max_length=128,
        description="Имя игрока латиницей, например `LeBron James`. Опечатки допустимы.",
    )
    season: str | None = Field(
        default=None,
        pattern=SEASON_PATTERN,
        description="Сезон вида `2012-13`. Без него вернётся вся карьера.",
    )


async def run_get_player_stats(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = GetPlayerStatsArgs.model_validate(arguments)
    except ValidationError as error:
        return _error_result(
            "invalid-arguments",
            "Аргументы не прошли валидацию.",
            {
                "errors": [
                    {"location": list(item["loc"]), "message": item["msg"]}
                    for item in error.errors()
                ]
            },
        )

    try:
        stats = await get_player_stats(session, player_name=args.player_name, season=args.season)
    except DomainError as error:
        # Резолюция имени и отсутствие сезона — обычный ход диалога с моделью, а не сбой:
        # «не найдено» с кандидатами доезжает до неё результатом Инструмента.
        return _error_result(_kind(error), error.detail, error.extensions())

    return stats.model_dump(mode="json")


class GetPlayerPercentilesArgs(BaseModel):
    player_name: str = Field(
        min_length=1,
        max_length=128,
        description="Имя игрока латиницей, например `Stephen Curry`. Опечатки допустимы.",
    )
    season: str | None = Field(
        default=None,
        pattern=SEASON_PATTERN,
        description="Сезон вида `2015-16`. Без него берётся последний сезон игрока.",
    )


async def run_get_player_percentiles(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = GetPlayerPercentilesArgs.model_validate(arguments)
    except ValidationError as error:
        return _error_result(
            "invalid-arguments",
            "Аргументы не прошли валидацию.",
            {
                "errors": [
                    {"location": list(item["loc"]), "message": item["msg"]}
                    for item in error.errors()
                ]
            },
        )

    try:
        percentiles = await get_player_percentiles(
            session, player_name=args.player_name, season=args.season
        )
    except DomainError as error:
        return _error_result(_kind(error), error.detail, error.extensions())

    return percentiles.model_dump(mode="json")


class ComparedPlayer(BaseModel):
    player_name: str = Field(
        min_length=1,
        max_length=128,
        description="Имя игрока латиницей, например `Damian Lillard`. Опечатки допустимы.",
    )
    season: str | None = Field(
        default=None,
        pattern=SEASON_PATTERN,
        description="Сезон вида `2012-13`. Без него берётся последний сезон этого игрока.",
    )


class ComparePlayersArgs(BaseModel):
    players: list[ComparedPlayer] = Field(
        min_length=2,
        max_length=4,
        description="От двух до четырёх игроков; у каждого свой сезон (или последний).",
    )


async def run_compare_players(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = ComparePlayersArgs.model_validate(arguments)
    except ValidationError as error:
        return _error_result(
            "invalid-arguments",
            "Аргументы не прошли валидацию.",
            {
                "errors": [
                    {"location": list(item["loc"]), "message": item["msg"]}
                    for item in error.errors()
                ]
            },
        )

    compared = []
    for entry, requested in enumerate(args.players):
        try:
            compared.append(
                await get_player_percentiles(
                    session, player_name=requested.player_name, season=requested.season
                )
            )
        except DomainError as error:
            # Неуверенная Резолюция любого имени — отказ всего сравнения: сравнение с
            # подставленным наугад игроком хуже, чем уточняющий вопрос. `entry` говорит
            # модели, чьё имя поправить; остальные записи она повторит как были.
            return _error_result(_kind(error), error.detail, {**error.extensions(), "entry": entry})

    # Один игрок в одном сезоне дважды — не сравнение, а повтор; на radar он слился бы с собой.
    seen = [(item.player.player_id, item.season) for item in compared]
    if len(set(seen)) != len(seen):
        return _error_result(
            "duplicate-players",
            "В сравнении один и тот же игрок в одном и том же сезоне указан дважды.",
            {},
        )

    return PlayerComparisonResponse(players=compared).model_dump(mode="json")


class FindSimilarPlayersArgs(BaseModel):
    player_name: str = Field(
        min_length=1,
        max_length=128,
        description="Имя игрока латиницей, например `Luka Doncic`. Опечатки допустимы.",
    )
    season: str | None = Field(
        default=None,
        pattern=SEASON_PATTERN,
        description="Сезон вида `2015-16`, от которого искать похожих. Без него берётся "
        "последний квалифицированный сезон игрока.",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Сколько похожих игроков вернуть, не больше {MAX_LIMIT}.",
    )


async def run_find_similar_players(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = FindSimilarPlayersArgs.model_validate(arguments)
    except ValidationError as error:
        return _error_result(
            "invalid-arguments",
            "Аргументы не прошли валидацию.",
            {
                "errors": [
                    {"location": list(item["loc"]), "message": item["msg"]}
                    for item in error.errors()
                ]
            },
        )

    try:
        similar = await find_similar_players(
            session, player_name=args.player_name, season=args.season, limit=args.limit
        )
    except DomainError as error:
        # Сезон без отпечатка (`fingerprint-missing`) — обычный ответ границы Инструмента:
        # модель получает причину и сезоны, где отпечаток есть, и честно говорит об этом.
        return _error_result(_kind(error), error.detail, error.extensions())

    return similar.model_dump(mode="json")


class CreateScoutingReportArgs(BaseModel):
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
        default="en",
        description="Язык текста отчёта: `ru`, если пользователь пишет по-русски, иначе `en`.",
    )


async def run_create_scouting_report(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = CreateScoutingReportArgs.model_validate(arguments)
    except ValidationError as error:
        return _invalid_arguments(error)

    try:
        report_input = await build_report_input(
            session, player_name=args.player_name, season=args.season, language=args.language
        )
    except DomainError as error:
        return _error_result(_kind(error), error.detail, error.extensions())
    # Чтения закончены, а генерация длится десятки секунд: транзакция сессии Инструмента всё
    # это время держала бы соединение пула. Дальше отчёт берёт свои короткие транзакции сам.
    await session.rollback()

    report: ScoutingReport | None = None
    try:
        # Без стриминга: внутри цикла агента снапшоты некому показывать, нужен готовый отчёт.
        async with aclosing(
            produce_report(
                ctx.session_factory,
                settings=ctx.settings,
                clerk_user_id=ctx.clerk_user_id,
                report_input=report_input,
                trace_metadata={"trace_user_id": ctx.clerk_user_id},
            )
        ) as produced:
            async for item in produced:
                if isinstance(item, ScoutingReport):
                    report = item
    except DomainError as error:
        # Невалидный отчёт после повтора — отказ Инструмента, а не сбой ответа: модель
        # честно скажет пользователю, что отчёт не вышел, и предложит повторить.
        return _error_result(_kind(error), error.detail, error.extensions())
    if report is None:  # pragma: no cover — поток производства заканчивается отчётом или ошибкой
        return _error_result("tool-failed", "Отчёт не был создан.", {"retryable": True})

    return ReportsToolResult(
        reports=[brief(report, report_input.player.full_name, with_content=True)]
    ).model_dump(mode="json")


class SearchReportsArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=256,
        description="О чём искать, по смыслу: `Brunson clutch scoring`, `отчёты про центровых`.",
    )
    player_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Имя игрока латиницей — сузить поиск до отчётов по нему.",
    )


async def run_search_reports(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = SearchReportsArgs.model_validate(arguments)
    except ValidationError as error:
        return _invalid_arguments(error)

    player_id: int | None = None
    if args.player_name is not None:
        try:
            player_id = (await resolve_player(session, args.player_name)).id
        except DomainError as error:
            return _error_result(_kind(error), error.detail, error.extensions())

    settings = ctx.settings
    (query_embedding,) = await embed(
        ctx.session_factory,
        model=settings.embedding_model,
        texts=[args.query],
        dimensions=settings.embedding_dimensions,
        timeout=settings.llm_timeout_seconds,
        operation="agent.search_reports",
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
    )
    # Владелец — из контекста хода, а не из аргументов: модель чужого пользователя назвать
    # не может, а запрос без этого условия в сервисе не выражается.
    reports = await search_reports(
        session,
        clerk_user_id=ctx.clerk_user_id,
        query_embedding=query_embedding,
        player_id=player_id,
    )
    return ReportsToolResult(reports=reports).model_dump(mode="json")


def _invalid_arguments(error: ValidationError) -> ToolResult:
    return _error_result(
        "invalid-arguments",
        "Аргументы не прошли валидацию.",
        {
            "errors": [
                {"location": list(item["loc"]), "message": item["msg"]} for item in error.errors()
            ]
        },
    )


# Аргументы `search_corpus`. Описания — для модели, см. GetPlayerStatsArgs.
class SearchCorpusArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=256,
        description="Поисковый запрос по-английски: корпус английский, русский запрос "
        "не найдёт ничего. Например `shot clock violation rule`.",
    )
    source: Literal["rulebook", "wikipedia"] | None = Field(
        default=None,
        description="Сузить поиск до одного источника: `rulebook` — официальные правила "
        "NBA, `wikipedia` — история и термины игры. Без него ищется везде.",
    )


async def run_search_corpus(
    session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    try:
        args = SearchCorpusArgs.model_validate(arguments)
    except ValidationError as error:
        return _error_result(
            "invalid-arguments",
            "Аргументы не прошли валидацию.",
            {
                "errors": [
                    {"location": list(item["loc"]), "message": item["msg"]}
                    for item in error.errors()
                ]
            },
        )

    settings = ctx.settings
    # Эмбеддинг запроса — той же моделью и через ту же границу litellm, что и индексация:
    # вектор запроса обязан жить в том же пространстве, что и векторы чанков.
    (query_embedding,) = await embed(
        ctx.session_factory,
        model=settings.embedding_model,
        texts=[args.query],
        dimensions=settings.embedding_dimensions,
        timeout=settings.llm_timeout_seconds,
        operation="agent.search_corpus",
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
    )
    hits = await search(
        session,
        query_embedding=query_embedding,
        query_text=args.query,
        source=args.source,
        top_k=settings.rag_top_k,
    )
    # Реранкер ест запас выдачи (см. corpus/search.py); без него лишнее отрезаем сами.
    hits = (await maybe_rerank(hits, query=args.query, settings=settings))[: settings.rag_top_k]

    # `n` — сквозной номер цитирования из реестра хода: system.md просит цитировать `[n]`,
    # и номера в результатах двух вызовов продолжают одну нумерацию.
    return {
        "results": [
            {
                "n": ctx.citations.register(hit, tool_call_id=ctx.tool_call_id),
                "title": hit.document_title,
                "locator": hit.locator,
                "text": hit.text,
                "url": hit.url,
            }
            for hit in hits
        ]
    }


async def _guarded(
    tool: Tool, session: AsyncSession, arguments: Mapping[str, Any], ctx: ToolContext
) -> ToolResult:
    """Держит обещание «Инструмент не бросает» даже когда падает не он сам.

    Оборвавшееся соединение с БД или таймаут запроса — не повод убивать ответ агента: модель
    получает структурный отказ и может ответить пользователю тем, что уже знает.
    """
    try:
        return await tool.run(session, arguments, ctx)
    except Exception:
        log.exception("agent.tool_failed", tool=tool.name)
        return _error_result(
            "tool-failed", f"Инструмент `{tool.name}` не смог выполниться.", {"retryable": True}
        )


def _arguments_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema аргументов без служебного заголовка pydantic: имя Python-класса модели
    ничего не говорит, а место в контексте занимает на каждом шаге цикла."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


GET_PLAYER_STATS: Final = Tool(
    name="get_player_stats",
    description=load_prompt("tools/get_player_stats"),
    parameters=_arguments_schema(GetPlayerStatsArgs),
    run=run_get_player_stats,
    project=project_player_stats,
)

SEARCH_CORPUS: Final = Tool(
    name="search_corpus",
    description=load_prompt("tools/search_corpus"),
    parameters=_arguments_schema(SearchCorpusArgs),
    run=run_search_corpus,
    project=project_search_corpus,
)

GET_PLAYER_PERCENTILES: Final = Tool(
    name="get_player_percentiles",
    description=load_prompt("tools/get_player_percentiles"),
    parameters=_arguments_schema(GetPlayerPercentilesArgs),
    run=run_get_player_percentiles,
    project=project_player_percentiles,
)

COMPARE_PLAYERS: Final = Tool(
    name="compare_players",
    description=load_prompt("tools/compare_players"),
    parameters=_arguments_schema(ComparePlayersArgs),
    run=run_compare_players,
    project=project_compare_players,
)

FIND_SIMILAR_PLAYERS: Final = Tool(
    name="find_similar_players",
    description=load_prompt("tools/find_similar_players"),
    parameters=_arguments_schema(FindSimilarPlayersArgs),
    run=run_find_similar_players,
    project=project_similar_players,
)

CREATE_SCOUTING_REPORT: Final = Tool(
    name="create_scouting_report",
    description=load_prompt("tools/create_scouting_report"),
    parameters=_arguments_schema(CreateScoutingReportArgs),
    run=run_create_scouting_report,
    project=project_reports,
)

SEARCH_REPORTS: Final = Tool(
    name="search_reports",
    description=load_prompt("tools/search_reports"),
    parameters=_arguments_schema(SearchReportsArgs),
    run=run_search_reports,
    project=project_reports,
)

TOOLS: Final = (
    GET_PLAYER_STATS,
    GET_PLAYER_PERCENTILES,
    COMPARE_PLAYERS,
    FIND_SIMILAR_PLAYERS,
    CREATE_SCOUTING_REPORT,
    SEARCH_REPORTS,
    SEARCH_CORPUS,
)
TOOLS_BY_NAME: Final = {tool.name: tool for tool in TOOLS}

DEFINITIONS: Final = [tool.definition() for tool in TOOLS]


async def execute_tool(
    session: AsyncSession, *, name: str, arguments: Mapping[str, Any] | None, ctx: ToolContext
) -> ToolResult:
    """Выполняет Инструмент по имени. Неизвестное имя и нечитаемые аргументы — тоже
    результат для модели: она называет Инструмент сама и способна исправиться."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        log.warning("agent.unknown_tool", tool=name)
        return _error_result(
            "unknown-tool",
            f"Инструмента `{name}` не существует.",
            {"available_tools": sorted(TOOLS_BY_NAME)},
        )
    if arguments is None:
        return _error_result(
            "invalid-arguments", "Аргументы Инструмента должны быть JSON-объектом.", {}
        )
    return await _guarded(tool, session, arguments, ctx)


def evidence_for(*, name: str, result: ToolResult) -> Evidence:
    """Чем закончился вызов с точки зрения карточки Evidence.

    Разбор `error` живёт здесь же, где эта форма собирается (`_error_result`): иначе один
    словарь читается по ключам в двух модулях и переименование поля ломает второй молча.
    """
    error = result.get("error")
    if isinstance(error, Mapping):
        return Evidence(failure=ToolFailure(kind=str(error["kind"]), detail=str(error["detail"])))
    tool = TOOLS_BY_NAME.get(name)
    # Неизвестное имя Инструмента до сюда не доходит — `execute_tool` отвечает на него
    # отказом, — но проверка нужна mypy и переживёт смену этого порядка.
    return Evidence(view=tool.project(result) if tool is not None else None)


def _error_result(kind: str, detail: str, extra: Mapping[str, Any]) -> ToolResult:
    """Единая форма неудачи: модель разбирает `error.kind`, а не текст сообщения.

    Данные ошибки кладутся первыми, `kind` и `detail` — поверх: расширяющий член с таким же
    именем иначе перебил бы дискриминатор, по которому модель и понимает, что произошло.
    """
    return {"error": {**extra, "kind": kind, "detail": detail}}


def _kind(error: DomainError) -> str:
    """Доменная ошибка уже несёт стабильный идентификатор типа — второй словарь кодов
    специально для модели не заводим."""
    return error.problem_type.removeprefix(PROBLEM_TYPE_PREFIX)
