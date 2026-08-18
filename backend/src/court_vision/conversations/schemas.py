"""Wire-модели Диалогов, включая типы событий SSE.

События потока описаны здесь pydantic-моделями, а не «строкой в документации»: из них
собирается OpenAPI, а из OpenAPI генерируются TS-типы фронтенда (`openapi-typescript`).
Руками их на фронте не пишут — иначе контракт потока живёт в двух местах и расходится
молча.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel

from court_vision.conversations.models import MessageRole


class ConversationCreate(BaseModel):
    """Заголовок можно не присылать: он подставится из первого вопроса."""

    title: str | None = Field(default=None, min_length=1, max_length=128)


class ConversationSummary(BaseModel):
    """Строка списка Диалогов."""

    conversation_id: UUID
    title: str
    created_at: datetime


class MessageCreate(BaseModel):
    """Вопрос пользователя. Верхняя граница длины есть, потому что весь Диалог целиком
    уезжает в контекст модели на каждом ходу."""

    content: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    """Реплика Диалога."""

    message_id: int
    role: MessageRole
    content: str
    created_at: datetime


class Problem(BaseModel):
    """Тело ошибки по RFC 9457 — тот же формат, что у обычных ответов API.

    Один формат ошибки на HTTP-ответ и на событие `error`: иначе фронт разбирает ошибку
    запроса и ошибку внутри потока двумя разными способами.
    """

    # Расширяющие члены Problem Details (`model`, `cause`, `candidates`) — часть формата,
    # и именно по ним вызывающая сторона понимает детали.
    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    instance: str


class TokenEvent(BaseModel):
    """Очередной фрагмент текста ответа. Клиент дописывает его в конец — не заменяет."""

    type: Literal["token"] = "token"
    text: str


class ToolCallEvent(BaseModel):
    """Агент пошёл за данными. Приходит до выполнения Инструмента: смысл события — показать,
    чем занят Агент, пока ответа ещё нет."""

    type: Literal["tool_call"] = "tool_call"
    # Идентификатор вызова от провайдера. Нужен клиенту, чтобы не дублировать карточку при
    # повторной подписке, а в спринте 2 — чтобы цитата указывала на конкретный вызов.
    tool_call_id: str
    name: str
    # None, если модель прислала аргументы не JSON-объектом — сам вызов при этом состоялся
    # и в потоке виден.
    arguments: dict[str, Any] | None = None


class CitationEvent(BaseModel):
    """Источник, на который опирается ответ.

    Объявлено сейчас, наполняется в спринте 2 вместе с RAG: контракт потока не должен
    меняться от появления цитат — фронт, сгенерированные типы и парсер событий переживают
    это без правок.
    """

    type: Literal["citation"] = "citation"
    chunk_id: str
    document_title: str
    snippet: str
    source_url: str | None = None


class DoneEvent(BaseModel):
    """Ответ дописан и сохранён. `message_id` — идентификатор сохранённой Реплики."""

    type: Literal["done"] = "done"
    message_id: int


class ErrorEvent(BaseModel):
    """Ответ не удался. Приходит вместо `done` — поток закрывается штатно.

    Обрыв соединения вместо этого события клиент не отличил бы от сетевой проблемы и показал
    бы «переподключаемся» там, где повторять нечего.
    """

    type: Literal["error"] = "error"
    problem: Problem


class ChatStreamEvent(
    RootModel[
        Annotated[
            TokenEvent | ToolCallEvent | CitationEvent | DoneEvent | ErrorEvent,
            Field(discriminator="type"),
        ]
    ]
):
    """Событие SSE-потока ответа. Дискриминатор — поле `type`.

    Модель существует ради контракта: в OpenAPI она даёт один тип-объединение, из которого
    генератор TS выводит исчерпывающий разбор событий по `type`.
    """
