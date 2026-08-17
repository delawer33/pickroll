"""Чистые преобразования строк CSV-датасета в строки таблиц.

Ни БД, ни сети — только разбор и нормализация, поэтому покрывается юнит-тестами.
Формат колонок соответствует датасету Kaggle
`eoinamoore/historical-nba-data-and-player-box-scores`.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final

# Историческая эра системы: сезоны с 2005-06 (см. CONTEXT.md).
FIRST_SEASON: Final = "2005-06"

# Сезон NBA стартует в октябре: всё, что раньше августа, относится к сезону предыдущего года.
_SEASON_START_MONTH: Final = 8


def season_from_date(game_date: date) -> str:
    """`2013-01-14` → `2012-13`, `2012-11-02` → `2012-13`."""
    start_year = game_date.year if game_date.month >= _SEASON_START_MONTH else game_date.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def is_historical_era(season: str) -> bool:
    """Сезоны сравниваются лексикографически: формат `YYYY-YY` это позволяет."""
    return season >= FIRST_SEASON


def parse_date(raw: str) -> date | None:
    """Датасет отдаёт `2012-11-02 19:30:00` или `2012-11-02`."""
    value = raw.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def parse_int(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_int_or_zero(raw: str) -> int:
    """Счётные показатели box score: пустое значение — это ноль, а не «неизвестно»."""
    return parse_int(raw) or 0


def parse_minutes(raw: str) -> Decimal | None:
    """Минуты приезжают дробными (`39.166666666666664`). Decimal — тип колонки numeric."""
    value = raw.strip()
    if not value:
        return None
    try:
        return round(Decimal(value), 3)
    except (ArithmeticError, ValueError):
        return None


def parse_bool(raw: str) -> bool:
    return raw.strip() in {"1", "1.0", "True", "true"}


def optional_text(raw: str) -> str | None:
    return raw.strip() or None


def player_position(row: dict[str, str]) -> str | None:
    """Три булевых флага датасета (`guard`/`forward`/`center`) → `G`, `F-C` и т.п."""
    codes = [
        code
        for column, code in (("guard", "G"), ("forward", "F"), ("center", "C"))
        if parse_bool(row.get(column, ""))
    ]
    return "-".join(codes) or None


TeamRow = tuple[int, str, str, str]
PlayerRow = tuple[
    int, str, str, str, date | None, str | None, int | None, int | None, str | None, int | None
]
GameRow = tuple[int, str, date, str, int, int, int | None, int | None, str | None, int | None]
BoxScoreRow = tuple[
    int, int, str, date, int, int, bool, bool, str | None, Decimal | None,
    int, int, int, int, int, int, int, int, int,
    int, int, int, int, int, int,
    int | None, str | None,
]  # fmt: skip


def team_row(row: dict[str, str]) -> TeamRow | None:
    team_id = parse_int(row.get("teamId", ""))
    city = row.get("teamCity", "").strip()
    name = row.get("teamName", "").strip()
    if team_id is None or not name:
        return None
    return (team_id, city, name, row.get("teamAbbrev", "").strip())


def team_is_newer(row: dict[str, str], best: dict[str, str]) -> bool:
    """Из истории франшизы берётся самая свежая запись — актуальные город и название."""
    return (parse_int(row.get("seasonActiveTill", "")) or 0) > (
        parse_int(best.get("seasonActiveTill", "")) or 0
    )


def player_row(row: dict[str, str]) -> PlayerRow | None:
    player_id = parse_int(row.get("personId", ""))
    first_name = row.get("firstName", "").strip()
    last_name = row.get("lastName", "").strip()
    if player_id is None or not (first_name or last_name):
        return None

    full_name = f"{first_name} {last_name}".strip()
    return (
        player_id,
        first_name,
        last_name,
        full_name,
        parse_date(row.get("birthDate", "")),
        optional_text(row.get("country", "")),
        parse_int(row.get("heightInches", "")),
        parse_int(row.get("bodyWeightLbs", "")),
        player_position(row),
        parse_int(row.get("draftYear", "")),
    )


def game_row(row: dict[str, str]) -> GameRow | None:
    """Возвращает None для матчей вне Исторической эры и для битых строк."""
    game_id = parse_int(row.get("gameId", ""))
    game_date = parse_date(row.get("gameDate", "")) or parse_date(row.get("gameDateTimeEst", ""))
    home_team_id = parse_int(row.get("hometeamId", ""))
    away_team_id = parse_int(row.get("awayteamId", ""))
    if game_id is None or game_date is None or home_team_id is None or away_team_id is None:
        return None

    season = season_from_date(game_date)
    if not is_historical_era(season):
        return None

    return (
        game_id,
        season,
        game_date,
        row.get("gameType", "").strip() or "Unknown",
        home_team_id,
        away_team_id,
        parse_int(row.get("homeScore", "")),
        parse_int(row.get("awayScore", "")),
        optional_text(row.get("arenaName", "")),
        parse_int(row.get("attendance", "")),
    )


def box_score_row(row: dict[str, str]) -> BoxScoreRow | None:
    """Возвращает None для строк вне Исторической эры и для битых строк."""
    game_id = parse_int(row.get("gameId", ""))
    player_id = parse_int(row.get("personId", ""))
    game_date = parse_date(row.get("gameDate", "")) or parse_date(row.get("gameDateTimeEst", ""))
    team_id = parse_int(row.get("playerteamId", ""))
    opponent_team_id = parse_int(row.get("opponentteamId", ""))
    if (
        game_id is None
        or player_id is None
        or game_date is None
        or team_id is None
        or opponent_team_id is None
    ):
        return None

    season = season_from_date(game_date)
    if not is_historical_era(season):
        return None

    return (
        game_id,
        player_id,
        season,
        game_date,
        team_id,
        opponent_team_id,
        parse_bool(row.get("home", "")),
        parse_bool(row.get("win", "")),
        optional_text(row.get("startingPosition", "")),
        parse_minutes(row.get("numMinutes", "")),
        parse_int_or_zero(row.get("points", "")),
        parse_int_or_zero(row.get("assists", "")),
        parse_int_or_zero(row.get("reboundsTotal", "")),
        parse_int_or_zero(row.get("reboundsOffensive", "")),
        parse_int_or_zero(row.get("reboundsDefensive", "")),
        parse_int_or_zero(row.get("steals", "")),
        parse_int_or_zero(row.get("blocks", "")),
        parse_int_or_zero(row.get("turnovers", "")),
        parse_int_or_zero(row.get("foulsPersonal", "")),
        parse_int_or_zero(row.get("fieldGoalsMade", "")),
        parse_int_or_zero(row.get("fieldGoalsAttempted", "")),
        parse_int_or_zero(row.get("threePointersMade", "")),
        parse_int_or_zero(row.get("threePointersAttempted", "")),
        parse_int_or_zero(row.get("freeThrowsMade", "")),
        parse_int_or_zero(row.get("freeThrowsAttempted", "")),
        parse_int(row.get("plusMinusPoints", "")),
        optional_text(row.get("comment", "")),
    )
