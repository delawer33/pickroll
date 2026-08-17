"""Агентный цикл — смысловое ядро проекта (ADR-0001).

Цикл простой и полностью наш: история → модель → если пришли tool-call'ы, выполнить их и
вернуть результаты в модель → повторять, пока не придёт ответ без вызовов. Никакого
фреймворка над этим нет — именно здесь живут решения о шагах, об учёте и о том, что
считать ошибкой.

Стриминга здесь пока нет: SSE приезжает в #8 поверх этой же механики.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent import tools as tool_registry
from court_vision.agent.errors import AgentStepLimitReached, IncompleteAnswer
from court_vision.agent.prompts import load_prompt
from court_vision.agent.transport import LlmReply, ToolCall, complete
from court_vision.config import Settings

log = structlog.get_logger(__name__)

OPERATION = "agent.step"


@dataclass(frozen=True, slots=True)
class Turn:
    """Реплика Диалога на входе цикла. Внутреннюю кухню — системный промпт, tool-call'ы и
    их результаты — цикл собирает сам: хранилищу Диалогов (#8) о ней знать незачем."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Состоявшийся вызов Инструмента — материал для события `tool_call` в SSE и для отладки."""

    name: str
    arguments: dict[str, Any] | None
    result: tool_registry.ToolResult


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    """Итог одного хода агента."""

    text: str
    tool_invocations: tuple[ToolInvocation, ...]


async def answer(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    history: Sequence[Turn],
    settings: Settings,
) -> AgentAnswer:
    """Прогоняет цикл до финального ответа модели.

    `history` — вся переписка Диалога целиком, включая текущий вопрос: агент отвечает с
    учётом контекста, поэтому follow-up «а в 2015-16?» работает без повтора имени игрока.

    На входе фабрика сессий, а не сессия: ход агента длится столько, сколько отвечает
    провайдер, и держать всё это время открытую транзакцию запроса — значит занимать
    соединение пула минутами на каждый вопрос. Каждый вызов Инструмента и каждая строка
    учёта берут своё короткое соединение.
    """
    if not history:
        raise ValueError("История Диалога пуста: агенту нечего отвечать.")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": load_prompt("system")},
        *({"role": turn.role, "content": turn.content} for turn in history),
    ]
    invocations: list[ToolInvocation] = []

    for step in range(settings.llm_max_steps):
        reply = await _step(session_factory, messages=messages, settings=settings)
        messages.append(reply.message)

        if not reply.tool_calls:
            log.info("agent.answered", steps=step + 1, tool_calls=len(invocations))
            return AgentAnswer(text=_final_text(reply), tool_invocations=tuple(invocations))

        for call in reply.tool_calls:
            invocation = await _invoke(session_factory, call)
            invocations.append(invocation)
            messages.append(_tool_message(call, invocation.result))

    # Потолок шагов — защита от цикла, в котором модель бесконечно перевызывает Инструмент.
    log.warning("agent.step_limit_reached", max_steps=settings.llm_max_steps)
    raise AgentStepLimitReached(settings.llm_max_steps)


async def _step(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    messages: list[dict[str, Any]],
    settings: Settings,
) -> LlmReply:
    return await complete(
        session_factory,
        model=settings.llm_model,
        messages=messages,
        tools=tool_registry.DEFINITIONS,
        timeout=settings.llm_timeout_seconds,
        operation=OPERATION,
    )


def _final_text(reply: LlmReply) -> str:
    """Финальная реплика годится в ответ пользователю, только если она законченная."""
    if reply.finish_reason == "length":
        log.warning("agent.answer_truncated")
        raise IncompleteAnswer("truncated")
    if not reply.text.strip():
        log.warning("agent.answer_empty")
        raise IncompleteAnswer("empty")
    return reply.text


async def _invoke(
    session_factory: async_sessionmaker[AsyncSession], call: ToolCall
) -> ToolInvocation:
    arguments = call.parsed_arguments()
    log.info("agent.tool_call", tool=call.name, arguments=arguments)
    async with session_factory() as session:
        # Инструменты только читают — коммитить нечего; сессия закрывается сразу, чтобы
        # соединение не ждало следующего ответа модели.
        result = await tool_registry.execute_tool(session, name=call.name, arguments=arguments)
    return ToolInvocation(name=call.name, arguments=arguments, result=result)


def _tool_message(call: ToolCall, result: tool_registry.ToolResult) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        # ensure_ascii=False: экранированная кириллица раздувает промпт втрое на ровном месте.
        "content": json.dumps(result, ensure_ascii=False),
    }
