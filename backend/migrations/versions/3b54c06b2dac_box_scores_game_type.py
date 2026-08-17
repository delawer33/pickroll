"""box_scores.game_type

Revision ID: 3b54c06b2dac
Revises: 5cf91f59e7f8
Create Date: 2026-08-17 11:26:00.879982

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b54c06b2dac"
down_revision: str | Sequence[str] | None = "5cf91f59e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("box_scores", sa.Column("game_type", sa.String(length=32), nullable=True))
    # Уже загруженные строки берут тип из своего матча — пересеивать данные не нужно.
    op.execute(
        "UPDATE box_scores b SET game_type = g.game_type FROM games g WHERE g.id = b.game_id"
    )
    op.alter_column("box_scores", "game_type", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("box_scores", "game_type")
