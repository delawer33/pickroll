"""initial schema: teams, players, games, box_scores

Revision ID: 5cf91f59e7f8
Revises:
Create Date: 2026-08-17 10:46:07.305813

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5cf91f59e7f8"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # pg_trgm нужен фаззи-резолюции имени игрока (см. модуль players).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "players",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("first_name", sa.String(length=64), nullable=False),
        sa.Column("last_name", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("height_inches", sa.Integer(), nullable=True),
        sa.Column("weight_pounds", sa.Integer(), nullable=True),
        sa.Column("position", sa.String(length=8), nullable=True),
        sa.Column("draft_year", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_players_full_name"), "players", ["full_name"], unique=False)
    op.create_index(
        "ix_players_full_name_trgm",
        "players",
        ["full_name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"full_name": "gin_trgm_ops"},
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("city", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("abbreviation", sa.String(length=8), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "games",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("game_type", sa.String(length=32), nullable=False),
        sa.Column("home_team_id", sa.BigInteger(), nullable=False),
        sa.Column("away_team_id", sa.BigInteger(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("arena_name", sa.String(length=128), nullable=True),
        sa.Column("attendance", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["away_team_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["home_team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_games_game_date"), "games", ["game_date"], unique=False)
    op.create_index(op.f("ix_games_season"), "games", ["season"], unique=False)
    op.create_table(
        "box_scores",
        sa.Column("game_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("opponent_team_id", sa.BigInteger(), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("is_win", sa.Boolean(), nullable=False),
        sa.Column("starting_position", sa.String(length=8), nullable=True),
        sa.Column("minutes", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("assists", sa.Integer(), nullable=False),
        sa.Column("rebounds_total", sa.Integer(), nullable=False),
        sa.Column("rebounds_offensive", sa.Integer(), nullable=False),
        sa.Column("rebounds_defensive", sa.Integer(), nullable=False),
        sa.Column("steals", sa.Integer(), nullable=False),
        sa.Column("blocks", sa.Integer(), nullable=False),
        sa.Column("turnovers", sa.Integer(), nullable=False),
        sa.Column("fouls_personal", sa.Integer(), nullable=False),
        sa.Column("field_goals_made", sa.Integer(), nullable=False),
        sa.Column("field_goals_attempted", sa.Integer(), nullable=False),
        sa.Column("three_pointers_made", sa.Integer(), nullable=False),
        sa.Column("three_pointers_attempted", sa.Integer(), nullable=False),
        sa.Column("free_throws_made", sa.Integer(), nullable=False),
        sa.Column("free_throws_attempted", sa.Integer(), nullable=False),
        sa.Column("plus_minus", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
        ),
        sa.ForeignKeyConstraint(
            ["opponent_team_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("game_id", "player_id"),
    )
    op.create_index(
        "ix_box_scores_player_season", "box_scores", ["player_id", "season"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_box_scores_player_season", table_name="box_scores")
    op.drop_table("box_scores")
    op.drop_index(op.f("ix_games_season"), table_name="games")
    op.drop_index(op.f("ix_games_game_date"), table_name="games")
    op.drop_table("games")
    op.drop_table("teams")
    op.drop_index("ix_players_full_name_trgm", table_name="players")
    op.drop_index(op.f("ix_players_full_name"), table_name="players")
    op.drop_table("players")
