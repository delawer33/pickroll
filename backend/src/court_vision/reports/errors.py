"""Доменные ошибки модуля Скаутинг-отчётов."""

from typing import Any, ClassVar

from court_vision.errors import DomainError
from court_vision.problems import PROBLEM_TYPE_PREFIX


class ReportNotFound(DomainError):
    """Отчёта нет — или он есть, но принадлежит другому пользователю.

    Оба случая неразличимы снаружи по той же причине, что и у Диалога: 403 на чужой отчёт
    подтверждал бы существование идентификатора.
    """

    status: ClassVar[int] = 404
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}report-not-found"
    title: ClassVar[str] = "Report not found"

    def __init__(self) -> None:
        super().__init__("Отчёт не найден.")


class ReportGenerationFailed(DomainError):
    """Модель дважды подряд отдала отчёт, не прошедший проверку.

    Отчёт не сохраняется и не показывается: частично верный разбор игрока хуже отсутствия
    разбора. Повтор для пользователя безопасен — ничего не записано.
    """

    status: ClassVar[int] = 502
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}report-generation-failed"
    title: ClassVar[str] = "Report generation failed"

    def __init__(self, violations: list[str], *, attempts: int) -> None:
        super().__init__(
            "Не удалось сгенерировать корректный отчёт: модель дважды нарушила схему "
            "или сослалась на данные, которых не было во входе. Повторите запрос."
        )
        self.violations = violations
        self.attempts = attempts

    def extensions(self) -> dict[str, Any]:
        # Нарушения последней попытки: по ним видно, чему модель не подчинилась, а наружу
        # уходит не больше пяти — остальное в логе.
        return {"attempts": self.attempts, "violations": self.violations[:5]}
