"""box_scores: эра-зависимые метрики становятся NULLable

Revision ID: b3f8a2d4c9e1
Revises: c1a4f7e2b830
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f8a2d4c9e1"
down_revision: str | Sequence[str] | None = "d4f8b2c7e915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Лига начала считать эти метрики не с основания (перехваты/блоки/подборы по типам —
# 1973-74, потери — 1977-78, трёхочковые — 1979-80): до сезона введения в колонке NULL,
# а не ноль. `minutes` NULLable с исходной схемы.
ERA_DEPENDENT_COLUMNS = (
    "rebounds_offensive",
    "rebounds_defensive",
    "steals",
    "blocks",
    "turnovers",
    "three_pointers_made",
    "three_pointers_attempted",
)


def upgrade() -> None:
    """Upgrade schema."""
    for column in ERA_DEPENDENT_COLUMNS:
        op.alter_column("box_scores", column, nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Старая схема хранила ноль там, где метрика не считалась, — возвращаемся к нему.
    for column in ERA_DEPENDENT_COLUMNS:
        op.execute(f"UPDATE box_scores SET {column} = 0 WHERE {column} IS NULL")
        op.alter_column("box_scores", column, nullable=False)
