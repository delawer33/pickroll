"""Граница litellm: единственное место в проекте, где вызывается провайдер.

По ADR-0001 litellm — транспорт и нормализация форматов, не более: цикл, Инструменты и
учёт стоимости написаны здесь руками. Поэтому всё, что приезжает от litellm, тут же
превращается в свои типы, а всё, что от него падает, — в доменную ошибку. Мультипровайдер
обеспечивается строкой модели: `anthropic/...`, `gemini/...`, `groq/...` — кода это не
касается.

Тесты подменяют ровно эту границу (`litellm.acompletion`) — механика цикла, учёт и
обработка ошибок проверяются настоящие.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Any

import litellm
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.errors import ProviderRequestRejected, ProviderUnavailable
from court_vision.agent.models import LlmUsage

log = structlog.get_logger(__name__)

# Провайдер отверг сам запрос — модель, ключ, параметры. Это не сбой снаружи, а
# неправильная конфигурация или неправильно собранный нами запрос, и лечится он не повтором.
_REJECTION_ERRORS = (
    litellm.exceptions.AuthenticationError,
    litellm.exceptions.PermissionDeniedError,
    litellm.exceptions.BadRequestError,
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Запрошенный моделью вызов Инструмента. `arguments` — сырой JSON: разбирает его
    вызывающая сторона, потому что невалидный JSON от модели — это не сбой транспорта, а
    обычный результат, на который она умеет самокорректироваться."""

    id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> dict[str, Any] | None:
        """None, если модель прислала не JSON-объект."""
        try:
            parsed = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


@dataclass(frozen=True, slots=True)
class LlmReply:
    """Ответ модели на один шаг цикла."""

    # Ассистентское сообщение как его вернул провайдер: уезжает обратно в историю без
    # пересборки — реконструируя его руками, легко потерять поля, которые провайдер
    # требует вернуть вместе с tool-call'ами.
    message: dict[str, Any]
    text: str
    tool_calls: tuple[ToolCall, ...]
    # Почему модель остановилась. `length` означает обрыв по лимиту токенов — такой текст
    # нельзя принимать за законченный ответ.
    finish_reason: str


async def complete(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout: float,
    operation: str,
) -> LlmReply:
    """Один вызов модели: ответ в своих типах, строка учёта в `llm_usage`.

    Строку учёта пишет транспорт, а не цикл: так ни один вызов провайдера не может
    оказаться неучтённым, откуда бы его ни сделали.
    """
    started = perf_counter()
    try:
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            # None, а не пустой список: часть провайдеров отвергает пустой массив tools.
            tools=tools or None,
            timeout=timeout,
        )
    except Exception as error:
        # Широкий except намеренно: единственное, что здесь может упасть, — вызов
        # провайдера, а иерархия его исключений принадлежит litellm и openai и меняется от
        # версии к версии. Причину не теряем: класс уходит в расширяющие члены Problem
        # Details, трейс — в лог.
        log.exception("llm.call_failed", model=model, operation=operation)
        cause = type(error).__name__
        if isinstance(error, _REJECTION_ERRORS):
            raise ProviderRequestRejected(model, cause) from error
        raise ProviderUnavailable(model, cause) from error

    latency_ms = int((perf_counter() - started) * 1000)
    await _record_usage(
        session_factory,
        _usage_row(response, model=model, operation=operation, latency_ms=latency_ms),
    )

    if not response.choices:
        # 200 без вариантов ответа — например, срез контент-фильтром на стороне шлюза.
        # Без этой проверки наружу ушёл бы IndexError мимо всей доменной классификации.
        raise ProviderUnavailable(model, "EmptyResponse")
    return _reply(response)


async def _record_usage(session_factory: async_sessionmaker[AsyncSession], row: LlmUsage) -> None:
    """Учёт живёт в собственной транзакции, отдельно от транзакции запроса.

    Иначе любая ошибка хода агента (упёрлись в потолок шагов, отказ провайдера на третьем
    вызове) откатывала бы вместе с собой строки уже оплаченных вызовов — и в таблице
    стоимости не было бы ровно тех ходов, которые стоили дороже всего.
    """
    async with session_factory() as session:
        session.add(row)
        await session.commit()


def _reply(response: Any) -> LlmReply:
    choice = response.choices[0]
    message = choice.message
    tool_calls = tuple(
        ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
        for call in (message.tool_calls or [])
    )
    return LlmReply(
        # exclude_none: провайдеры спотыкаются о `"content": null` рядом с tool_calls,
        # а pydantic-модель litellm заполняет неотданные поля именно None.
        message=message.model_dump(exclude_none=True),
        text=message.content or "",
        tool_calls=tool_calls,
        finish_reason=choice.finish_reason or "",
    )


def _usage_row(response: Any, *, model: str, operation: str, latency_ms: int) -> LlmUsage:
    # Провайдер может не прислать блок usage вовсе — тогда учёт получает нули, но строка
    # всё равно появляется: пропуск вызова в таблице хуже, чем вызов без токенов.
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0) or prompt_tokens + completion_tokens
    return LlmUsage(
        model=model,
        response_model=getattr(response, "model", None),
        operation=operation,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=_cost(response),
        latency_ms=latency_ms,
    )


def _cost(response: Any) -> Decimal | None:
    """Стоимость считает litellm по своему прайслисту — держать вторую копию цен в репозитории
    значит гарантированно её просрочить.

    Модель без цен даёт None, а не ноль: на неизвестной модели litellm либо бросает
    исключение, либо возвращает ровный ноль, и оба случая означают «мы не знаем», а не
    «бесплатно». Ноль в этой колонке занизил бы любую сумму по таблице.
    """
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        log.warning("llm.cost_unknown", model=getattr(response, "model", None))
        return None
    return Decimal(str(cost)) if cost else None
