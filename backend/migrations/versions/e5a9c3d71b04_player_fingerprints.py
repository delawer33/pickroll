"""player fingerprints

Revision ID: e5a9c3d71b04
Revises: b3f8a2d4c9e1
Create Date: 2026-10-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5a9c3d71b04"
down_revision: str | Sequence[str] | None = "b3f8a2d4c9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "player_fingerprints",
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("embedding", Vector(9), nullable=False),
        sa.Column("z_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("player_id", "season"),
    )
    # HNSW по косинусу. Alembic не умеет описать операторный класс через create_index —
    # сырой DDL, как у индекса чанков.
    op.execute(
        "CREATE INDEX ix_player_fingerprints_embedding_hnsw "
        "ON player_fingerprints USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX ix_player_fingerprints_embedding_hnsw")
    op.drop_table("player_fingerprints")
