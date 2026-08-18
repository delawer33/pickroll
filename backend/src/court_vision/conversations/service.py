"""Чтение и запись Диалогов.

Сервис не коммитит: unit of work равен HTTP-запросу, граница транзакции — в `db.py`.
Исключение — ответ агента (`streaming.py`): он длится дольше запроса и владеет своими
короткими транзакциями сам.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.agent.loop import Turn
from court_vision.conversations.errors import ConversationNotFound
from court_vision.conversations.models import Conversation, Message, MessageRole
from court_vision.conversations.schemas import ConversationSummary, MessageResponse
from court_vision.pagination import InvalidCursor, Page, decode_cursor, encode_cursor

# Заголовок Диалога, пока его не задали: в БД лежит NULL, наружу отдаётся эта строка.
TITLE_MAX_LENGTH = 128
FALLBACK_TITLE = "Новый диалог"

# Границы bigint. Идентификатор из курсора уезжает в SQL, и число за пределами типа роняет
# запрос на уровне драйвера — то есть 500 вместо «курсор нечитаем».
_BIGINT_RANGE = range(1, 2**63)


async def create_conversation(
    session: AsyncSession, *, clerk_user_id: str, title: str | None
) -> ConversationSummary:
    """Заводит пустой Диалог. Без заголовка его подставит первый вопрос."""
    conversation = Conversation(clerk_user_id=clerk_user_id, title=title)
    session.add(conversation)
    # flush, а не commit: коммитит граница запроса, но идентификатор и метка времени нужны
    # уже здесь — из них собирается ответ. Серверные значения приезжают вместе с INSERT
    # (`eager_defaults` на модели), поэтому отдельного SELECT не требуется.
    await session.flush()
    return summary(conversation)


async def get_owned_conversation(
    session: AsyncSession, *, conversation_id: UUID, clerk_user_id: str
) -> Conversation:
    """Диалог по идентификатору — только свой.

    Владение проверяется здесь, одним условием запроса, а не отдельной проверкой после
    выборки: забыть добавить `and` к существующему запросу труднее, чем забыть вызвать
    проверку.
    """
    statement = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.clerk_user_id == clerk_user_id
    )
    conversation = (await session.execute(statement)).scalar_one_or_none()
    if conversation is None:
        raise ConversationNotFound()
    return conversation


async def list_conversations(
    session: AsyncSession, *, clerk_user_id: str, limit: int, cursor: str | None
) -> Page[ConversationSummary]:
    """Диалоги пользователя, свежие сверху.

    Ключ сортировки — `(created_at, id)`, обе колонки неизменны. Сортировка по времени
    последней активности была бы удобнее в списке, но ключ keyset-пагинации обязан быть
    неподвижным: Диалог, поднявшийся вверх между двумя запросами, увёл бы за собой границу
    страницы, и часть списка клиент бы не увидел.
    """
    statement = (
        select(Conversation)
        .where(Conversation.clerk_user_id == clerk_user_id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
    )
    if cursor is not None:
        created_at, conversation_id = _conversation_cursor(cursor)
        statement = statement.where(
            tuple_(Conversation.created_at, Conversation.id) < (created_at, conversation_id)
        )

    conversations, next_cursor = await _page(
        session,
        statement,
        limit=limit,
        cursor_of=lambda item: encode_cursor(item.created_at.isoformat(), item.id),
    )
    return Page(items=[summary(item) for item in conversations], next_cursor=next_cursor)


async def list_messages(
    session: AsyncSession, *, conversation: Conversation, limit: int, cursor: str | None
) -> Page[MessageResponse]:
    """Реплики Диалога в порядке появления.

    На входе сам Диалог, а не его идентификатор: получить объект можно только через
    `get_owned_conversation`, то есть проверив владение, — забыть проверку тут попросту не
    выражается.

    Порядок прямой, а не «свежие сверху»: Реплики читают как переписку, сверху вниз, и
    первая страница — это начало разговора. Ключ — монотонный `id`.
    """
    statement = (
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id.asc())
    )
    if cursor is not None:
        statement = statement.where(Message.id > _message_cursor(cursor))

    messages, next_cursor = await _page(
        session, statement, limit=limit, cursor_of=lambda item: encode_cursor(item.id)
    )
    return Page(items=[_message(item) for item in messages], next_cursor=next_cursor)


async def conversation_history(session: AsyncSession, *, conversation_id: UUID) -> list[Turn]:
    """Вся переписка Диалога на вход агенту.

    Целиком, без окна: follow-up «а в 2015-16?» осмыслен только вместе с вопросом, в котором
    было названо имя игрока. Ограничение длины Диалога — задача сжатия истории, и появится
    оно тогда, когда контекст начнёт упираться в потолок, а не сейчас.
    """
    statement = (
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
    )
    result = await session.execute(statement)
    return [Turn(role=row.role, content=row.content) for row in result]


async def append_message(
    session: AsyncSession, *, conversation_id: UUID, role: MessageRole, content: str
) -> Message:
    """Дописывает Реплику в Диалог."""
    message = Message(conversation_id=conversation_id, role=role, content=content)
    session.add(message)
    await session.flush()
    return message


def title_from_question(question: str) -> str:
    """Заголовок из первого вопроса: обрезанный по границе слова, а не по символу.

    Заголовок нужен списку Диалогов сразу — иначе там столбик одинаковых «Новых диалогов»,
    в котором свой Диалог не найти.
    """
    collapsed = " ".join(question.split())
    if len(collapsed) <= TITLE_MAX_LENGTH:
        return collapsed or FALLBACK_TITLE
    head, _, _ = collapsed[: TITLE_MAX_LENGTH - 1].rpartition(" ")
    return f"{head or collapsed[: TITLE_MAX_LENGTH - 1]}…"


def summary(conversation: Conversation) -> ConversationSummary:
    """Заголовок NULL означает «ещё не задан» — наружу вместо него уезжает заглушка."""
    return ConversationSummary(
        conversation_id=conversation.id,
        title=conversation.title or FALLBACK_TITLE,
        created_at=conversation.created_at,
    )


async def _page[ItemT](
    session: AsyncSession,
    statement: Select[tuple[ItemT]],
    *,
    limit: int,
    cursor_of: Callable[[ItemT], str],
) -> tuple[list[ItemT], str | None]:
    """Забирает страницу и решает, есть ли следующая.

    Запрашивается на один элемент больше, чем нужно: лишний элемент и есть ответ на вопрос
    «есть ли что-то дальше» — второй COUNT-запрос для этого не нужен, а курсор в последней
    странице должен быть пустым, иначе клиент делает лишний круг за пустотой.
    """
    if limit <= 0:
        return [], None
    result = await session.execute(statement.limit(limit + 1))
    items: list[ItemT] = list(result.scalars())
    if len(items) <= limit:
        return items, None
    page = items[:limit]
    return page, cursor_of(page[-1])


def _conversation_cursor(cursor: str) -> tuple[datetime, UUID]:
    created_at, conversation_id = decode_cursor(cursor, parts=2)
    try:
        parsed = datetime.fromisoformat(created_at)
        identifier = UUID(conversation_id)
    except ValueError as error:
        raise InvalidCursor() from error
    if parsed.tzinfo is None:
        # Метку без зоны драйвер истолковал бы по часам процесса, и граница страницы молча
        # сдвинулась бы на смещение — часть Диалогов клиент не увидел бы вовсе.
        raise InvalidCursor()
    return parsed, identifier


def _message_cursor(cursor: str) -> int:
    (message_id,) = decode_cursor(cursor, parts=1)
    try:
        parsed = int(message_id)
    except ValueError as error:
        raise InvalidCursor() from error
    if parsed not in _BIGINT_RANGE:
        raise InvalidCursor()
    return parsed


def _message(message: Message) -> MessageResponse:
    return MessageResponse(
        message_id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )
