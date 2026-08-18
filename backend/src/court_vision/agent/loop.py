"""Агентный цикл — смысловое ядро проекта (ADR-0001).

Цикл простой и полностью наш: история → модель → если пришли tool-call'ы, выполнить их и
вернуть результаты в модель → повторять, пока не придёт ответ без вызовов. Никакого
фреймворка над этим нет — именно здесь живут решения о шагах, об учёте и о том, что
считать ошибкой.

Цикл стриминговый: наружу он отдаёт события по мере их появления, а не готовый ответ.
Иначе пользователь ждёт молча всё время, пока модель думает и ходит за данными.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent import tools as tool_registry
from court_vision.agent.errors import AgentStepLimitReached, IncompleteAnswer
from court_vision.agent.prompts import load_prompt
from court_vision.agent.transport import LlmReply, TextDelta, ToolCall, stream
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
    """Итог одного ответа агента — последнее событие потока.

    `text` равен склейке всех отданных до него `TokenChunk`: именно этот текст сохраняется
    Репликой, поэтому перезагрузка страницы показывает ровно то, что пользователь читал в
    потоке, а не сокращённую версию.
    """

    text: str
    tool_invocations: tuple[ToolInvocation, ...]


@dataclass(frozen=True, slots=True)
class TokenChunk:
    """Фрагмент текста ответа."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCalled:
    """Агент решил вызвать Инструмент. Отдаётся до выполнения: смысл события — показать,
    чем занят агент, пока ответа ещё нет."""

    name: str
    arguments: dict[str, Any] | None


AgentEvent = TokenChunk | ToolCalled | AgentAnswer

# Разделитель между текстовыми блоками разных шагов: модель нередко говорит «сейчас
# посмотрю» до вызова Инструмента, и без пустой строки преамбула склеилась бы с ответом.
_STEP_SEPARATOR = "\n\n"


async def stream_answer(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    history: Sequence[Turn],
    settings: Settings,
    trace_metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[AgentEvent]:
    """Прогоняет цикл, отдавая события по мере их появления; последнее — `AgentAnswer`.

    `history` — вся переписка Диалога целиком, включая текущий вопрос: агент отвечает с
    учётом контекста, поэтому follow-up «а в 2015-16?» работает без повтора имени игрока.

    На входе фабрика сессий, а не сессия: ответ агента длится столько, сколько отвечает
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
    blocks: list[str] = []

    for step in range(settings.llm_max_steps):
        reply: LlmReply | None = None
        spoken: list[str] = []
        # aclosing: без него брошенный снаружи цикл не закрывал бы поток провайдера — тот
        # остался бы висеть на своём yield вместе с открытым соединением.
        async with aclosing(
            _step(
                session_factory,
                messages=messages,
                settings=settings,
                trace_metadata=trace_metadata,
            )
        ) as parts:
            async for part in parts:
                if isinstance(part, TextDelta):
                    if not spoken and blocks:
                        yield TokenChunk(_STEP_SEPARATOR)
                        blocks.append(_STEP_SEPARATOR)
                    spoken.append(part.text)
                    yield TokenChunk(part.text)
                else:
                    reply = part
        if reply is None:
            # Транспорт заканчивает поток ровно одним LlmReply — либо бросает доменную
            # ошибку. Проверка, а не assert: под `-O` assert исчезает, и на его месте
            # оказался бы AttributeError вместо доменной ошибки.
            raise IncompleteAnswer("no_reply")
        blocks.extend(spoken)
        messages.append(reply.message)

        if not reply.tool_calls:
            log.info("agent.answered", steps=step + 1, tool_calls=len(invocations))
            yield AgentAnswer(
                text=_final_text(reply, spoken=spoken, blocks=blocks),
                tool_invocations=tuple(invocations),
            )
            return

        for call in reply.tool_calls:
            yield ToolCalled(name=call.name, arguments=call.parsed_arguments())
            invocation = await _invoke(session_factory, call)
            invocations.append(invocation)
            messages.append(_tool_message(call, invocation.result))

    # Потолок шагов — защита от цикла, в котором модель бесконечно перевызывает Инструмент.
    log.warning("agent.step_limit_reached", max_steps=settings.llm_max_steps)
    raise AgentStepLimitReached(settings.llm_max_steps)


def _step(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    messages: list[dict[str, Any]],
    settings: Settings,
    trace_metadata: dict[str, Any] | None,
) -> AsyncGenerator[TextDelta | LlmReply]:
    return stream(
        session_factory,
        model=settings.llm_model,
        messages=messages,
        tools=tool_registry.DEFINITIONS,
        timeout=settings.llm_timeout_seconds,
        operation=OPERATION,
        trace_metadata=trace_metadata,
    )


def _final_text(reply: LlmReply, *, spoken: Sequence[str], blocks: Sequence[str]) -> str:
    """Сказанное годится в ответ пользователю, только если оно законченное.

    На пустоту проверяется последний шаг (`spoken`), а не всё сказанное (`blocks`): модель
    могла сказать «сейчас посмотрю», сходить за данными и вернуться ни с чем — тогда
    преамбула без единой цифры уехала бы пользователю как готовый ответ.
    """
    if reply.finish_reason == "length":
        log.warning("agent.answer_truncated")
        raise IncompleteAnswer("truncated")
    if not "".join(spoken).strip():
        log.warning("agent.answer_empty")
        raise IncompleteAnswer("empty")
    return "".join(blocks)


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
