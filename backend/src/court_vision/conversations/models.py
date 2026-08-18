"""Хранилище Диалогов.

Диалог и его Реплики персистятся с самого начала, а не задним числом: пришить сохранение
позже — это миграция схемы плюс переделка агентного цикла, который к тому моменту уже
привык отвечать на одиночный вопрос.

В Диалоге лежат только Реплики (см. CONTEXT.md): вопрос пользователя и ответ Агента.
Внутренняя кухня ответа — системный промпт, вызовы Инструментов и их результаты — Репликой
не является и здесь не хранится; учёт вызовов модели ведёт `llm_usage`.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from court_vision.db import Base

# Роль Реплики. `tool` здесь нет намеренно: результаты Инструментов в Диалог не попадают.
MessageRole = Literal["user", "assistant"]


class Conversation(Base):
    """Диалог — многоходовая переписка одного пользователя с Агентом.

    Ключ — UUID, а не последовательность: идентификатор Диалога торчит в URL, и по
    инкрементному ключу чужие Диалоги перебирались бы простым `id - 1`. Владение всё равно
    проверяется на каждом запросе, но угадываемый идентификатор — лишний повод ошибиться.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        # Опора списка Диалогов: keyset-пагинация идёт ровно по этому порядку.
        Index("ix_conversations_owner_created_at", "clerk_user_id", "created_at", "id"),
    )
    # Серверные значения (`id`, `created_at`) приезжают вместе с INSERT через RETURNING —
    # иначе на каждую запись шёл бы второй запрос за тем, что БД уже посчитала.
    # Словарь конфигурации мапперу положено объявлять именно так: под ClassVar mypy видит
    # переопределение атрибута базового класса и ругается уже он — отсюда подавление.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Пользователь для системы — опаковая строка Clerk (ADR-0002); локальной таблицы
    # `users` нет, поэтому и внешнего ключа здесь нет.
    clerk_user_id: Mapped[str] = mapped_column(String(64))
    # NULL — «заголовок ещё не задан»: его подставит первый вопрос. Строкой-заглушкой это
    # выразить нельзя — пользователь вправе назвать Диалог ровно так же, и его заголовок
    # затёрло бы. Осмысленный заголовок от модели приезжает с полировкой UX.
    title: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    """Реплика Диалога — вопрос пользователя или ответ Агента.

    Ключ — Identity: Реплики читаются и пагинируются строго в порядке появления, а
    монотонный ключ и есть этот порядок. Сортировать по `created_at` было бы хуже: две
    Реплики одного хода пишутся в разных транзакциях и в принципе могут получить
    неразличимую метку времени.
    """

    __tablename__ = "messages"
    __table_args__ = (
        # Опора чтения Диалога: и полная история для Агента, и страница для UI идут по
        # (conversation_id, id).
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )
    # Словарь конфигурации мапперу положено объявлять именно так: под ClassVar mypy видит
    # переопределение атрибута базового класса и ругается уже он — отсюда подавление.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
