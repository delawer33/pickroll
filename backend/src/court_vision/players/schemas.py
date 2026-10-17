"""Wire-модели статистики игрока. snake_case по конвенциям; из них же берётся OpenAPI."""

from datetime import date

from pydantic import BaseModel, Field

# Сезон в системе — строка `YYYY-YY` (см. players/models.py). Паттерн общий для валидации
# входного параметра и для описания в OpenAPI.
SEASON_PATTERN = r"^\d{4}-\d{2}$"


class PlayerIdentity(BaseModel):
    """Резолвнутая идентичность игрока: она едина сквозь все сезоны и команды."""

    player_id: int
    full_name: str
    position: str | None = None
    birth_date: date | None = None
    country: str | None = None
    draft_year: int | None = None


class SeasonTotals(BaseModel):
    """Суммы за сезон.

    Эра-зависимые метрики отдаются как null, если лига в тот сезон их не считала
    (минуты — до 1951-52, перехваты/блоки/подборы по типам — до 1973-74, потери — до
    1977-78, трёхочковые — до 1979-80). null = «метрика не считалась», не ноль.
    """

    minutes: float | None
    points: int
    rebounds_total: int
    rebounds_offensive: int | None
    rebounds_defensive: int | None
    assists: int
    steals: int | None
    blocks: int | None
    turnovers: int | None
    fouls_personal: int
    field_goals_made: int
    field_goals_attempted: int
    three_pointers_made: int | None
    three_pointers_attempted: int | None
    free_throws_made: int
    free_throws_attempted: int


class SeasonAverages(BaseModel):
    """Показатели за игру. Проценты попаданий — от тоталов, а не среднее процентов по матчам:
    среднее процентов завышало бы вклад матчей с одним броском.

    Эра-зависимые метрики — null, если сезон раньше введения метрики (см. SeasonTotals)."""

    minutes: float | None
    points: float
    rebounds_total: float
    rebounds_offensive: float | None
    rebounds_defensive: float | None
    assists: float
    steals: float | None
    blocks: float | None
    turnovers: float | None
    fouls_personal: float
    field_goals_made: float
    field_goals_attempted: float
    three_pointers_made: float | None
    three_pointers_attempted: float | None
    free_throws_made: float
    free_throws_attempted: float
    field_goal_percentage: float | None = None
    three_point_percentage: float | None = None
    free_throw_percentage: float | None = None


class SeasonStats(BaseModel):
    """Агрегаты одного сезона, посчитанные на лету из строк Box Score."""

    season: str = Field(pattern=SEASON_PATTERN)
    games_played: int
    totals: SeasonTotals
    averages: SeasonAverages


class PlayerStatsResponse(BaseModel):
    """С заданным сезоном `seasons` содержит один элемент, без сезона — всю карьеру."""

    player: PlayerIdentity
    # Тип матчей, по которым посчитаны агрегаты: сравнивать сезоны игрока имеет смысл
    # только внутри одного типа.
    game_type: str
    seasons: list[SeasonStats]


class MetricPercentile(BaseModel):
    """Одна метрика игрока в сезоне и её Перцентиль среди Квалифицированных игроков.

    `value == None` — лига в тот сезон метрику не считала (или считать нечего: ни одного
    броска), и тогда Перцентиля нет тоже: `percentile == None`, а не 0.
    """

    metric: str
    label: str
    # Показатель за игру; проценты — доли 0–1 от тоталов, как в `SeasonAverages`.
    value: float | None
    # 0–100, «больше — лучше» для всех метрик, включая инвертированные.
    percentile: int | None = Field(default=None, ge=0, le=100)
    # Перцентиль инвертирован: меньше значение — выше Перцентиль (потери).
    lower_is_better: bool = False


class PlayerPercentilesResponse(BaseModel):
    """Перцентили игрока в одном сезоне регулярного чемпионата."""

    player: PlayerIdentity
    season: str = Field(pattern=SEASON_PATTERN)
    game_type: str
    games_played: int
    # False — игрок не Квалифицированный: Перцентили посчитаны относительно выборки
    # Квалифицированных игроков, но сам он в неё не входит и доверять им вслепую нельзя.
    qualified: bool
    # Сколько Квалифицированных игроков в выборке сезона.
    sample_size: int
    metrics: list[MetricPercentile]


class PlayerComparisonResponse(BaseModel):
    """Перцентили нескольких игроков, каждого в своём сезоне, в порядке запроса."""

    players: list[PlayerPercentilesResponse]
