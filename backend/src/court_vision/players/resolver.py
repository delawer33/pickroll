"""Резолюция игрока по свободному тексту.

Поиск фаззи (pg_trgm), потому что имя приезжает от LLM, а в написании редких имён она
ошибается. Три исхода структурны — уверенный матч, неоднозначность, «не найдено» с
ближайшими кандидатами: по любому из них вызывающая сторона может самокорректироваться,
не разбирая текст сообщения.

Пороги сходства — деталь реализации этого модуля, не часть контракта: вызывающий код
видит только исход.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.models import Player

# Ниже этого сходства кандидат не заслуживает даже упоминания среди ближайших: на другом
# алфавите pg_trgm даёт ровный ноль, и подсовывать в ответ случайные имена — дезинформация.
_CANDIDATE_FLOOR: Final = 0.2
# Выше — имя узнаваемо. Замеры на данных: узнаваемые опечатки начинаются с 0.54 («Lebron»
# → LeBron James, «Kobe Briant» → 0.60), а имя не из датасета («Michael Jordan» → Michael
# Curry) даёт 0.38 — порог стоит между этими группами.
_CONFIDENT_SIMILARITY: Final = 0.45
# Кандидаты, набравшие хотя бы такую долю от сходства лидера, равноправны. Доля, а не
# абсолютный отрыв: разница между «Bryant» → Joe Bryant (0.64) и Kobe Bryant (0.58) —
# это шум от длины имени, а не сигнал, и молча выбрать одного из них нельзя.
_TIE_RATIO: Final = 0.9
_MAX_CANDIDATES: Final = 5


@dataclass(frozen=True, slots=True)
class PlayerCandidate:
    """Игрок, похожий на запрос, но не выбранный: пригоден для повторного вызова."""

    player_id: int
    full_name: str
    similarity: float


@dataclass(frozen=True, slots=True)
class ResolvedPlayer:
    """Уверенный матч: кандидат один и написание узнаваемо."""

    player: Player
    similarity: float


@dataclass(frozen=True, slots=True)
class AmbiguousName:
    """Запрос одинаково хорошо подходит нескольким игрокам."""

    query: str
    candidates: tuple[PlayerCandidate, ...]


@dataclass(frozen=True, slots=True)
class UnknownName:
    """Ни один игрок не похож достаточно. `nearest` пуст, если не похож вообще никто."""

    query: str
    nearest: tuple[PlayerCandidate, ...]


PlayerResolution = ResolvedPlayer | AmbiguousName | UnknownName


async def resolve_player_name(session: AsyncSession, name: str) -> PlayerResolution:
    """Превращает свободный текст в идентичность игрока.

    Ничего не знает про HTTP и про агента: переиспользуется всеми инструментами, которым
    нужно имя игрока на входе.
    """
    query = " ".join(name.split())
    if not query:
        return UnknownName(query=query, nearest=())

    ranked = await _search(session, query)
    if not ranked:
        return UnknownName(query=query, nearest=())

    best = ranked[0]
    # Порядок проверок важен: «не найдено» сильнее неоднозначности. Два равно непохожих
    # имени — это не выбор из двух игроков, а отсутствие ответа.
    if best.similarity < _CONFIDENT_SIMILARITY:
        return UnknownName(query=query, nearest=tuple(ranked))

    tied = [c for c in ranked if c.similarity >= best.similarity * _TIE_RATIO]
    if len(tied) > 1:
        return AmbiguousName(query=query, candidates=tuple(tied))

    player = await session.get(Player, best.player_id)
    if player is None:  # pragma: no cover — игрок исчезнуть внутри одной транзакции не может
        return UnknownName(query=query, nearest=())
    return ResolvedPlayer(player=player, similarity=best.similarity)


async def _search(session: AsyncSession, query: str) -> list[PlayerCandidate]:
    # Порог оператора `%` живёт в настройке сессии, а не в выражении. SET LOCAL, потому что
    # соединение возвращается в пул: настройка обязана умереть вместе с транзакцией.
    await session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {_CANDIDATE_FLOOR}"))

    similarity = func.similarity(Player.full_name, query)
    statement = (
        select(Player.id, Player.full_name, similarity.label("similarity"))
        # Именно `%`, а не `similarity(...) >= порог`: оператор идёт по gin-индексу
        # ix_players_full_name_trgm, сравнение функции — полным перебором таблицы.
        .where(Player.full_name.bool_op("%")(query))
        # full_name в сортировке — тай-брейк: без него порядок тезоименинников случаен,
        # и «неоднозначность» отдавала бы кандидатов в разном порядке от запроса к запросу.
        .order_by(similarity.desc(), Player.full_name, Player.id)
        .limit(_MAX_CANDIDATES)
    )

    result = await session.execute(statement)
    return [
        PlayerCandidate(player_id=row.id, full_name=row.full_name, similarity=float(row.similarity))
        for row in result
    ]
