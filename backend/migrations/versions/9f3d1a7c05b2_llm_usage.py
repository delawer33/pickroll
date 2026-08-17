"""llm_usage

Revision ID: 9f3d1a7c05b2
Revises: 3b54c06b2dac
Create Date: 2026-08-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3d1a7c05b2"
down_revision: str | Sequence[str] | None = "3b54c06b2dac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("response_model", sa.String(length=128), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        # Точность под цену одного вызова: шесть знаков после запятой — это доли цента.
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Дашборд стоимости спрашивает «сколько потратили за период» и «на что» — обе выборки
    # идут по этим колонкам.
    op.create_index(op.f("ix_llm_usage_created_at"), "llm_usage", ["created_at"])
    op.create_index(op.f("ix_llm_usage_operation"), "llm_usage", ["operation"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_llm_usage_operation"), table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_created_at"), table_name="llm_usage")
    op.drop_table("llm_usage")
