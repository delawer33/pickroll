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
from dataclasses import dataclass
from typing import Any, Final

import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.agent.prompts import load_prompt
from court_vision.errors import DomainError
from court_vision.players.schemas import SEASON_PATTERN
from court_vision.players.service import get_player_stats
from court_vision.problems import PROBLEM_TYPE_PREFIX

log = structlog.get_logger(__name__)

ToolResult = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Tool:
    """Инструмент, доступный агенту."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[AsyncSession, Mapping[str, Any]], Awaitable[ToolResult]]

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


async def run_get_player_stats(session: AsyncSession, arguments: Mapping[str, Any]) -> ToolResult:
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


async def _guarded(tool: Tool, session: AsyncSession, arguments: Mapping[str, Any]) -> ToolResult:
    """Держит обещание «Инструмент не бросает» даже когда падает не он сам.

    Оборвавшееся соединение с БД или таймаут запроса — не повод убивать ход агента: модель
    получает структурный отказ и может ответить пользователю тем, что уже знает.
    """
    try:
        return await tool.run(session, arguments)
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
)

TOOLS: Final = (GET_PLAYER_STATS,)
TOOLS_BY_NAME: Final = {tool.name: tool for tool in TOOLS}

DEFINITIONS: Final = [tool.definition() for tool in TOOLS]


async def execute_tool(
    session: AsyncSession, *, name: str, arguments: Mapping[str, Any] | None
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
    return await _guarded(tool, session, arguments)


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
