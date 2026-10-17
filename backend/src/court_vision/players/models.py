from datetime import date
from typing import Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from court_vision.db import Base

# Словарь значений `game_type` живёт рядом с колонкой, которую описывает: и seed, который
# их записывает, и агрегация, которая по ним фильтрует, обязаны говорить одними строками.
# Тип закодирован первой цифрой идентификатора матча в данных NBA.com.
GAME_TYPE_BY_CODE: Final = {
    "1": "Preseason",
    "2": "Regular Season",
    "3": "All-Star Game",
    "4": "Playoffs",
    "5": "Play-in Tournament",
    "6": "NBA Cup Final",
}
UNKNOWN_GAME_TYPE: Final = "Unknown"

# Сезонная статистика игрока по умолчанию считается по регулярному чемпионату — так её
# публикуют NBA и все справочники.
REGULAR_SEASON: Final = "Regular Season"


class Team(Base):
    """Франшиза NBA. `id` — идентификатор команды из данных NBA.com.

    Хранится актуальное название: историческая смена городов и имён для задач
    Court Vision не нужна.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    city: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    abbreviation: Mapped[str] = mapped_column(String(8))


class Player(Base):
    """Игрок. Идентичность едина сквозь все сезоны и команды (см. CONTEXT.md)."""

    __tablename__ = "players"
    __table_args__ = (
        # Опора фаззи-резолюции имени: `full_name % 'Lebron'` идёт по этому индексу.
        Index(
            "ix_players_full_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    # Денормализовано осознанно: по этой колонке идёт фаззи-поиск pg_trgm.
    full_name: Mapped[str] = mapped_column(String(128), index=True)
    birth_date: Mapped[date | None] = mapped_column(Date)
    country: Mapped[str | None] = mapped_column(String(64))
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_pounds: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[str | None] = mapped_column(String(8))
    draft_year: Mapped[int | None] = mapped_column(Integer)


class Game(Base):
    """Матч NBA. `season` — строка вида `2012-13`."""

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    season: Mapped[str] = mapped_column(String(7), index=True)
    game_date: Mapped[date] = mapped_column(Date, index=True)
    game_type: Mapped[str] = mapped_column(String(32))
    home_team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id"))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    arena_name: Mapped[str | None] = mapped_column(String(128))
    attendance: Mapped[int | None] = mapped_column(Integer)


class BoxScore(Base):
    """Строка Box Score — статистика уровня «игрок-матч».

    Минимальная гранулярность системы: play-by-play отсутствует. Сезонные агрегаты
    считаются на лету из этих строк, предагрегированных таблиц нет.
    """

    __tablename__ = "box_scores"
    __table_args__ = (
        # Опора агрегации: GROUP BY по (player_id, season) идёт по этому индексу вместо
        # полного перебора таблицы.
        Index("ix_box_scores_player_season", "player_id", "season"),
    )

    game_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("games.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), primary_key=True)

    # Денормализовано из games ради агрегации без join'а. `game_type` здесь обязателен:
    # без него сезонные средние молча смешали бы предсезонку и Матч всех звёзд с
    # регулярным чемпионатом.
    season: Mapped[str] = mapped_column(String(7))
    game_date: Mapped[date] = mapped_column(Date)
    game_type: Mapped[str] = mapped_column(String(32))

    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id"))
    opponent_team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id"))
    is_home: Mapped[bool] = mapped_column(Boolean)
    is_win: Mapped[bool] = mapped_column(Boolean)
    starting_position: Mapped[str | None] = mapped_column(String(8))

    # Эра-зависимые метрики (NULLable) — лига начала считать их не с основания:
    # минуты — 1951-52, перехваты/блоки/подборы по типам — 1973-74, потери — 1977-78,
    # трёхочковые — 1979-80 (см. METRIC_INTRODUCED в seed/rows.py). NULL = «не считалась
    # в ту эпоху», и это не ноль: SUM/AVG по NULL дают NULL, а не занижают агрегаты.
    minutes: Mapped[float | None] = mapped_column(Numeric(6, 3))
    points: Mapped[int] = mapped_column(Integer)
    assists: Mapped[int] = mapped_column(Integer)
    rebounds_total: Mapped[int] = mapped_column(Integer)
    rebounds_offensive: Mapped[int | None] = mapped_column(Integer)
    rebounds_defensive: Mapped[int | None] = mapped_column(Integer)
    steals: Mapped[int | None] = mapped_column(Integer)
    blocks: Mapped[int | None] = mapped_column(Integer)
    turnovers: Mapped[int | None] = mapped_column(Integer)
    fouls_personal: Mapped[int] = mapped_column(Integer)

    field_goals_made: Mapped[int] = mapped_column(Integer)
    field_goals_attempted: Mapped[int] = mapped_column(Integer)
    three_pointers_made: Mapped[int | None] = mapped_column(Integer)
    three_pointers_attempted: Mapped[int | None] = mapped_column(Integer)
    free_throws_made: Mapped[int] = mapped_column(Integer)
    free_throws_attempted: Mapped[int] = mapped_column(Integer)

    plus_minus: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)


# Число признаков Статистического отпечатка зашито в тип колонки: pgvector требует
# фиксированную размерность для HNSW. Список самих признаков — `fingerprints.FEATURES`, и
# модуль сверяет его длину с этой константой при импорте.
FINGERPRINT_DIMENSIONS = 9


class PlayerFingerprint(Base):
    """Статистический отпечаток игрока в сезоне (см. CONTEXT.md).

    Таблица производная: целиком пересобирается командой `rebuild_fingerprints` из строк
    Box Score, руками и приложением не пишется. Строки есть только у Квалифицированных
    сезонов, где считались все признаки и минуты.
    """

    __tablename__ = "player_fingerprints"
    __table_args__ = (
        # Косинусная близость по HNSW — как в индексе чанков корпуса.
        Index(
            "ix_player_fingerprints_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.id"), primary_key=True)
    season: Mapped[str] = mapped_column(String(7), primary_key=True)
    # Вектор, по которому идёт поиск, и те же z-scores по ключам признаков для объяснения:
    # JSONB читается человеком и моделью, вектор — только индексом.
    embedding: Mapped[list[float]] = mapped_column(Vector(FINGERPRINT_DIMENSIONS))
    z_scores: Mapped[dict[str, float]] = mapped_column(JSONB)
