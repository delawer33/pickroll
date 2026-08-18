"""conversations and messages

Revision ID: c1a4f7e2b830
Revises: 9f3d1a7c05b2
Create Date: 2026-08-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c1a4f7e2b830"
down_revision: str | Sequence[str] | None = "9f3d1a7c05b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversations",
        # `gen_random_uuid()` — ядро Postgres с 13-й версии, расширение для него не нужно.
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("clerk_user_id", sa.String(length=64), nullable=False),
        # NULL = заголовок ещё не задан, его подставит первый вопрос.
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Список Диалогов пользователя с keyset-пагинацией идёт ровно по этому порядку колонок.
    op.create_index(
        "ix_conversations_owner_created_at",
        "conversations",
        ["clerk_user_id", "created_at", "id"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # CASCADE: Реплика без своего Диалога смысла не имеет, и удаление Диалога не должно
        # требовать двух запросов в правильном порядке.
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_messages_conversation_id_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_owner_created_at", table_name="conversations")
    op.drop_table("conversations")
