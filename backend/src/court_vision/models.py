"""Единая точка импорта моделей всех модулей — для Alembic и для тестов.

Модели живут в своих вертикальных срезах; здесь только реэкспорт, чтобы
`Base.metadata` знал обо всех таблицах.
"""

from court_vision.players.models import BoxScore, Game, Player, Team

__all__ = ["BoxScore", "Game", "Player", "Team"]
