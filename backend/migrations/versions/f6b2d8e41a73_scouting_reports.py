"""scouting reports

Revision ID: f6b2d8e41a73
Revises: e5a9c3d71b04
Create Date: 2026-10-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f6b2d8e41a73"
down_revision: str | Sequence[str] | None = "e5a9c3d71b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "scouting_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("clerk_user_id", sa.String(length=64), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("summary_embedding", Vector(768), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scouting_reports_owner_created_at",
        "scouting_reports",
        ["clerk_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_scouting_reports_owner_player", "scouting_reports", ["clerk_user_id", "player_id"]
    )
    # Отчёт неизменен (CONTEXT.md): правку отвергает сама база, а не только отсутствие
    # эндпоинта — иначе неизменность держалась бы на дисциплине каждого будущего запроса.
    op.execute(
        """
        CREATE FUNCTION scouting_reports_reject_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'scouting_reports are immutable: create a new report instead';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER scouting_reports_immutable BEFORE UPDATE ON scouting_reports "
        "FOR EACH ROW EXECUTE FUNCTION scouting_reports_reject_update()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER scouting_reports_immutable ON scouting_reports")
    op.execute("DROP FUNCTION scouting_reports_reject_update()")
    op.drop_index("ix_scouting_reports_owner_player", table_name="scouting_reports")
    op.drop_index("ix_scouting_reports_owner_created_at", table_name="scouting_reports")
    op.drop_table("scouting_reports")
