"""Хранилище Скаутинг-отчётов.

Отчёт принадлежит пользователю, который его заказал, и неизменяем: новый взгляд на того же
игрока — новая строка. Неизменяемость держит не только отсутствие эндпоинтов правки, но и
триггер в миграции: UPDATE по таблице отвергается самой базой.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from court_vision.corpus.models import EMBEDDING_DIMENSIONS
from court_vision.db import Base


class ScoutingReport(Base):
    """Скаутинг-отчёт: игрок в сезоне, содержимое по фиксированной схеме и эмбеддинг резюме.

    Ключ — UUID, а не последовательность: идентификатор торчит в URL (`/reports/{id}`), и по
    инкрементному ключу чужие отчёты перебирались бы простым `id - 1`.
    """

    __tablename__ = "scouting_reports"
    __table_args__ = (
        # Опора списка своих отчётов: keyset-пагинация идёт ровно по этому порядку.
        Index("ix_scouting_reports_owner_created_at", "clerk_user_id", "created_at", "id"),
        # Опора фильтра «отчёты по игроку» и поиска внутри него.
        Index("ix_scouting_reports_owner_player", "clerk_user_id", "player_id"),
    )
    # Серверные значения приезжают с INSERT через RETURNING, см. conversations/models.py.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Пользователь — опаковая строка Clerk (ADR-0002), внешнего ключа нет.
    clerk_user_id: Mapped[str] = mapped_column(String(64))
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"))
    season: Mapped[str] = mapped_column(String(7))
    # Валидированное схемой содержимое (`ScoutingReportContent`), как оно было сохранено.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Версия схемы содержимого: отчёты неизменны и переживут схему, прочитать старый отчёт
    # новым кодом можно, только зная, по какой схеме он записан.
    schema_version: Mapped[int] = mapped_column(Integer)
    # Модель-генератор: строка litellm, как в `llm_usage.model`.
    model: Mapped[str] = mapped_column(String(128))
    # Эмбеддинг резюме для `search_reports`. Размерность — та же, что у корпуса: один
    # эмбеддер на проект, см. `settings.embedding_model`.
    summary_embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
