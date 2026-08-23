"""message citations

Revision ID: d4f8b2c7e915
Revises: a7c3e9d41f28
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4f8b2c7e915"
down_revision: str | Sequence[str] | None = "a7c3e9d41f28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Цитаты ответа Агента — JSONB рядом с Репликой, а не отдельная таблица: читаются и
    # пишутся только вместе с ней. NULL — у реплик пользователя и ответов без корпуса.
    op.add_column(
        "messages", sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "citations")
