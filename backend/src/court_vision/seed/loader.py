"""Загрузка датасета в Postgres через COPY.

Идемпотентность: строки едут COPY-ем во временную таблицу-двойник, а оттуда одним
`INSERT ... ON CONFLICT DO UPDATE` в целевую. Повторный запуск обновляет строки на
месте и не создаёт дублей.

COPY, а не построчные INSERT: на сотнях тысяч строк box score это минуты против часа.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import asyncpg
import structlog

from court_vision.seed import rows as row_parsers
from court_vision.seed.source import (
    BOX_SCORES_FILE,
    GAMES_FILE,
    PLAYERS_FILE,
    TEAMS_FILE,
    DatasetSource,
    read_rows,
)

log = structlog.get_logger(__name__)

COPY_BATCH_SIZE: Final = 50_000

TEAM_COLUMNS: Final = ("id", "city", "name", "abbreviation")
PLAYER_COLUMNS: Final = (
    "id",
    "first_name",
    "last_name",
    "full_name",
    "birth_date",
    "country",
    "height_inches",
    "weight_pounds",
    "position",
    "draft_year",
)
GAME_COLUMNS: Final = (
    "id",
    "season",
    "game_date",
    "game_type",
    "home_team_id",
    "away_team_id",
    "home_score",
    "away_score",
    "arena_name",
    "attendance",
)
BOX_SCORE_COLUMNS: Final = (
    "game_id",
    "player_id",
    "season",
    "game_date",
    "game_type",
    "team_id",
    "opponent_team_id",
    "is_home",
    "is_win",
    "starting_position",
    "minutes",
    "points",
    "assists",
    "rebounds_total",
    "rebounds_offensive",
    "rebounds_defensive",
    "steals",
    "blocks",
    "turnovers",
    "fouls_personal",
    "field_goals_made",
    "field_goals_attempted",
    "three_pointers_made",
    "three_pointers_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "plus_minus",
    "comment",
)


@dataclass(frozen=True)
class SeedResult:
    teams: int
    players: int
    games: int
    box_scores: int


async def seed_database(connection: asyncpg.Connection[Any], source: DatasetSource) -> SeedResult:
    """Наполняет БД из директории с CSV-файлами датасета. Повторный запуск безопасен."""
    teams = await _load_teams(connection, source.path(TEAMS_FILE))
    players = await _load_players(connection, source.path(PLAYERS_FILE))
    games, game_ids = await _load_games(connection, source.path(GAMES_FILE))
    box_scores = await _load_box_scores(connection, source.path(BOX_SCORES_FILE), game_ids)

    result = SeedResult(teams=teams, players=players, games=games, box_scores=box_scores)
    log.info(
        "seed.done",
        teams=result.teams,
        players=result.players,
        games=result.games,
        box_scores=result.box_scores,
    )
    return result


async def _load_teams(connection: asyncpg.Connection[Any], path: Path) -> int:
    # Франшиза встречается в истории несколько раз (переезды, смена имени) — берём свежую запись.
    latest: dict[str, dict[str, str]] = {}
    for row in read_rows(path):
        team_id = row.get("teamId", "").strip()
        if not team_id:
            continue
        known = latest.get(team_id)
        if known is None or row_parsers.team_is_newer(row, known):
            latest[team_id] = row

    records = [parsed for row in latest.values() if (parsed := row_parsers.team_row(row))]
    return await _upsert(
        connection,
        table="teams",
        columns=TEAM_COLUMNS,
        conflict=("id",),
        records=records,
    )


async def _load_players(connection: asyncpg.Connection[Any], path: Path) -> int:
    records = (parsed for row in read_rows(path) if (parsed := row_parsers.player_row(row)))
    return await _upsert_batched(
        connection,
        table="players",
        columns=PLAYER_COLUMNS,
        conflict=("id",),
        records=records,
    )


async def _load_games(connection: asyncpg.Connection[Any], path: Path) -> tuple[int, set[int]]:
    """Грузит матчи Исторической эры и возвращает их идентификаторы.

    Идентификаторы нужны следующему шагу: строка box score, чей матч не попал в
    выборку, нарушила бы внешний ключ.
    """
    known_team_ids = await _existing_ids(connection, "teams")
    game_ids: set[int] = set()
    skipped: Counter[str] = Counter()

    def parsed_games() -> Iterator[row_parsers.GameRow]:
        for row in read_rows(path):
            game = row_parsers.game_row(row)
            if game is None:
                skipped["unparsed_or_before_era"] += 1
                continue
            if game[4] not in known_team_ids or game[5] not in known_team_ids:
                skipped["unknown_team"] += 1
                continue
            game_ids.add(game[0])
            yield game

    count = await _upsert_batched(
        connection,
        table="games",
        columns=GAME_COLUMNS,
        conflict=("id",),
        records=parsed_games(),
    )
    _log_skipped("games", skipped)
    return count, game_ids


async def _load_box_scores(
    connection: asyncpg.Connection[Any], path: Path, game_ids: set[int]
) -> int:
    known_player_ids = await _existing_ids(connection, "players")
    known_team_ids = await _existing_ids(connection, "teams")
    skipped: Counter[str] = Counter()

    def parsed_box_scores() -> Iterator[row_parsers.BoxScoreRow]:
        for row in read_rows(path):
            box_score = row_parsers.box_score_row(row)
            if box_score is None:
                skipped["unparsed"] += 1
                continue
            game_id, player_id = box_score[0], box_score[1]
            team_id, opponent_team_id = box_score[5], box_score[6]
            if game_id not in game_ids:
                skipped["unknown_game"] += 1
                continue
            if player_id not in known_player_ids:
                skipped["unknown_player"] += 1
                continue
            if team_id not in known_team_ids or opponent_team_id not in known_team_ids:
                skipped["unknown_team"] += 1
                continue
            yield box_score

    count = await _upsert_batched(
        connection,
        table="box_scores",
        columns=BOX_SCORE_COLUMNS,
        conflict=("game_id", "player_id"),
        records=parsed_box_scores(),
    )
    _log_skipped("box_scores", skipped)
    return count


def _log_skipped(table: str, skipped: Counter[str]) -> None:
    """Отброшенные строки — не молчаливая норма: без счётчиков подмена схемы датасета
    выглядела бы как успешный seed с пустой таблицей."""
    if skipped:
        log.info("seed.skipped", table=table, **skipped)


async def _existing_ids(connection: asyncpg.Connection[Any], table: str) -> set[int]:
    return {record["id"] for record in await connection.fetch(f"SELECT id FROM {table}")}


async def _upsert_batched(
    connection: asyncpg.Connection[Any],
    *,
    table: str,
    columns: Sequence[str],
    conflict: Sequence[str],
    records: Iterable[tuple[Any, ...]],
) -> int:
    total = 0
    batch: list[tuple[Any, ...]] = []
    for record in records:
        batch.append(record)
        if len(batch) >= COPY_BATCH_SIZE:
            total += await _upsert(
                connection, table=table, columns=columns, conflict=conflict, records=batch
            )
            log.info("seed.batch", table=table, loaded=total)
            batch = []

    if batch:
        total += await _upsert(
            connection, table=table, columns=columns, conflict=conflict, records=batch
        )
    return total


async def _upsert(
    connection: asyncpg.Connection[Any],
    *,
    table: str,
    columns: Sequence[str],
    conflict: Sequence[str],
    records: Sequence[tuple[Any, ...]],
) -> int:
    if not records:
        return 0

    staging = f"staging_{table}"
    column_list = ", ".join(columns)
    conflict_list = ", ".join(conflict)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns)

    async with connection.transaction():
        # Не ON COMMIT DROP: внутри чужой транзакции asyncpg открывает SAVEPOINT, а его
        # освобождение временную таблицу не удаляет — второй батч упал бы на «уже существует».
        await connection.execute(f"DROP TABLE IF EXISTS {staging}")
        await connection.execute(f"CREATE TEMP TABLE {staging} (LIKE {table} INCLUDING DEFAULTS)")
        await connection.copy_records_to_table(staging, records=records, columns=list(columns))
        # DISTINCT ON: датасет может отдать одну и ту же сущность дважды, а ON CONFLICT
        # не спасает от повторного ключа внутри одного INSERT.
        await connection.execute(
            f"INSERT INTO {table} ({column_list}) "
            f"SELECT DISTINCT ON ({conflict_list}) {column_list} FROM {staging} "
            f"ORDER BY {conflict_list} "
            f"ON CONFLICT ({conflict_list}) DO UPDATE SET {updates}"
        )
        await connection.execute(f"DROP TABLE {staging}")

    return len(records)
