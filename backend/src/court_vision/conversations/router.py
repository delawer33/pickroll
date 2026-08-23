"""HTTP-поверхность Диалогов: четыре эндпоинта, включая SSE-поток ответа.

Роутер тонкий: чтение и запись — в `service.py`, ответ агента — в `streaming.py`.
"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.sse import EventSourceResponse

from court_vision.agent.loop import Turn
from court_vision.auth.dependencies import CurrentUserId
from court_vision.config import Settings
from court_vision.conversations.schemas import (
    ChatStreamEvent,
    ConversationCreate,
    ConversationSummary,
    MessageCreate,
    MessageResponse,
)
from court_vision.conversations.service import (
    create_conversation,
    get_owned_conversation,
    list_conversations,
    list_messages,
)
from court_vision.conversations.streaming import accept_question, reply_events
from court_vision.db import SessionDep, SessionFactoryDep
from court_vision.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page

router = APIRouter(prefix="/conversations", tags=["conversations"])

ConversationId = Annotated[UUID, Path(description="Идентификатор Диалога.")]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Размер страницы.")]
# Пустая строка приравнивается к отсутствию курсора: сгенерированный клиент, подставляющий
# `?cursor=` для пустого `next_cursor`, иначе получал бы 400 на первой же странице.
Cursor = Annotated[
    str | None,
    Query(description="Курсор из `next_cursor` предыдущей страницы. Опаковый — не разбирать."),
]


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create(
    session: SessionDep, user_id: CurrentUserId, payload: ConversationCreate
) -> ConversationSummary:
    """Заводит Диалог. Без заголовка он подставится из первого вопроса."""
    return await create_conversation(session, clerk_user_id=user_id, title=payload.title)


@router.get("", response_model=Page[ConversationSummary])
async def index(
    session: SessionDep,
    user_id: CurrentUserId,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: Cursor = None,
) -> Page[ConversationSummary]:
    """Диалоги пользователя, свежие сверху."""
    return await list_conversations(
        session, clerk_user_id=user_id, limit=limit, cursor=cursor or None
    )


@router.get("/{conversation_id}/messages", response_model=Page[MessageResponse])
async def messages(
    session: SessionDep,
    user_id: CurrentUserId,
    conversation_id: ConversationId,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: Cursor = None,
) -> Page[MessageResponse]:
    """Реплики Диалога в порядке появления. Чужой Диалог — 404."""
    conversation = await get_owned_conversation(
        session, conversation_id=conversation_id, clerk_user_id=user_id
    )
    return await list_messages(
        session, conversation=conversation, limit=limit, cursor=cursor or None
    )


async def _accepted_question(
    session_factory: SessionFactoryDep,
    user_id: CurrentUserId,
    conversation_id: ConversationId,
    payload: MessageCreate,
) -> list[Turn]:
    """Проверяет владение и сохраняет вопрос — до того, как начнётся поток.

    Именно зависимость, а не первые строки генератора: зависимости выполняются до отправки
    ответа, поэтому чужой Диалог и негодное тело запроса уходят обычным Problem Details со
    статусом. Внутри генератора эти же ошибки застали бы уже отданные заголовки `200 OK` и
    выглядели бы для клиента как обрыв связи.
    """
    return await accept_question(
        session_factory,
        conversation_id=conversation_id,
        clerk_user_id=user_id,
        question=payload.content,
    )


@router.post(
    "/{conversation_id}/messages",
    response_class=EventSourceResponse,
    summary="Задать вопрос и получить ответ агента потоком",
)
async def send_message(
    request: Request,
    session_factory: SessionFactoryDep,
    user_id: CurrentUserId,
    conversation_id: ConversationId,
    history: Annotated[list[Turn], Depends(_accepted_question)],
) -> AsyncIterator[ChatStreamEvent]:
    """Стримит ответ агента событиями SSE: `token`, `tool_call`, `tool_result`, `citation`,
    `done`, `error`.

    Сессия запроса здесь не берётся намеренно: тело потока генерируется после возврата из
    обработчика, и транзакция запроса висела бы открытой всё время ответа модели.
    """
    settings: Settings = request.app.state.settings
    async for event in reply_events(
        session_factory,
        conversation_id=conversation_id,
        clerk_user_id=user_id,
        history=history,
        settings=settings,
        instance=request.url.path,
    ):
        yield event
