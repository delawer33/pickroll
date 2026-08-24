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
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
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
class TextDelta:
    """Фрагмент текста, пришедший в потоке. Дописывается в конец — не заменяет собой."""

    text: str


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


async def stream(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout: float,
    operation: str,
    api_key: str | None = None,
    api_base: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[TextDelta | LlmReply]:
    """Один вызов модели потоком: сначала `TextDelta` по мере поступления, в конце — ровно
    один `LlmReply` с собранным ответом целиком.

    Вызов только стриминговый, второго — «быстрого, без потока» — здесь нет намеренно: две
    реализации одного вызова разъезжаются в деталях (учёт, классификация ошибок, сборка
    ассистентского сообщения), и разъезд обнаруживается на той из них, которую реже гоняют.
    Кому поток не нужен, тот его вычерпывает (`loop.answer`).

    Строку учёта пишет транспорт, а не цикл: так ни один вызов провайдера не может
    оказаться неучтённым, откуда бы его ни сделали.
    """
    started = perf_counter()
    chunks: list[Any] = []
    # Собранный ответ виден блоку finally: по нему пишется учёт, и второй раз собирать его
    # там незачем.
    response: Any | None = None
    try:
        try:
            response_stream = await litellm.acompletion(
                model=model,
                messages=messages,
                # None, а не пустой список: часть провайдеров отвергает пустой массив tools.
                tools=tools or None,
                timeout=timeout,
                # None, а не пустая строка: так litellm возвращается к своему поиску ключа в
                # окружении, а пустая строка ушла бы в заголовок авторизации как есть.
                api_key=api_key or None,
                api_base=api_base or None,
                stream=True,
                # Без этого блок usage в стриминге не приходит вовсе, и учёт получал бы нули
                # на каждом вызове.
                stream_options={"include_usage": True},
                # Метаданные трассировки: их читают колбэки litellm (Langfuse), провайдеру
                # они не уезжают. None, а не пустой словарь — чтобы не плодить пустые поля.
                metadata=trace_metadata or None,
            )
            # aclosing, а не просто `async for`: цикл сам за собой итератор не закрывает, а
            # брошенный поток провайдера держит открытым HTTP-соединение до сборки мусора —
            # пользователь, ушедший со страницы посреди ответа, оставлял бы его за собой.
            async with aclosing(response_stream) as parts:
                async for chunk in parts:
                    chunks.append(chunk)
                    delta = _delta_text(chunk)
                    if delta:
                        yield TextDelta(delta)
        except Exception as error:
            # Широкий except намеренно: единственное, что здесь может упасть, — вызов
            # провайдера, а иерархия его исключений принадлежит litellm и openai и меняется
            # от версии к версии. Причину не теряем: класс уходит в расширяющие члены
            # Problem Details, трейс — в лог.
            log.exception("llm.call_failed", model=model, operation=operation)
            cause = type(error).__name__
            if isinstance(error, _REJECTION_ERRORS):
                raise ProviderRequestRejected(model, cause) from error
            raise ProviderUnavailable(model, cause) from error

        response = _rebuilt(chunks, messages=messages)
        if response is None or not response.choices:
            # Поток закончился, не отдав ни одного пригодного варианта ответа — например срез
            # контент-фильтром на стороне шлюза. Без этой проверки наружу ушёл бы IndexError
            # мимо всей доменной классификации.
            raise ProviderUnavailable(model, "EmptyResponse")
        yield _reply(response)
    finally:
        # Учёт в finally: строка обязана появиться на каждый оплаченный вызов, а поток
        # обрывают чаще всего снаружи — пользователь закрыл страницу, и обычный код после
        # цикла до этого места уже не дошёл бы.
        await _account(
            session_factory,
            # Оборванный поток собрать в ответ нечем, но usage провайдер мог успеть прислать:
            # вызов оплачен, и пропуск такой строки занизил бы любую сумму по таблице.
            response or _with_usage(chunks),
            model=model,
            operation=operation,
            started=started,
        )


# Потолок текстов в одном вызове эмбеддера: у провайдеров свои лимиты на размер батча
# (у OpenAI-совместимых обычно ≥ 2048 входов, у шлюзов бывает меньше), сотня — безопасно
# и достаточно крупно, чтобы не платить латентностью за каждый чанк.
_EMBED_BATCH_SIZE = 100


async def embed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str,
    texts: Sequence[str],
    dimensions: int,
    timeout: float,
    operation: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> list[list[float]]:
    """Эмбеддинги батчами через ту же границу litellm и с тем же учётом, что и `stream`.

    Порядок результата совпадает с порядком `texts`. Каждый вызов провайдера пишет свою
    строку в llm_usage (operation например "corpus.embed") — по тем же правилам: строка
    обязана появиться на каждый оплаченный вызов, даже упавший после оплаты.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = list(texts[start : start + _EMBED_BATCH_SIZE])
        started = perf_counter()
        response: Any | None = None
        try:
            try:
                response = await litellm.aembedding(
                    model=model,
                    input=batch,
                    dimensions=dimensions,
                    # litellm белым списком режет dimensions для незнакомых openai-моделей,
                    # хотя шлюз параметр поддерживает — пропускаем явно.
                    allowed_openai_params=["dimensions"],
                    timeout=timeout,
                    # См. `stream`: None вместо пустых строк.
                    api_key=api_key or None,
                    api_base=api_base or None,
                )
            except Exception as error:
                # Широкий except по той же причине, что в `stream`: иерархия исключений
                # принадлежит litellm и меняется от версии к версии.
                log.exception("llm.embed_failed", model=model, operation=operation)
                cause = type(error).__name__
                if isinstance(error, _REJECTION_ERRORS):
                    raise ProviderRequestRejected(model, cause) from error
                raise ProviderUnavailable(model, cause) from error
            vectors.extend(_embedding_vectors(response, model=model, expected=len(batch)))
        finally:
            await _account(
                session_factory, response, model=model, operation=operation, started=started
            )
    return vectors


def _embedding_vectors(response: Any, *, model: str, expected: int) -> list[list[float]]:
    """Векторы из ответа в порядке входных текстов.

    Провайдер не обязан сохранять порядок — сортируем по `index`. Недостача вектора —
    негодный ответ, как пустой поток в `stream`: молча вернуть меньше значило бы
    рассинхронизировать векторы с чанками у вызывающей стороны.
    """
    data = list(getattr(response, "data", None) or [])
    if len(data) != expected:
        raise ProviderUnavailable(model, "EmbeddingCountMismatch")

    def _field(item: Any, name: str, default: Any) -> Any:
        return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)

    ordered = sorted(data, key=lambda item: int(_field(item, "index", 0)))
    return [[float(value) for value in _field(item, "embedding", [])] for item in ordered]


async def _account(
    session_factory: async_sessionmaker[AsyncSession],
    billed: Any | None,
    *,
    model: str,
    operation: str,
    started: float,
) -> None:
    """Пишет строку учёта. Своих ошибок наружу не выпускает.

    Ответ уже сгенерирован и оплачен, и уронить его из-за неудачной записи в таблицу
    стоимости — обменять товар на чек: пользователь остался бы без ответа, который провайдер
    уже посчитал.
    """
    if billed is None:
        return
    latency_ms = int((perf_counter() - started) * 1000)
    try:
        await _record_usage(
            session_factory,
            _usage_row(billed, model=model, operation=operation, latency_ms=latency_ms),
        )
    except Exception:
        log.exception("llm.usage_not_recorded", model=model, operation=operation)


def _rebuilt(chunks: list[Any], *, messages: list[dict[str, Any]]) -> Any | None:
    """Собирает чанки обратно в обычный ответ силами litellm.

    Склейка дельт tool-call'ов по индексам — работа транспортного слоя, и переписывать её у
    себя значит держать копию формата каждого провайдера. Сборка падает на потоке без
    вариантов ответа; это не сбой нашего кода, а такой же негодный ответ, как пустой.
    """
    if not chunks:
        return None
    try:
        return litellm.stream_chunk_builder(chunks, messages=messages)
    except Exception:
        log.warning("llm.stream_unbuildable", chunks=len(chunks))
        return None


def _with_usage(chunks: list[Any]) -> Any | None:
    """Чанк с блоком usage — обычно последний. Из него берётся учёт, когда ответа нет."""
    return next((chunk for chunk in reversed(chunks) if getattr(chunk, "usage", None)), None)


def _delta_text(chunk: Any) -> str:
    """Текст из чанка. Пусто — если чанк несёт дельту tool-call'а или только блок usage."""
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return str(getattr(delta, "content", None) or "")


async def _record_usage(session_factory: async_sessionmaker[AsyncSession], row: LlmUsage) -> None:
    """Учёт живёт в собственной транзакции, отдельно от транзакции запроса.

    Иначе любая ошибка ответа агента (упёрлись в потолок шагов, отказ провайдера на третьем
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
