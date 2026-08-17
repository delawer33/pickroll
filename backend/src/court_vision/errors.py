"""Базовый тип доменных ошибок.

Сервисы кидают подклассы `DomainError` и ничего не знают ни про статусы, ни про формат
ответа: перевод в RFC 9457 Problem Details делает единый handler (`problems.py`).
"""

from typing import Any, ClassVar


class DomainError(Exception):
    """Ошибка предметной области, у которой есть осмысленная проекция на HTTP.

    Подкласс объявляет `status`, `problem_type` и `title`, а через `extensions()` может
    добавить в тело расширяющие члены — данные, по которым вызывающая сторона (в том
    числе агент) способна самокорректироваться.
    """

    status: ClassVar[int] = 400
    problem_type: ClassVar[str] = "about:blank"
    title: ClassVar[str] = "Domain error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def extensions(self) -> dict[str, Any]:
        return {}
