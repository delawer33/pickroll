"""Документы корпуса и их чанки — хранилище RAG-индекса.

Документ — единица выгрузки и идемпотентности (по `content_hash`), чанк — единица
поиска: у него и вектор, и tsvector, и локатор для цитирования.
"""

from datetime import datetime
from typing import Literal

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, DateTime, ForeignKey, Identity, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from court_vision.db import Base

SourceType = Literal["rulebook", "wikipedia"]

# Размерность эмбеддинга зашита в тип колонки: pgvector требует фиксированную размерность
# для HNSW-индекса, а смена размерности — это в любом случае новая миграция и переиндексация.
EMBEDDING_DIMENSIONS = 768


class Document(Base):
    """Документ-источник: rulebook целиком или одна статья Wikipedia.

    `(source_type, title)` — естественный ключ: повторная выгрузка находит свой документ
    по нему, а `content_hash` решает, менять ли что-то. `url` — стабильный внешний
    идентификатор для цитат, не ключ: у выгруженного из кеша PDF он один на все версии.
    """

    __tablename__ = "documents"

    # Строка своя, не из данных NBA.com — ключ генерирует БД (identity, см. llm_usage).
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(256))
    url: Mapped[str | None] = mapped_column(String(512))
    # sha256 плоского текста документа — ключ идемпотентности: совпал — документ не трогаем.
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Chunk(Base):
    """Чанк документа — то, что находит поиск и цитирует агент."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # CASCADE: чанк без документа смысла не имеет, переиндексация удаляет их вместе.
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Человекочитаемый адрес внутри документа: "Rule 5 — Sec. II",
    # "Playing career > Chicago Bulls". Уезжает в цитату как есть.
    locator: Mapped[str] = mapped_column(String(256))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Каким эмбеддером посчитан вектор: индекс со смесью моделей неисправим поиском,
    # поэтому ingest отказывается писать чужой моделью без --reindex.
    embedding_model: Mapped[str] = mapped_column(String(64))
    # Генерируется базой из text: полнотекстовая половина гибридного поиска не может
    # разойтись с текстом чанка по построению.
    ts: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
