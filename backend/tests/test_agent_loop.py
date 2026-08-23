"""Механика агентного цикла на настоящих данных и настоящих Инструментах.

Подменяется ровно одна вещь — `litellm.acompletion`, граница провайдера: живых LLM-вызовов
в CI быть не должно, а всё остальное (маршрутизация к Инструменту, резолюция имени в
Postgres, самокоррекция по кандидатам, учёт стоимости) проверяется настоящее.

Качество формулировок ответа здесь не проверяется — это работа eval-сьюта (спринт 5).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import aclosing
from typing import Any

import litellm
import pytest
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
from court_vision.agent.evidence import TableView
from court_vision.agent.loop import (
    AgentAnswer,
    TokenChunk,
    ToolCalled,
    ToolResulted,
    Turn,
    stream_answer,
)
from court_vision.agent.models import LlmUsage
from court_vision.config import Settings
from tests.conftest import (
    MODEL,
    RESPONSE_MODEL,
    ProviderFactory,
    empty_reply,
    preamble_tool_call_reply,
    text_reply,
    tool_call_reply,
)

pytestmark = pytest.mark.usefixtures("seeded_database")

QUESTION = [Turn(role="user", content="Сколько набирал Леброн в 2012-13?")]


@pytest.fixture
def agent_settings() -> Settings:
    """Потолок шагов занижен: тест на зацикливание не должен ждать шесть вызовов."""
    return Settings(llm_model=MODEL, llm_max_steps=3)


async def answer(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    history: Sequence[Turn],
    settings: Settings,
) -> AgentAnswer:
    """Вычерпывает цикл до итога.

    Помощник тестов, а не часть цикла: продакшн-потребитель у цикла один — SSE, и ему нужны
    все события. Здесь же почти каждый тест проверяет именно итог, и разбор потока в каждом
    из них закрывал бы собой предмет проверки.
    """
    async with aclosing(
        stream_answer(session_factory, history=history, settings=settings)
    ) as events:
        async for event in events:
            if isinstance(event, AgentAnswer):
                return event
    raise AssertionError("Цикл закончился без ответа и без ошибки.")


async def usage_rows(session: AsyncSession) -> list[LlmUsage]:
    result = await session.execute(select(LlmUsage).order_by(LlmUsage.id))
    return list(result.scalars())


async def test_вопрос_проходит_через_инструмент_к_финальному_ответу(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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


async def test_цикл_отдаёт_события_по_мере_появления(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """Стриминг по-настоящему: текст приезжает частями, вызов Инструмента виден до ответа,
    а склейка отданных фрагментов совпадает с итоговым текстом — именно он и сохраняется
    Репликой."""
    provider(
        preamble_tool_call_reply(
            "Секунду.", "get_player_stats", json.dumps({"player_name": "LeBron James"})
        ),
        text_reply("В сезоне 2012-13 Леброн набирал 26.8 очка за игру."),
    )

    events = [
        event
        async for event in stream_answer(session_factory, history=QUESTION, settings=agent_settings)
    ]

    tool_calls = [event for event in events if isinstance(event, ToolCalled)]
    tokens = [event for event in events if isinstance(event, TokenChunk)]
    assert [call.name for call in tool_calls] == ["get_player_stats"]
    assert len(tokens) > 1, "ответ пришёл одним куском — это не поток"
    # Преамбула проговорена до вызова Инструмента, вызов — до ответа.
    assert events.index(tokens[0]) < events.index(tool_calls[0]) < events.index(tokens[-1])

    final = events[-1]
    assert isinstance(final, AgentAnswer)
    assert "".join(token.text for token in tokens) == final.text
    # Преамбула и ответ — разные абзацы: без разделителя они склеились бы в одну фразу.
    assert final.text == "Секунду.\n\nВ сезоне 2012-13 Леброн набирал 26.8 очка за игру."


async def test_инструменты_объявлены_модели(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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


async def test_адрес_и_ключ_шлюза_доезжают_до_провайдера(
    session_factory: async_sessionmaker[AsyncSession],
    provider: ProviderFactory,
) -> None:
    """Модель через OpenAI-совместимый шлюз — это `openai/` в строке модели плюс адрес.

    Потеряется адрес — запрос уедет по адресу OpenAI по умолчанию, где этой модели нет, и
    отвалится не на конфиге, а ошибкой провайдера на живом вопросе.
    """
    settings = Settings(
        llm_model="openai/anthropic/claude-haiku-4.5",
        llm_api_base="https://gateway.example/v1",
        llm_api_key="sk-gateway",
        llm_max_steps=3,
    )
    fake = provider(text_reply("Привет."))

    await answer(session_factory, history=QUESTION, settings=settings)

    assert fake.calls[0]["model"] == "openai/anthropic/claude-haiku-4.5"
    assert fake.calls[0]["api_base"] == "https://gateway.example/v1"
    assert fake.calls[0]["api_key"] == "sk-gateway"


async def test_без_настроек_шлюза_провайдер_ищет_ключ_сам(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """None, а не пустая строка: по None litellm возвращается к своему поиску ключа в
    окружении, а пустая строка уехала бы в заголовок авторизации как есть."""
    fake = provider(text_reply("Привет."))

    await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert fake.calls[0]["api_key"] is None
    assert fake.calls[0]["api_base"] is None


async def test_история_диалога_целиком_уезжает_модели(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """Пустой Диалог — ошибка вызывающего, и платить за отказ провайдера незачем."""
    fake = provider()

    with pytest.raises(ValueError):
        await answer(session_factory, history=[], settings=agent_settings)

    assert fake.calls == []


async def test_не_найдено_с_кандидатами_доезжает_до_модели_результатом(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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


async def test_результат_инструмента_отдаётся_вторым_событием_с_проекцией(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """Карточка Evidence живёт в двух состояниях: сначала ожидание, потом результат.

    Проекция — таблица по сезону на строку с настоящими цифрами из Postgres: без неё рейка
    показывает аргументы вызова, то есть не то, что обещает §7.2 дизайн-системы.
    """
    provider(
        tool_call_reply(
            "get_player_stats",
            json.dumps({"player_name": "LeBron James", "season": "2012-13"}),
        ),
        text_reply("В сезоне 2012-13 Леброн набирал 26.8 очка за игру."),
    )

    events = [
        event
        async for event in stream_answer(session_factory, history=QUESTION, settings=agent_settings)
    ]

    called = [event for event in events if isinstance(event, ToolCalled)]
    resulted = [event for event in events if isinstance(event, ToolResulted)]
    assert [event.id for event in resulted] == [event.id for event in called]
    assert events.index(called[0]) < events.index(resulted[0])

    outcome = resulted[0]
    assert outcome.failure is None
    assert outcome.duration_ms >= 0
    assert outcome.view is not None
    view = outcome.view.root
    assert isinstance(view, TableView)
    assert view.columns == ["Season", "GP", "Pts", "Reb", "Ast", "FG%"]
    assert view.rows == [["2012-13", "76", "26.8", "8.0", "7.3", "56.5"]]


async def test_отказ_инструмента_виден_в_карточке_а_не_выглядит_удачным_вызовом(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """Сезона 2005-06 у Карри нет. Без исхода в событии эта карточка в рейке выглядела бы
    ровно как та, что принесла карьеру Леброна, — «Агент сходил за данными» без данных."""
    provider(
        tool_call_reply(
            "get_player_stats",
            json.dumps({"player_name": "Stephen Curry", "season": "2005-06"}),
        ),
        text_reply("В 2005-06 Карри ещё не играл в НБА."),
    )

    events = [
        event
        async for event in stream_answer(
            session_factory,
            history=[Turn(role="user", content="Карри в 2005-06?")],
            settings=agent_settings,
        )
    ]

    outcome = next(event for event in events if isinstance(event, ToolResulted))
    assert outcome.failure is not None
    assert outcome.failure.kind == "season-data-missing"
    assert outcome.failure.detail
    # У отказа проекции нет: показывать в карточке нечего, кроме самого отказа.
    assert outcome.view is None


async def test_кириллица_в_имени_не_роняет_цикл(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    fake = provider(
        tool_call_reply("get_player_stats", json.dumps({"player_name": "Леброн Джеймс"})),
        text_reply("Уточните имя латиницей."),
    )

    result = await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert result.tool_invocations[0].result["error"]["kind"] == "player-not-found"
    assert fake.tool_results()[0]["error"]["nearest"] == []


async def test_невалидные_аргументы_возвращаются_модели_а_не_падают(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    provider: ProviderFactory,
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
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """`finish_reason=length` — обрыв на полуслове: «набирал 26.» соврало бы цифрой."""
    provider(text_reply("В сезоне 2012-13 Леброн набирал 26.", finish_reason="length"))

    with pytest.raises(IncompleteAnswer) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions() == {"reason": "truncated"}


async def test_преамбула_без_ответа_не_выдаётся_за_ответ(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """Модель сказала «сейчас посмотрю», сходила за данными и вернулась ни с чем.

    Преамбула уже прочитана пользователем, но ответом она не является: отдать её за готовый
    ответ — то же самое, что отдать пустую строку, только незаметнее.
    """
    provider(
        preamble_tool_call_reply(
            "Секунду, посмотрю статистику.",
            "get_player_stats",
            json.dumps({"player_name": "LeBron James"}),
        ),
        text_reply("", pieces=1),
    )

    with pytest.raises(IncompleteAnswer) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions() == {"reason": "empty"}


async def test_брошенный_поток_закрывает_поток_провайдера(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    """Пользователь ушёл со страницы посреди ответа: соединение с провайдером обязано
    закрыться сразу, а не дожидаться сборки мусора."""
    fake = provider(text_reply("В сезоне 2012-13 Леброн набирал 26.8 очка за игру."))

    async with aclosing(
        stream_answer(session_factory, history=QUESTION, settings=agent_settings)
    ) as events:
        async for event in events:
            if isinstance(event, TokenChunk):
                break

    assert fake.closed, "поток провайдера остался открытым"


async def test_пустой_ответ_модели_не_выдаётся_за_ответ(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
) -> None:
    provider(text_reply("   "))

    with pytest.raises(IncompleteAnswer) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions() == {"reason": "empty"}


async def test_ошибка_провайдера_превращается_в_доменную(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    provider: ProviderFactory,
    session: AsyncSession,
) -> None:
    """Поток без вариантов ответа (срез фильтром, сбой шлюза) не должен выходить наружу
    ни IndexError'ом, ни ошибкой сборки чанков из недр litellm."""
    provider(empty_reply())

    with pytest.raises(ProviderUnavailable) as raised:
        await answer(session_factory, history=QUESTION, settings=agent_settings)

    assert raised.value.extensions()["cause"] == "EmptyResponse"
    # Вызов состоялся и был оплачен — учесть его обязаны.
    assert len(await usage_rows(session)) == 1


async def test_каждый_вызов_модели_пишет_строку_учёта(
    session_factory: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    provider: ProviderFactory,
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
    provider: ProviderFactory,
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
