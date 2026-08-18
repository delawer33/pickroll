"""Доменные ошибки агентного слоя.

Наружу выходят только эти типы: исключения litellm и openai за границу модуля не выходят —
иначе смена провайдера меняла бы формат ошибки, которую видит фронт.
"""

from typing import Any, ClassVar

from court_vision.errors import DomainError
from court_vision.problems import PROBLEM_TYPE_PREFIX


class ProviderUnavailable(DomainError):
    """Провайдер не ответил: сеть, таймаут, лимит, перегрузка на его стороне.

    Причины схлопнуты в один тип осознанно: реакция вызывающей стороны на них одна и та же —
    сказать пользователю, что ответа сейчас не будет, и предложить повторить. Различать их
    по-настоящему начнёт фолбэк-политика спринта 5.
    """

    status: ClassVar[int] = 502
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}llm-provider-unavailable"
    title: ClassVar[str] = "LLM provider unavailable"

    def __init__(self, model: str, cause: str) -> None:
        super().__init__("LLM-провайдер не ответил на запрос.")
        self.model = model
        # Классификатор причины, а не текст провайдера: в сообщении может оказаться фрагмент
        # запроса, а наружу лишнего отдавать нечего. Полный трейс — в логе.
        self.cause = cause

    def extensions(self) -> dict[str, Any]:
        return {"model": self.model, "cause": self.cause}


class ProviderRequestRejected(DomainError):
    """Провайдер отверг сам запрос: неизвестная модель, неверный ключ, негодные параметры.

    Отдельно от `ProviderUnavailable`, потому что это не сбой снаружи, а наша ошибка —
    опечатка в `LLM_MODEL` или просроченный ключ. Под видом 502 она выглядела бы как
    временная недоступность, и её чинили бы повторами вместо правки окружения.
    """

    status: ClassVar[int] = 500
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}llm-request-rejected"
    title: ClassVar[str] = "LLM request rejected"

    def __init__(self, model: str, cause: str) -> None:
        super().__init__("Запрос к LLM отвергнут провайдером.")
        self.model = model
        self.cause = cause

    def extensions(self) -> dict[str, Any]:
        return {"model": self.model, "cause": self.cause}


class IncompleteAnswer(DomainError):
    """Модель закончила ход, но пригодного ответа не отдала: пусто или обрыв по лимиту токенов.

    Отдавать такое пользователю нельзя: оборванная на середине фраза «набирал 26.» выглядит
    как законченный ответ и врёт цифрой, а пустая строка — как успешный пустой ответ.
    """

    status: ClassVar[int] = 502
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}incomplete-answer"
    title: ClassVar[str] = "Incomplete answer"

    def __init__(self, reason: str) -> None:
        super().__init__("Модель не отдала законченный ответ.")
        self.reason = reason

    def extensions(self) -> dict[str, Any]:
        return {"reason": self.reason}


class AgentStepLimitReached(DomainError):
    """Цикл упёрся в потолок шагов, так и не дойдя до финального ответа.

    Отдельная ошибка, а не «отдать последнее, что сказала модель»: на последнем шаге у
    модели на руках только результаты Инструментов, и её незаконченная реплика выглядела
    бы как полноценный ответ.
    """

    status: ClassVar[int] = 500
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}agent-step-limit-reached"
    title: ClassVar[str] = "Agent did not finish"

    def __init__(self, max_steps: int) -> None:
        super().__init__(f"Агент не уложился в {max_steps} шагов и остался без ответа.")
        self.max_steps = max_steps

    def extensions(self) -> dict[str, Any]:
        return {"max_steps": self.max_steps}
