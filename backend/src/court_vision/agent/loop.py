"""Агентный цикл — смысловое ядро проекта (ADR-0001).

Цикл простой и полностью наш: история → модель → если пришли tool-call'ы, выполнить их и
вернуть результаты в модель → повторять, пока не придёт ответ без вызовов. Никакого
фреймворка над этим нет — именно здесь живут решения о шагах, об учёте и о том, что
считать ошибкой.

Цикл стриминговый: наружу он отдаёт события по мере их появления, а не готовый ответ.
Иначе пользователь ждёт молча всё время, пока модель думает и ходит за данными.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent import tools as tool_registry
from court_vision.agent.errors import AgentStepLimitReached, IncompleteAnswer
from court_vision.agent.evidence import EvidenceView, ToolFailure
from court_vision.agent.prompts import load_prompt
from court_vision.agent.transport import LlmReply, TextDelta, ToolCall, stream
from court_vision.config import Settings
from court_vision.corpus.search import ChunkHit

log = structlog.get_logger(__name__)

OPERATION = "agent.step"


@dataclass(frozen=True, slots=True)
class Turn:
    """Реплика Диалога на входе цикла. Внутреннюю кухню — системный промпт, tool-call'ы и
    их результаты — цикл собирает сам: хранилищу Диалогов (#8) о ней знать незачем."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Состоявшийся вызов Инструмента — материал для события `tool_call` в SSE и для отладки."""

    name: str
    arguments: dict[str, Any] | None
    result: tool_registry.ToolResult
    duration_ms: int


@dataclass(frozen=True, slots=True)
class TurnCitation:
    """Факт цитаты: маркер `[n]` в тексте ответа и всё, на что он указывает.

    Здесь и вызов (`tool_call_id` — карточка Evidence), и чанк корпуса (`chunk_id`,
    документ, локатор): фронту не приходится собирать цитату из двух событий.
    """

    index: int
    tool_call_id: str
    chunk_id: int
    document_title: str
    locator: str
    snippet: str
    source_url: str | None


# Маркер цитаты в тексте ответа: `[1]`, `[12]`. Ровно та же форма, которую просит system.md.
_CITATION_MARKER = re.compile(r"\[(\d+)\]")


class CitationRegistry:
    """Сквозная нумерация цитат одного ответа.

    Один реестр на ход агента, а не на вызов Инструмента: модель цитирует `[n]` по номерам
    из результатов, и два вызова `search_corpus` обязаны продолжать одну нумерацию — иначе
    второй вызов повторил бы номера первого, и маркер стал бы неоднозначным.
    """

    def __init__(self) -> None:
        # Дедуп по chunk_id: один и тот же чанк, найденный двумя вызовами, — одна цитата
        # с одним номером, а не две записи про одно место документа.
        self._by_chunk: dict[int, TurnCitation] = {}

    def register(self, hit: ChunkHit, *, tool_call_id: str) -> int:
        """Выдаёт чанку номер цитирования: 1, 2, 3... в порядке первой регистрации."""
        existing = self._by_chunk.get(hit.chunk_id)
        if existing is not None:
            return existing.index
        citation = TurnCitation(
            index=len(self._by_chunk) + 1,
            tool_call_id=tool_call_id,
            chunk_id=hit.chunk_id,
            document_title=hit.document_title,
            locator=hit.locator,
            snippet=hit.text,
            source_url=hit.url,
        )
        self._by_chunk[hit.chunk_id] = citation
        return citation.index

    def cited_in(self, text: str) -> list[TurnCitation]:
        """Факты цитат по маркерам `[n]` финального текста.

        Валидация постфактум, в обе стороны: маркер-сирота (номера никто не выдавал)
        остаётся плоским текстом и цитатой не становится, а выданный номер без маркера в
        тексте не эмитится — источник, на который ответ не опёрся, не провенанс.
        """
        registered = {citation.index: citation for citation in self._by_chunk.values()}
        cited = {int(marker.group(1)) for marker in _CITATION_MARKER.finditer(text)}
        return [registered[index] for index in sorted(cited) if index in registered]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Контекст хода, доступный Инструменту помимо сессии БД.

    Реестр цитат — общий на весь ход; остальное — то, без чего `search_corpus` не сможет
    посчитать эмбеддинг запроса (транспорту нужны фабрика сессий для учёта и настройки).
    """

    citations: CitationRegistry
    # Вызов, внутри которого работает Инструмент: цитата указывает на конкретную карточку.
    tool_call_id: str
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    """Итог одного ответа агента — последнее событие потока.

    `text` равен склейке всех отданных до него `TokenChunk`: именно этот текст сохраняется
    Репликой, поэтому перезагрузка страницы показывает ровно то, что пользователь читал в
    потоке, а не сокращённую версию.
    """

    text: str
    tool_invocations: tuple[ToolInvocation, ...]
    # Цитаты, на которые ответ опёрся маркерами [n]. Пустой tuple — обычный ответ без корпуса.
    citations: tuple[TurnCitation, ...] = ()


@dataclass(frozen=True, slots=True)
class TokenChunk:
    """Фрагмент текста ответа."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCalled:
    """Агент решил вызвать Инструмент. Отдаётся до выполнения: смысл события — показать,
    чем занят агент, пока ответа ещё нет."""

    # Идентификатор вызова от провайдера: по нему клиент отличает два вызова одного и того
    # же Инструмента друг от друга.
    id: str
    name: str
    arguments: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ToolResulted:
    """Инструмент отработал. Второе событие про тот же вызов: `id` совпадает с `ToolCalled`.

    Два события, а не одно с результатом: `ToolCalled` отдаётся до выполнения намеренно —
    показать, чем занят агент, пока ответа нет. Без второго события удачный вызов и
    вернувшийся ни с чем выглядят в рейке одинаково.
    """

    id: str
    duration_ms: int
    # Взаимоисключающие: у отказа проекции нет, показывать в карточке нечего кроме отказа.
    failure: ToolFailure | None
    view: EvidenceView | None


AgentEvent = TokenChunk | ToolCalled | ToolResulted | AgentAnswer

# Разделитель между текстовыми блоками разных шагов: модель нередко говорит «сейчас
# посмотрю» до вызова Инструмента, и без пустой строки преамбула склеилась бы с ответом.
_STEP_SEPARATOR = "\n\n"


async def stream_answer(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    history: Sequence[Turn],
    settings: Settings,
    trace_metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[AgentEvent]:
    """Прогоняет цикл, отдавая события по мере их появления; последнее — `AgentAnswer`.

    `history` — вся переписка Диалога целиком, включая текущий вопрос: агент отвечает с
    учётом контекста, поэтому follow-up «а в 2015-16?» работает без повтора имени игрока.

    На входе фабрика сессий, а не сессия: ответ агента длится столько, сколько отвечает
    провайдер, и держать всё это время открытую транзакцию запроса — значит занимать
    соединение пула минутами на каждый вопрос. Каждый вызов Инструмента и каждая строка
    учёта берут своё короткое соединение.
    """
    if not history:
        raise ValueError("История Диалога пуста: агенту нечего отвечать.")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": load_prompt("system")},
        *({"role": turn.role, "content": turn.content} for turn in history),
    ]
    invocations: list[ToolInvocation] = []
    blocks: list[str] = []
    citations = CitationRegistry()

    for step in range(settings.llm_max_steps):
        reply: LlmReply | None = None
        spoken: list[str] = []
        # aclosing: без него брошенный снаружи цикл не закрывал бы поток провайдера — тот
        # остался бы висеть на своём yield вместе с открытым соединением.
        async with aclosing(
            _step(
                session_factory,
                messages=messages,
                settings=settings,
                trace_metadata=trace_metadata,
            )
        ) as parts:
            async for part in parts:
                if isinstance(part, TextDelta):
                    if not spoken and blocks:
                        yield TokenChunk(_STEP_SEPARATOR)
                        blocks.append(_STEP_SEPARATOR)
                    spoken.append(part.text)
                    yield TokenChunk(part.text)
                else:
                    reply = part
        if reply is None:
            # Транспорт заканчивает поток ровно одним LlmReply — либо бросает доменную
            # ошибку. Проверка, а не assert: под `-O` assert исчезает, и на его месте
            # оказался бы AttributeError вместо доменной ошибки.
            raise IncompleteAnswer("no_reply")
        blocks.extend(spoken)
        messages.append(reply.message)

        if not reply.tool_calls:
            log.info("agent.answered", steps=step + 1, tool_calls=len(invocations))
            text = _final_text(reply, spoken=spoken, blocks=blocks)
            yield AgentAnswer(
                text=text,
                tool_invocations=tuple(invocations),
                citations=tuple(citations.cited_in(text)),
            )
            return

        for call in reply.tool_calls:
            yield ToolCalled(id=call.id, name=call.name, arguments=call.parsed_arguments())
            ctx = ToolContext(
                citations=citations,
                tool_call_id=call.id,
                session_factory=session_factory,
                settings=settings,
            )
            invocation = await _invoke(session_factory, call, ctx)
            invocations.append(invocation)
            evidence = tool_registry.evidence_for(name=call.name, result=invocation.result)
            yield ToolResulted(
                id=call.id,
                duration_ms=invocation.duration_ms,
                failure=evidence.failure,
                view=evidence.view,
            )
            messages.append(_tool_message(call, invocation.result))

    # Потолок шагов — защита от цикла, в котором модель бесконечно перевызывает Инструмент.
    log.warning("agent.step_limit_reached", max_steps=settings.llm_max_steps)
    raise AgentStepLimitReached(settings.llm_max_steps)


def _step(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    messages: list[dict[str, Any]],
    settings: Settings,
    trace_metadata: dict[str, Any] | None,
) -> AsyncGenerator[TextDelta | LlmReply]:
    return stream(
        session_factory,
        model=settings.llm_model,
        messages=messages,
        tools=tool_registry.DEFINITIONS,
        timeout=settings.llm_timeout_seconds,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        operation=OPERATION,
        trace_metadata=trace_metadata,
    )


def _final_text(reply: LlmReply, *, spoken: Sequence[str], blocks: Sequence[str]) -> str:
    """Сказанное годится в ответ пользователю, только если оно законченное.

    На пустоту проверяется последний шаг (`spoken`), а не всё сказанное (`blocks`): модель
    могла сказать «сейчас посмотрю», сходить за данными и вернуться ни с чем — тогда
    преамбула без единой цифры уехала бы пользователю как готовый ответ.
    """
    if reply.finish_reason == "length":
        log.warning("agent.answer_truncated")
        raise IncompleteAnswer("truncated")
    if not "".join(spoken).strip():
        log.warning("agent.answer_empty")
        raise IncompleteAnswer("empty")
    return "".join(blocks)


async def _invoke(
    session_factory: async_sessionmaker[AsyncSession], call: ToolCall, ctx: ToolContext
) -> ToolInvocation:
    arguments = call.parsed_arguments()
    log.info("agent.tool_call", tool=call.name, arguments=arguments)
    # Замеряется всё вместе с ожиданием соединения из пула: в карточке стоит длительность,
    # которую прожил пользователь, а не время одного SQL-запроса.
    started = perf_counter()
    async with session_factory() as session:
        # Инструменты только читают — коммитить нечего; сессия закрывается сразу, чтобы
        # соединение не ждало следующего ответа модели.
        result = await tool_registry.execute_tool(
            session, name=call.name, arguments=arguments, ctx=ctx
        )
    duration_ms = round((perf_counter() - started) * 1000)
    log.info("agent.tool_result", tool=call.name, duration_ms=duration_ms)
    return ToolInvocation(
        name=call.name, arguments=arguments, result=result, duration_ms=duration_ms
    )


def _tool_message(call: ToolCall, result: tool_registry.ToolResult) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        # ensure_ascii=False: экранированная кириллица раздувает промпт втрое на ровном месте.
        "content": json.dumps(result, ensure_ascii=False),
    }
