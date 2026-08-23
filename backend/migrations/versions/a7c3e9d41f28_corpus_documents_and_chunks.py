"""corpus documents and chunks

Revision ID: a7c3e9d41f28
Revises: c1a4f7e2b830
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7c3e9d41f28"
down_revision: str | Sequence[str] | None = "c1a4f7e2b830"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        # NULL — у документа нет стабильного внешнего адреса (кешированный файл).
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # Естественный ключ повторной выгрузки: она находит свой документ по нему.
        sa.UniqueConstraint("source_type", "title", name="uq_documents_source_type_title"),
    )
    op.create_index("ix_documents_source_type", "documents", ["source_type"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("locator", sa.String(length=256), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("embedding_model", sa.String(length=64), nullable=False),
        # Генерируется базой из text: полнотекстовый индекс не может разойтись с текстом.
        sa.Column(
            "ts",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        # CASCADE: переиндексация и удаление документа сносят его чанки одним запросом.
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    # HNSW по косинусу — векторная половина гибридного поиска. Alembic не умеет описать
    # операторный класс через create_index — сырой DDL.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index("ix_chunks_ts", "chunks", ["ts"], postgresql_using="gin")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chunks_ts", table_name="chunks")
    op.execute("DROP INDEX ix_chunks_embedding_hnsw")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_source_type", table_name="documents")
    op.drop_table("documents")
