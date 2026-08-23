"""Ответ агента как SSE-поток.

Здесь сходятся три вещи: агентный цикл, сохранение Реплик и формат событий. Транзакциями
модуль владеет сам — тело SSE-ответа генерируется уже после того, как обработчик вернул
ответ, и транзакция запроса висела бы открытой всё время, пока отвечает модель
(исключение из общего правила, см. docs/conventions.md).

Порядок намеренный: вопрос пользователя сохраняется и коммитится до первого события потока.
Иначе оборванное на середине соединение уносило бы вопрос с собой, и Диалог оставался бы с
ответом без вопроса.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.loop import (
    AgentAnswer,
    TokenChunk,
    ToolCalled,
    ToolResulted,
    Turn,
    stream_answer,
)
from court_vision.config import Settings
from court_vision.conversations.schemas import (
    ChatStreamEvent,
    DoneEvent,
    ErrorEvent,
    Problem,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from court_vision.conversations.service import (
    append_message,
    conversation_history,
    get_owned_conversation,
    title_from_question,
)
from court_vision.errors import DomainError
from court_vision.problems import INTERNAL_PROBLEM, domain_problem, problem_body

log = structlog.get_logger(__name__)


async def accept_question(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: UUID,
    clerk_user_id: str,
    question: str,
) -> list[Turn]:
    """Сохраняет вопрос и возвращает историю Диалога целиком, включая его.

    Всё в одной короткой транзакции: проверка владения, запись Реплики и чтение истории —
    читать историю отдельным запросом после коммита значило бы отдать агенту переписку, в
    которой уже нет только что заданного вопроса.

    Чужой Диалог падает `ConversationNotFound` здесь, до начала потока: пока ответа ещё нет,
    ошибка уходит обычным Problem Details со статусом, а не событием внутри `200 OK`.
    """
    async with session_factory() as session:
        conversation = await get_owned_conversation(
            session, conversation_id=conversation_id, clerk_user_id=clerk_user_id
        )
        await append_message(
            session, conversation_id=conversation_id, role="user", content=question
        )
        if conversation.title is None:
            conversation.title = title_from_question(question)
        history = await conversation_history(session, conversation_id=conversation_id)
        await session.commit()
    return history


async def reply_events(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: UUID,
    clerk_user_id: str,
    history: list[Turn],
    settings: Settings,
    instance: str,
) -> AsyncIterator[ChatStreamEvent]:
    """События ответа: `token` по мере генерации, `tool_call` перед походом за данными и
    `tool_result` после него, `done` после сохранения ответа — либо `error` вместо всего
    остального.

    Любая ошибка становится событием, а не обрывом соединения: заголовки ответа ушли ещё до
    первого токена, статус уже `200`, и оборванный поток клиент не отличил бы от сетевого
    сбоя — показал бы «переподключаемся» там, где повторять нечего.
    """
    try:
        async for event in stream_answer(
            session_factory,
            history=history,
            settings=settings,
            trace_metadata=_trace_metadata(
                conversation_id=conversation_id, clerk_user_id=clerk_user_id
            ),
        ):
            match event:
                case TokenChunk(text=text):
                    yield _event(TokenEvent(text=text))
                case ToolCalled(id=call_id, name=name, arguments=arguments):
                    yield _event(
                        ToolCallEvent(tool_call_id=call_id, name=name, arguments=arguments)
                    )
                case ToolResulted(id=call_id, duration_ms=duration_ms, failure=failure, view=view):
                    yield _event(
                        ToolResultEvent(
                            tool_call_id=call_id,
                            duration_ms=duration_ms,
                            error=failure,
                            view=view,
                        )
                    )
                case AgentAnswer(text=text):
                    message_id = await _save_answer(
                        session_factory, conversation_id=conversation_id, text=text
                    )
                    yield _event(DoneEvent(message_id=message_id))
    except DomainError as error:
        log.warning(
            "conversation.reply_failed",
            conversation_id=str(conversation_id),
            problem_type=error.problem_type,
        )
        yield _event(ErrorEvent(problem=_problem(domain_problem(error, instance=instance))))
    except Exception:
        log.exception("conversation.reply_crashed", conversation_id=str(conversation_id))
        yield _event(
            ErrorEvent(
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


async def _save_answer(
    session_factory: async_sessionmaker[AsyncSession], *, conversation_id: UUID, text: str
) -> int:
    """Ответ агента — Реплика Диалога. Своя транзакция: вопрос закоммичен задолго до этого,
    и одной транзакции на обоих не бывает по построению."""
    async with session_factory() as session:
        message = await append_message(
            session, conversation_id=conversation_id, role="assistant", content=text
        )
        await session.commit()
        return message.id


def _event(
    event: TokenEvent | ToolCallEvent | ToolResultEvent | DoneEvent | ErrorEvent,
) -> ChatStreamEvent:
    """Оборачивает событие в тип-объединение потока — тот же, что описан в OpenAPI.

    Имени события (`event:` в SSE) у фреймов нет намеренно: тип лежит внутри JSON полем
    `type`. Клиент читает поток через `fetch` — `EventSource` не умеет посылать заголовок
    `Authorization` — и разбирает события по одному и тому же полю и в потоке, и в
    сохранённой истории.
    """
    return ChatStreamEvent(event)


def _problem(body: dict[str, Any]) -> Problem:
    return Problem.model_validate(body)


def _trace_metadata(*, conversation_id: UUID, clerk_user_id: str) -> dict[str, Any]:
    """Метаданные трейса для Langfuse: ответы одного Диалога собираются в одну сессию.

    Без них трейсы — плоская россыпь вызовов модели, по которой нельзя посмотреть разговор
    целиком, а именно за этим в трейсы и идут.
    """
    return {"session_id": str(conversation_id), "trace_user_id": clerk_user_id}
