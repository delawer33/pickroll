"""Доменные ошибки модуля Диалогов."""

from typing import ClassVar

from court_vision.errors import DomainError
from court_vision.problems import PROBLEM_TYPE_PREFIX


class ConversationNotFound(DomainError):
    """Диалога нет — или он есть, но принадлежит другому пользователю.

    Оба случая намеренно неразличимы снаружи: 403 на чужой Диалог подтверждал бы, что такой
    идентификатор существует, и превращал бы перебор в способ узнать, сколько Диалогов у
    соседа. Наружу — ровное 404.
    """

    status: ClassVar[int] = 404
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}conversation-not-found"
    title: ClassVar[str] = "Conversation not found"

    def __init__(self) -> None:
        super().__init__("Диалог не найден.")
