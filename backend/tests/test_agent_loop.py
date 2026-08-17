"""Механика агентного цикла на настоящих данных и настоящих Инструментах.

Подменяется ровно одна вещь — `litellm.acompletion`, граница провайдера: живых LLM-вызовов
в CI быть не должно, а всё остальное (маршрутизация к Инструменту, резолюция имени в
Postgres, самокоррекция по кандидатам, учёт стоимости) проверяется настоящее.

Качество формулировок ответа здесь не проверяется — это работа eval-сьюта (спринт 5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import litellm
import pytest
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
    Message,
    ModelResponse,
    Usage,
)
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent import tools as tool_registry
from court_vision.agent.errors import (
    AgentStepLimitReached,
    IncompleteAnswer,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from court_vision.agent.loop import Turn, answer
from court_vision.agent.models import LlmUsage
from court_vision.config import Settings

pytestmark = pytest.mark.usefixtures("seeded_database")

MODEL = "anthropic/claude-haiku-4-5"
RESPONSE_MODEL = "claude-haiku-4-5-20251001"

QUESTION = [Turn(role="user", content="Сколько набирал Леброн в 2012-13?")]


@pytest.fixture
def agent_settings() -> Settings:
    """Потолок шагов занижен: тест на зацикливание не должен ждать шесть вызовов."""
    return Settings(llm_model=MODEL, llm_max_steps=3)


def tool_call_reply(name: str, arguments: str, *, call_id: str = "call_1") -> ModelResponse:
    """Ответ модели с запросом Инструмента. `arguments` — строка: модель присылает JSON
    текстом, и тесты обязаны иметь возможность прислать сюда что угодно."""
    message = Message(
        content=None,
        role="assistant",
        tool_calls=[
            ChatCompletionMessageToolCall(
                id=call_id, type="function", function=Function(name=name, arguments=arguments)
            )
        ],
    )
    return _response(message, finish_reason="tool_calls")


def text_reply(text: str, *, finish_reason: str = "stop") -> ModelResponse:
    return _response(Message(content=text, role="assistant"), finish_reason=finish_reason)


def _response(message: Message, *, finish_reason: str) -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-test",
        choices=[Choices(finish_reason=finish_reason, index=0, message=message)],
        # Модель из ответа отличается от запрошенной строки litellm — по ней litellm и
        # считает стоимость.
        model=RESPONSE_MODEL,
        usage=Usage(prompt_tokens=1200, completion_tokens=80, total_tokens=1280),
    )


@dataclass
class FakeProvider:
    """Подставной провайдер: отдаёт заготовленные ответы по порядку и запоминает запросы.

    Запомненные `calls` — главный предмет проверки: только по ним видно, что именно доехало
    до модели на следующем шаге.
    """

    replies: list[ModelResponse | Exception]
    repeat_last: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, **kwargs: Any) -> ModelResponse:
        # Копия истории, а не ссылка: цикл дописывает в тот же список, и без копии каждый
        # запомненный запрос показывал бы состояние на конец прогона, а не на момент вызова.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        index = len(self.calls) - 1
        if index >= len(self.replies):
            if not self.repeat_last:
                raise AssertionError("модель вызвана больше раз, чем ожидал тест")
            index = len(self.replies) - 1
        reply = self.replies[index]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def tool_results(self) -> list[dict[str, Any]]:
        """Результаты Инструментов, доехавшие до модели, — из последнего запроса."""
        return [
            json.loads(message["content"])
            for message in self.calls[-1]["messages"]
            if message.get("role") == "tool"
        ]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Ставит подставного провайдера на границу litellm. Заготовки складывает тест."""

    def _install(*replies: ModelResponse | Exception, repeat_last: bool = False) -> FakeProvider:
        fake = FakeProvider(replies=list(replies), repeat_last=repeat_last)
        monkeypatch.setattr(litellm, "acompletion", fake)
        return fake

    return _install


async def usage_rows(session: AsyncSession) -> list[LlmUsage]:
    result = await session.execute(select(LlmUsage).order_by(LlmUsage.id))
    return list(result.scalars())


async def test_вопрос_проходит_через_инструмент_к_финальному_ответу(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    fake = provider(
        tool_call_reply(
            "get_player_stats",
            json.dumps({"player_name": "LeBron James", "season": "2012-13"}),
        ),
        text_reply("В сезоне 2012-13 Леброн набирал 26.8 очка за игру."),
    )

    result = await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert result.text.startswith("В сезоне 2012-13")
    assert len(fake.calls) == 2

    invocation = result.tool_invocations[0]
    assert invocation.name == "get_player_stats"
    assert invocation.arguments == {"player_name": "LeBron James", "season": "2012-13"}
    # Цифры в результате — настоящие, из Postgres, а не из подставного ответа модели.
    assert invocation.result["seasons"][0]["averages"]["points"] == 26.8

    tool_result = fake.tool_results()[0]
    assert tool_result["player"]["full_name"] == "LeBron James"
    assert tool_result["seasons"][0]["totals"]["points"] == 2036


async def test_инструменты_объявлены_модели(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    fake = provider(text_reply("Привет."))

    await answer(session_factory, history=QUESTION, settings=agent_settings)

    tools = fake.calls[0]["tools"]
    assert [tool["function"]["name"] for tool in tools] == ["get_player_stats"]
    parameters = tools[0]["function"]["parameters"]
    assert parameters["required"] == ["player_name"]
    # Требование латиницы — часть контракта Инструмента: без него модель шлёт кириллицу,
    # которую резольвер не находит в принципе.
    assert "латиницей" in tools[0]["function"]["description"]


async def test_история_диалога_целиком_уезжает_модели(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """Follow-up «а в 2015-16?» работает только если предыдущие реплики доехали."""
    fake = provider(text_reply("В 2015-16 — 25.3 очка."))

    await answer(
        session_factory,
        history=[
            Turn(role="user", content="Сколько набирал Леброн в 2012-13?"),
            Turn(role="assistant", content="26.8 очка за игру."),
            Turn(role="user", content="А в 2015-16?"),
        ],
        settings=agent_settings,
    )

    messages = fake.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert [message["role"] for message in messages[1:]] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "А в 2015-16?"


async def test_пустая_история_не_доходит_до_провайдера(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """Пустой Диалог — ошибка вызывающего, и платить за отказ провайдера незачем."""
    fake = provider()

    with pytest.raises(ValueError):
        await answer(session_factory, history=[], settings=agent_settings)

    assert fake.calls == []


async def test_не_найдено_с_кандидатами_доезжает_до_модели_результатом(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """Неудачная резолюция — не исключение, а ход диалога: цикл продолжается."""
    fake = provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "Michael Jordan"})),
        text_reply("Майкла Джордана в данных нет — статистика начинается с сезона 2005-06."),
    )

    result = await answer(
        session_factory,
        history=[Turn(role="user", content="Статистика Джордана?")],
        settings=agent_settings,
    )

    assert result.text.startswith("Майкла Джордана")
    failure = result.tool_invocations[0].result["error"]
    assert failure["kind"] == "player-not-found"
    assert failure["nearest"], "без кандидатов модели не от чего оттолкнуться"

    # Тот же структурный ответ обязан быть виден модели — иначе исправляться ей не по чему.
    assert fake.tool_results()[0]["error"]["kind"] == "player-not-found"
    assert "Michael Curry" in {
        candidate["full_name"] for candidate in fake.tool_results()[0]["error"]["nearest"]
    }


async def test_модель_самокорректируется_вторым_вызовом(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """Многошаговый цикл целиком: неудачный вызов → структурная причина → второй вызов → цифры.

    Карри задрафтован в 2009-м, и сезона 2005-06 у него нет. Модель узнаёт об этом из
    результата Инструмента и переспрашивает без сезона — без исключений и без обрыва цикла.
    """
    fake = provider(
        tool_call_reply(
            "get_player_stats",
            json.dumps({"player_name": "Stephen Curry", "season": "2005-06"}),
        ),
        tool_call_reply(
            "get_player_stats", json.dumps({"player_name": "Stephen Curry"}), call_id="call_2"
        ),
        text_reply("В 2005-06 Карри ещё не играл в НБА; его первый сезон — 2009-10."),
    )

    result = await answer(
        session_factory,
        history=[Turn(role="user", content="Карри в 2005-06?")],
        settings=agent_settings,
    )

    assert len(fake.calls) == 3
    failure = result.tool_invocations[0].result["error"]
    assert failure["kind"] == "season-data-missing"
    assert failure["player"]["full_name"] == "Stephen Curry"

    # Второй вызов уже без сезона — и до модели доехали настоящие сезоны карьеры.
    assert fake.tool_results()[1]["player"]["full_name"] == "Stephen Curry"
    assert fake.tool_results()[1]["seasons"]


async def test_кириллица_в_имени_не_роняет_цикл(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    fake = provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "Леброн Джеймс"})),
        text_reply("Уточните имя латиницей."),
    )

    result = await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert result.tool_invocations[0].result["error"]["kind"] == "player-not-found"
    assert fake.tool_results()[0]["error"]["nearest"] == []


async def test_невалидные_аргументы_возвращаются_модели_а_не_падают(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    provider(
        tool_call_reply(
            "get_player_stats", json.dumps({"player_name": "LeBron", "season": "2012"})
        ),
        tool_call_reply("get_player_stats", "не json", call_id="call_2"),
        text_reply("Готово."),
    )

    result = await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert result.tool_invocations[0].result["error"]["kind"] == "invalid-arguments"
    assert result.tool_invocations[0].result["error"]["errors"]
    assert result.tool_invocations[1].arguments is None
    assert result.tool_invocations[1].result["error"]["kind"] == "invalid-arguments"


async def test_несуществующий_инструмент_возвращается_модели(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    provider(
        tool_call_reply("search_corpus", "{}"),
        text_reply("Такого я пока не умею."),
    )

    result = await answer(session_factory, history=QUESTION, settings=agent_settings)

    failure = result.tool_invocations[0].result["error"]
    assert failure["kind"] == "unknown-tool"
    assert failure["available_tools"] == ["get_player_stats"]


async def test_упавший_инструмент_не_обрывает_ход_агента(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обещание «Инструмент не бросает» держится и когда падает не он сам, а БД под ним.

    Единственное место, где тест подменяет свой код, а не границу litellm: сбой соединения
    иначе не воспроизвести, а обещание без проверки — просто комментарий.
    """

    async def _explode(*args: Any, **kwargs: Any) -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection reset"))

    monkeypatch.setattr(tool_registry, "get_player_stats", _explode)
    provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "LeBron James"})),
        text_reply("Статистика сейчас недоступна."),
    )

    result = await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert result.text == "Статистика сейчас недоступна."
    assert result.tool_invocations[0].result["error"]["kind"] == "tool-failed"


async def test_число_шагов_цикла_ограничено(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """Модель, бесконечно перевызывающая Инструмент, обязана упереться в потолок."""
    fake = provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "LeBron James"})),
        repeat_last=True,
    )

    with pytest.raises(AgentStepLimitReached) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert len(fake.calls) == agent_settings.llm_max_steps
    assert raised.value.extensions() == {"max_steps": agent_settings.llm_max_steps}


async def test_оборванный_ответ_не_выдаётся_за_готовый(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """`finish_reason=length` — обрыв на полуслове: «набирал 26.» соврало бы цифрой."""
    provider(text_reply("В сезоне 2012-13 Леброн набирал 26.", finish_reason="length"))

    with pytest.raises(IncompleteAnswer) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions() == {"reason": "truncated"}


async def test_пустой_ответ_модели_не_выдаётся_за_ответ(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    provider(text_reply("   "))

    with pytest.raises(IncompleteAnswer) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions() == {"reason": "empty"}


async def test_ошибка_провайдера_превращается_в_доменную(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    provider(
        litellm.exceptions.APIConnectionError(
            message="connection refused", llm_provider="anthropic", model=MODEL
        )
    )

    with pytest.raises(ProviderUnavailable) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.status == 502
    assert raised.value.extensions() == {"model": MODEL, "cause": "APIConnectionError"}


async def test_отвергнутый_запрос_отличается_от_недоступности(
    session_factory: async_sessionmaker[AsyncSession], agent_settings: Settings, provider: Any
) -> None:
    """Опечатка в `LLM_MODEL` — не сбой провайдера: повторами она не лечится."""
    provider(
        litellm.exceptions.BadRequestError(
            message="LLM Provider NOT provided", llm_provider="anthropic", model=MODEL
        )
    )

    with pytest.raises(ProviderRequestRejected) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.status == 500
    assert raised.value.extensions() == {"model": MODEL, "cause": "BadRequestError"}


async def test_ответ_без_вариантов_остаётся_доменной_ошибкой(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: Any,
    session: AsyncSession,
) -> None:
    """200 без choices (срез фильтром, сбой шлюза) не должен выходить наружу IndexError'ом."""
    provider(
        ModelResponse(
            id="chatcmpl-test",
            choices=[],
            model=RESPONSE_MODEL,
            usage=Usage(prompt_tokens=1200, completion_tokens=0, total_tokens=1200),
        )
    )

    with pytest.raises(ProviderUnavailable) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions()["cause"] == "EmptyResponse"
    # Вызов состоялся и был оплачен — учесть его обязаны.
    assert len(await usage_rows(session)) == 1


async def test_каждый_вызов_модели_пишет_строку_учёта(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: Any,
    session: AsyncSession,
) -> None:
    provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "LeBron James"})),
        text_reply("26.8 очка."),
    )

    await answer(session_factory, history=QUESTION, settings=agent_settings)

    rows = await usage_rows(session)
    assert len(rows) == 2
    for row in rows:
        assert row.model == MODEL
        assert row.response_model == RESPONSE_MODEL
        assert row.operation == "agent.step"
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (1200, 80, 1280)
        # Стоимость считает litellm по своему прайслисту — своих цен в репозитории нет.
        assert row.cost_usd is not None and row.cost_usd > 0
        assert row.latency_ms >= 0


async def test_учёт_переживает_провалившийся_ход_агента(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: Any,
    session: AsyncSession,
) -> None:
    """Оплаченные вызовы обязаны остаться в учёте, даже если ход агента закончился ошибкой:
    именно провалившиеся ходы стоят дороже всего, и терять их в дашборде стоимости нельзя."""
    provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "LeBron James"})),
        repeat_last=True,
    )

    with pytest.raises(AgentStepLimitReached):
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert len(await usage_rows(session)) == agent_settings.llm_max_steps
