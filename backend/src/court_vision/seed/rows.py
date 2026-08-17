"""Чистые преобразования строк CSV-датасета в строки таблиц.

Ни БД, ни сети — только разбор и нормализация, поэтому покрывается юнит-тестами.
Формат колонок соответствует датасету Kaggle
`eoinamoore/historical-nba-data-and-player-box-scores`.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Final

from court_vision.players.models import GAME_TYPE_BY_CODE, UNKNOWN_GAME_TYPE

# Историческая эра системы: сезоны с 2005-06 (см. CONTEXT.md).
FIRST_SEASON: Final = "2005-06"

# Обычно сезон NBA стартует в октябре: всё, что раньше августа, относится к сезону
# предыдущего года. Это лишь запасной вариант — см. season_from_game_id.
_SEASON_START_MONTH: Final = 8

# Идентификатор матча в данных NBA.com кодирует сезон: 8 цифр, где первая — тип матча,
# а вторая-третья — год начала сезона (`41900406` → плей-офф сезона 2019-20).
_GAME_ID_LENGTH: Final = 8
# Данные начинаются с сезона 1946-47, поэтому `46` и выше — двадцатый век.
_GAME_ID_CENTURY_PIVOT: Final = 46


def format_season(start_year: int) -> str:
    """`2012` → `2012-13`."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_from_date(game_date: date) -> str:
    """`2013-01-14` → `2012-13`, `2012-11-02` → `2012-13`.

    Приближение: сезон 2019-20 доигрывался в «пузыре» до октября 2020, и по дате его
    матчи неотличимы от начала сезона 2020-21. Достоверный источник — season_from_game_id.
    """
    start_year = game_date.year if game_date.month >= _SEASON_START_MONTH else game_date.year - 1
    return format_season(start_year)


def season_from_game_id(raw_game_id: str) -> str | None:
    """`41900406` → `2019-20`. None, если идентификатор непривычной формы."""
    value = raw_game_id.strip()
    if len(value) != _GAME_ID_LENGTH or not value.isdigit():
        return None

    year_in_century = int(value[1:3])
    century = 1900 if year_in_century >= _GAME_ID_CENTURY_PIVOT else 2000
    return format_season(century + year_in_century)


def season_of(row: dict[str, str]) -> str | None:
    """Сезон строки датасета: по идентификатору матча, а при его отсутствии — по дате."""
    season = season_from_game_id(row.get("gameId", ""))
    if season is not None:
        return season

    game_date = game_date_of(row)
    return season_from_date(game_date) if game_date else None


def game_date_of(row: dict[str, str]) -> date | None:
    return parse_date(row.get("gameDate", "")) or parse_date(row.get("gameDateTimeEst", ""))


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
        # OverflowError — это `inf`, ValueError — всё остальное нечисловое.
        return int(float(value))
    except (ValueError, OverflowError):
        return None


def parse_int_or_zero(raw: str) -> int:
    """Счётные показатели box score: пустое значение — это ноль, а не «неизвестно»."""
    return parse_int(raw) or 0


def parse_minutes(raw: str) -> Decimal | None:
    """Минуты приезжают дробными (`39.166666666666664`) или как `MM:SS` в старых матчах.

    Decimal — тип колонки numeric. `NaN` отсеивается явно: Decimal его принимает, а
    в numeric-колонке он отравил бы любой avg() по сезону.
    """
    value = raw.strip()
    if not value:
        return None

    try:
        parsed = _parse_minutes_value(value)
    except (ArithmeticError, ValueError):
        return None

    return round(parsed, 3) if parsed.is_finite() else None


def _parse_minutes_value(value: str) -> Decimal:
    if ":" in value:
        minutes, _, seconds = value.partition(":")
        return Decimal(minutes) + Decimal(seconds) / 60
    return Decimal(value)


def parse_bool(raw: str) -> bool:
    return raw.strip() in {"1", "1.0", "True", "true"}


def optional_text(raw: str) -> str | None:
    return raw.strip() or None


def game_type_of(row: dict[str, str]) -> str:
    """Колонка `gameType` датасета для одного и того же типа встречается в нескольких
    написаниях («NBA Cup», «Emirates NBA Cup»), а цифра идентификатора стабильна — по ней
    и нормализуем к словарю значений колонки."""
    game_id = row.get("gameId", "").strip()
    if len(game_id) == _GAME_ID_LENGTH and game_id.isdigit():
        by_code = GAME_TYPE_BY_CODE.get(game_id[0])
        if by_code is not None:
            return by_code
    return row.get("gameType", "").strip() or UNKNOWN_GAME_TYPE


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
    int, int, str, date, str, int, int, bool, bool, str | None, Decimal | None,
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
    game_date = game_date_of(row)
    season = season_of(row)
    home_team_id = parse_int(row.get("hometeamId", ""))
    away_team_id = parse_int(row.get("awayteamId", ""))
    if (
        game_id is None
        or game_date is None
        or season is None
        or home_team_id is None
        or away_team_id is None
    ):
        return None

    if not is_historical_era(season):
        return None

    return (
        game_id,
        season,
        game_date,
        game_type_of(row),
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
    game_date = game_date_of(row)
    season = season_of(row)
    team_id = parse_int(row.get("playerteamId", ""))
    opponent_team_id = parse_int(row.get("opponentteamId", ""))
    if (
        game_id is None
        or player_id is None
        or game_date is None
        or season is None
        or team_id is None
        or opponent_team_id is None
    ):
        return None

    if not is_historical_era(season):
        return None

    return (
        game_id,
        player_id,
        season,
        game_date,
        game_type_of(row),
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
