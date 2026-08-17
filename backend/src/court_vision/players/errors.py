"""Доменные ошибки модуля игроков.

Каждая несёт в расширяющих членах данные для самокоррекции: агент, получив «не найдено»
с ближайшими кандидатами или список тезоименинников, делает второй вызов уже с точным
именем, вместо того чтобы гадать по тексту сообщения.
"""

from typing import Any, ClassVar

from court_vision.errors import DomainError
from court_vision.players.resolver import PlayerCandidate
from court_vision.problems import PROBLEM_TYPE_PREFIX


class PlayerNotFound(DomainError):
    """Ни один игрок не похож на запрос достаточно, чтобы его выбрать."""

    status: ClassVar[int] = 404
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}player-not-found"
    title: ClassVar[str] = "Player not found"

    def __init__(self, query: str, nearest: tuple[PlayerCandidate, ...]) -> None:
        super().__init__(f"Игрок по запросу «{query}» не найден.")
        self.query = query
        self.nearest = nearest

    def extensions(self) -> dict[str, Any]:
        return {"query": self.query, "nearest": [_candidate(c) for c in self.nearest]}


class AmbiguousPlayerName(DomainError):
    """Запрос одинаково хорошо подходит нескольким игрокам — выбирать за вызывающего нельзя."""

    status: ClassVar[int] = 409
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}ambiguous-player-name"
    title: ClassVar[str] = "Ambiguous player name"

    def __init__(self, query: str, candidates: tuple[PlayerCandidate, ...]) -> None:
        super().__init__(f"Запрос «{query}» подходит нескольким игрокам — уточните имя полностью.")
        self.query = query
        self.candidates = candidates

    def extensions(self) -> dict[str, Any]:
        return {"query": self.query, "candidates": [_candidate(c) for c in self.candidates]}


class SeasonDataMissing(DomainError):
    """Игрок опознан, но матчей регулярного чемпионата в запрошенном сезоне у него нет.

    Отдельная ошибка, а не пустые агрегаты: нули и «данных нет» — разные утверждения, и
    агент, перепутав их, соврал бы про игрока.
    """

    status: ClassVar[int] = 404
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}season-data-missing"
    title: ClassVar[str] = "No season data"

    def __init__(self, player_id: int, full_name: str, season: str | None) -> None:
        scope = f"в сезоне {season}" if season else "в Исторической эре"
        super().__init__(f"У игрока {full_name} нет матчей регулярного чемпионата {scope}.")
        self.player_id = player_id
        self.full_name = full_name
        self.season = season

    def extensions(self) -> dict[str, Any]:
        return {
            "player": {"player_id": self.player_id, "full_name": self.full_name},
            "season": self.season,
        }


def _candidate(candidate: PlayerCandidate) -> dict[str, Any]:
    return {
        "player_id": candidate.player_id,
        "full_name": candidate.full_name,
        "similarity": round(candidate.similarity, 3),
    }
