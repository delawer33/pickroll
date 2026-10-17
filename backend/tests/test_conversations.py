"""Диалоги и SSE-чат через HTTP — сквозной срез: запрос, поток, сохранённые Реплики.

Подменяется ровно одна вещь — граница litellm; всё остальное настоящее: Postgres, auth,
резолюция имени, агентный цикл, формат событий. Живых LLM-вызовов в CI быть не должно.
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import litellm
import pytest
from fastapi import FastAPI
from litellm.types.utils import EmbeddingResponse, Usage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.models import LlmUsage
from court_vision.conversations.service import FALLBACK_TITLE, append_message, create_conversation
from court_vision.corpus.models import Chunk, Document
from tests.conftest import (
    MODEL,
    ProviderFactory,
    TokenFactory,
    text_reply,
    tool_call_reply,
    truncated_reply,
)

pytestmark = pytest.mark.usefixtures("seeded_database")

CONVERSATIONS = "/api/v1/conversations"
QUESTION = "Сколько в среднем набирал Леброн в 2012-13?"
ANSWER = "В сезоне 2012-13 Леброн набирал 26.8 очка за игру."


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """Тот же тестовый app, но с заниженным потолком шагов — как в тестах цикла."""
    app.state.settings = app.state.settings.model_copy(
        update={"llm_model": MODEL, "llm_max_steps": 3}
    )
    return app


@pytest.fixture
async def other_client(app: FastAPI, make_token: TokenFactory) -> AsyncIterator[httpx.AsyncClient]:
    """Второй залогиненный пользователь — им проверяется изоляция Диалогов."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {make_token('user_other')}"},
    ) as client:
        yield client


async def new_conversation(client: httpx.AsyncClient, title: str | None = None) -> str:
    response = await client.post(CONVERSATIONS, json={"title": title})
    assert response.status_code == 201, response.text
    return str(response.json()["conversation_id"])


async def ask(
    client: httpx.AsyncClient, conversation_id: str, question: str
) -> list[dict[str, Any]]:
    """Задаёт вопрос и вычерпывает поток до конца. Возвращает разобранные события."""
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST", f"{CONVERSATIONS}/{conversation_id}/messages", json={"content": question}
    ) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    return events


def answer_replies() -> tuple[list[Any], list[Any]]:
    """Заготовка обычного хода: вызов Инструмента, затем текстовый ответ."""
    return (
        tool_call_reply(
            "get_player_stats", json.dumps({"player_name": "LeBron James", "season": "2012-13"})
        ),
        text_reply(ANSWER),
    )


async def test_ход_диалога_отдаётся_потоком_событий_до_done(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client)

    events = await ask(api_client, conversation_id, QUESTION)

    kinds = [event["type"] for event in events]
    assert kinds[0] == "tool_call", "поход за данными обязан быть виден до первого токена"
    assert kinds[-1] == "done"
    assert kinds.count("token") > 1, "ответ пришёл одним куском — это не поток"
    assert "error" not in kinds

    assert events[0]["name"] == "get_player_stats"
    assert events[0]["arguments"] == {"player_name": "LeBron James", "season": "2012-13"}

    # Результат вызова — второе событие про тот же `tool_call_id`: карточка Evidence
    # показывает то, что Инструмент принёс, а не то, чем его позвали.
    result = next(event for event in events if event["type"] == "tool_result")
    assert result["tool_call_id"] == events[0]["tool_call_id"]
    assert result["error"] is None
    assert result["duration_ms"] >= 0
    assert result["view"]["kind"] == "table"
    assert result["view"]["rows"] == [["2012-13", "76", "26.8", "8.0", "7.3", "56.5"]]
    assert kinds.index("tool_result") < kinds.index("done")

    streamed = "".join(event["text"] for event in events if event["type"] == "token")
    assert streamed == ANSWER
    assert events[-1]["message_id"] > 0


async def test_реплики_сохраняются_и_видны_после_перезагрузки(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Перезагрузка страницы обязана показать ровно то, что пользователь читал в потоке."""
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client)
    events = await ask(api_client, conversation_id, QUESTION)

    response = await api_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")

    assert response.status_code == 200
    messages = response.json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == QUESTION
    assert messages[1]["content"] == ANSWER
    assert messages[1]["message_id"] == events[-1]["message_id"]


async def test_заголовок_диалога_берётся_из_первого_вопроса(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Иначе в списке столбик одинаковых «Новых диалогов», в котором свой не найти."""
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client)
    assert (await api_client.get(CONVERSATIONS)).json()["items"][0]["title"] == FALLBACK_TITLE

    await ask(api_client, conversation_id, QUESTION)

    assert (await api_client.get(CONVERSATIONS)).json()["items"][0]["title"] == QUESTION


async def test_follow_up_работает_без_имени_игрока(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """«А в 2015-16?» осмысленно только вместе с историей: агенту уезжает весь Диалог."""
    fake = provider(
        *answer_replies(),
        tool_call_reply(
            "get_player_stats",
            json.dumps({"player_name": "LeBron James", "season": "2015-16"}),
            call_id="call_2",
        ),
        text_reply("В 2015-16 — 25.3 очка."),
    )
    conversation_id = await new_conversation(api_client)
    await ask(api_client, conversation_id, QUESTION)

    events = await ask(api_client, conversation_id, "А в 2015-16?")

    assert [event["type"] for event in events][-1] == "done"
    # Третий вызов модели — первый шаг второго хода: в нём и видно, что доехало в контекст.
    history = [
        (message["role"], message.get("content"))
        for message in fake.calls[2]["messages"]
        if message["role"] in {"user", "assistant"}
    ]
    assert history == [("user", QUESTION), ("assistant", ANSWER), ("user", "А в 2015-16?")]


async def test_чужой_диалог_не_раскрывает_данные(
    api_client: httpx.AsyncClient, other_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Ровное 404 на все три обращения: 403 подтверждал бы, что такой Диалог существует."""
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client)
    await ask(api_client, conversation_id, QUESTION)

    messages = await other_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")
    posted = await other_client.post(
        f"{CONVERSATIONS}/{conversation_id}/messages", json={"content": "Покажи чужой диалог"}
    )
    listed = await other_client.get(CONVERSATIONS)

    assert messages.status_code == 404
    assert QUESTION not in messages.text and ANSWER not in messages.text
    # Отказ приходит обычным Problem Details, а не событием внутри `200 OK`: ответа ещё нет.
    assert posted.status_code == 404
    assert posted.headers["content-type"].startswith("application/problem+json")
    assert listed.json()["items"] == []


async def test_ошибка_провайдера_доходит_событием_а_не_обрывом(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Оборванный поток клиент не отличил бы от сетевого сбоя и предлагал бы повтор там,
    где повторять нечего."""
    provider(
        litellm.exceptions.APIConnectionError(
            message="connection refused", llm_provider="anthropic", model=MODEL
        )
    )
    conversation_id = await new_conversation(api_client)

    events = await ask(api_client, conversation_id, QUESTION)

    assert [event["type"] for event in events] == ["error"]
    problem = events[0]["problem"]
    assert problem["status"] == 502
    assert problem["type"].endswith("llm-provider-unavailable")
    # Расширяющие члены Problem Details — часть формата и доезжают до клиента.
    assert problem["cause"] == "APIConnectionError"

    # Вопрос остался в Диалоге: он сохранён до начала потока, и повтор виден пользователю.
    messages = (await api_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()
    assert [message["role"] for message in messages["items"]] == ["user"]


async def test_обрыв_посреди_ответа_тоже_становится_событием(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Провайдер отвалился, когда часть ответа уже прочитана: статус `200` и заголовки ушли
    к клиенту, подменить ответ на 502 нечем — остаётся событие `error` в том же потоке."""
    provider(
        truncated_reply(
            "Сейчас посмотрю",
            litellm.exceptions.APIConnectionError(
                message="connection reset", llm_provider="anthropic", model=MODEL
            ),
        )
    )
    conversation_id = await new_conversation(api_client)

    events = await ask(api_client, conversation_id, QUESTION)

    assert [event["type"] for event in events] == ["token", "error"]
    assert events[-1]["problem"]["status"] == 502

    # Недописанный ответ Репликой не становится: оборванная фраза выглядела бы как готовая.
    messages = (await api_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()
    assert [message["role"] for message in messages["items"]] == ["user"]


async def test_список_диалогов_пагинируется_курсором(
    api_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Каждый Диалог в своей транзакции: `now()` в Postgres — время начала транзакции, и
    # созданные одним коммитом Диалоги делили бы одну метку времени. В проде каждый из них
    # приезжает своим запросом.
    for index in range(3):
        async with session_factory() as session:
            await create_conversation(session, clerk_user_id="user_test", title=f"Диалог {index}")
            await session.commit()

    first = (await api_client.get(CONVERSATIONS, params={"limit": 2})).json()
    second = (
        await api_client.get(CONVERSATIONS, params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()

    assert [item["title"] for item in first["items"]] == ["Диалог 2", "Диалог 1"]
    assert [item["title"] for item in second["items"]] == ["Диалог 0"]
    # Последняя страница без курсора: иначе клиент делает лишний круг за пустотой.
    assert second["next_cursor"] is None


async def test_реплики_пагинируются_курсором(
    api_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Реплики читают как переписку, сверху вниз: первая страница — начало разговора."""
    async with session_factory() as session:
        conversation = await create_conversation(
            session, clerk_user_id="user_test", title="Долгий разговор"
        )
        conversation_id = conversation.conversation_id
        for index in range(3):
            await append_message(
                session, conversation_id=conversation_id, role="user", content=f"Вопрос {index}"
            )
        await session.commit()

    url = f"{CONVERSATIONS}/{conversation_id}/messages"
    first = (await api_client.get(url, params={"limit": 2})).json()
    second = (await api_client.get(url, params={"limit": 2, "cursor": first["next_cursor"]})).json()

    assert [item["content"] for item in first["items"]] == ["Вопрос 0", "Вопрос 1"]
    assert [item["content"] for item in second["items"]] == ["Вопрос 2"]
    assert second["next_cursor"] is None


def cursor_of(*parts: object) -> str:
    """Курсор, собранный руками — так подделывают его снаружи."""
    return urlsafe_b64encode("\x1f".join(str(part) for part in parts).encode()).decode()


@pytest.mark.parametrize(
    ("case", "cursor"),
    [
        ("мусор вместо base64", "не курсор"),
        ("курсор другого списка", cursor_of(42)),
        ("нечитаемый идентификатор", cursor_of("2026-08-18T12:00:00+00:00", "не uuid")),
        # Метка без зоны драйвер истолковал бы по часам процесса, и граница страницы
        # сдвинулась бы на смещение — молча, без ошибки.
        ("метка времени без зоны", cursor_of("2026-08-18T12:00:00", str(uuid4()))),
    ],
)
async def test_подделанный_курсор_отвергается_а_не_молча_листается(
    api_client: httpx.AsyncClient, case: str, cursor: str
) -> None:
    """Молча отданная не та страница зациклила бы листающего клиента или скрыла бы от него
    часть списка."""
    response = await api_client.get(CONVERSATIONS, params={"cursor": cursor})

    assert response.status_code == 400, case
    assert response.json()["type"].endswith("invalid-cursor"), case


async def test_курсор_за_пределами_типа_остаётся_ошибкой_запроса(
    api_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Число, не влезающее в bigint, роняло бы запрос в драйвере — то есть 500 на подделанном
    параметре строки запроса."""
    conversation = await create_conversation(session, clerk_user_id="user_test", title="Пусто")
    await session.commit()
    url = f"{CONVERSATIONS}/{conversation.conversation_id}/messages"

    response = await api_client.get(url, params={"cursor": cursor_of("9" * 30)})

    assert response.status_code == 400
    assert response.json()["type"].endswith("invalid-cursor")


async def test_пустой_курсор_означает_первую_страницу(
    api_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Сгенерированный клиент подставляет `?cursor=` для пустого `next_cursor` — это первая
    страница, а не битый курсор."""
    await create_conversation(session, clerk_user_id="user_test", title="Единственный")
    await session.commit()

    response = await api_client.get(CONVERSATIONS, params={"cursor": ""})

    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["Единственный"]


async def test_свой_заголовок_не_затирается_первым_вопросом(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Заглушка живёт в NULL, а не в строке: пользователь вправе назвать Диалог как угодно,
    в том числе ровно так же, как называется заглушка."""
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client, title=FALLBACK_TITLE)

    await ask(api_client, conversation_id, QUESTION)

    assert (await api_client.get(CONVERSATIONS)).json()["items"][0]["title"] == FALLBACK_TITLE


async def test_диалоги_требуют_аутентификации(anonymous_client: httpx.AsyncClient) -> None:
    assert (await anonymous_client.get(CONVERSATIONS)).status_code == 401


async def test_события_потока_описаны_в_openapi(app: FastAPI) -> None:
    """TS-типы фронтенда генерируются из OpenAPI, значит контракт потока обязан быть там —
    вместе со всеми типами событий и дискриминатором."""
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["ChatStreamEvent"]["discriminator"]["mapping"]) == {
        "token",
        "tool_call",
        "tool_result",
        "citation",
        "done",
        "error",
    }
    # Проекция результата — тоже объединение с дискриминатором: разбор карточки Evidence
    # генератор TS выводит из него, а не из строки в документации.
    assert set(schemas["EvidenceView"]["discriminator"]["mapping"]) == {
        "table",
        "bars",
        "comparison",
        "quote",
        "quotes",
        "similar",
        "report",
    }
    # Событие `citation` — факт цитаты целиком: маркер, вызов и чанк корпуса.
    assert set(schemas["CitationEvent"]["properties"]) >= {
        "index",
        "tool_call_id",
        "chunk_id",
        "document_title",
        "locator",
        "snippet",
        "source_url",
    }
    # Сохранённая Реплика возвращает те же факты цитат: F5 восстанавливает маркеры.
    assert "citations" in schemas["MessageResponse"]["properties"]

    stream = app.openapi()["paths"]["/api/v1/conversations/{conversation_id}/messages"]["post"]
    content = stream["responses"]["200"]["content"]
    assert "text/event-stream" in content
    # Канонический вид SSE в OpenAPI: `data` — JSON-строка, а её `contentSchema` и есть
    # объединение событий. Именно по нему генератор TS выводит разбор потока.
    event = content["text/event-stream"]["itemSchema"]["properties"]["data"]
    assert event["contentSchema"]["$ref"].endswith("ChatStreamEvent")


GOALTENDING_RULE = "Goaltending is touching the ball on its downward flight toward the basket."
CITED_ANSWER = "Goaltending — касание мяча на нисходящей траектории к кольцу [1]."


@pytest.fixture
async def rule_corpus(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Один документ rulebook с одним чанком — достаточно, чтобы цитата прошла насквозь."""
    async with session_factory() as session:
        rulebook = Document(
            source_type="rulebook",
            title="NBA Official Playing Rules",
            url="https://r",
            content_hash="hash-rulebook",
            fetched_at=datetime.now(UTC),
        )
        session.add(rulebook)
        await session.flush()
        session.add(
            Chunk(
                document_id=rulebook.id,
                locator="Rule 11 — Sec. I",
                text=GOALTENDING_RULE,
                embedding=[1.0] + [0.0] * 767,
                embedding_model="test/embedder",
            )
        )
        await session.commit()


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подставной `litellm.aembedding` — вектор запроса совпадает с вектором чанка."""

    async def fake(**kwargs: Any) -> EmbeddingResponse:
        texts: list[str] = kwargs["input"]
        return EmbeddingResponse(
            model="gemini-embedding-001",
            data=[
                {"embedding": [1.0] + [0.0] * 767, "index": index, "object": "embedding"}
                for index in range(len(texts))
            ],
            usage=Usage(prompt_tokens=7, completion_tokens=0, total_tokens=7),
        )

    monkeypatch.setattr(litellm, "aembedding", fake)


async def test_цитата_приходит_событием_до_done_и_переживает_перезагрузку(
    api_client: httpx.AsyncClient,
    provider: ProviderFactory,
    rule_corpus: None,
    embedder: None,
) -> None:
    """Сквозной путь спринта: вопрос про правило → цитата `[1]` событием потока → после F5
    те же факты цитат возвращаются вместе с Репликой."""
    provider(
        tool_call_reply(
            "search_corpus", json.dumps({"query": "goaltending rule", "source": "rulebook"})
        ),
        text_reply(CITED_ANSWER),
    )
    conversation_id = await new_conversation(api_client)

    events = await ask(api_client, conversation_id, "Что такое goaltending?")

    kinds = [event["type"] for event in events]
    assert "citation" in kinds
    # Цитаты — после текста (маркеры валидируются по финальному тексту), но строго до done.
    assert kinds.index("citation") > kinds.index("token")
    assert kinds.index("citation") < kinds.index("done")

    citation = next(event for event in events if event["type"] == "citation")
    assert citation["index"] == 1
    assert citation["tool_call_id"] == events[0]["tool_call_id"]
    assert citation["document_title"] == "NBA Official Playing Rules"
    assert citation["locator"] == "Rule 11 — Sec. I"
    assert citation["snippet"] == GOALTENDING_RULE
    assert citation["source_url"] == "https://r"
    assert citation["chunk_id"] > 0

    # Карточка Evidence — цитаты с номерами, а не дамп аргументов вызова.
    result = next(event for event in events if event["type"] == "tool_result")
    assert result["view"]["kind"] == "quotes"
    assert result["view"]["quotes"][0]["index"] == 1

    # Перезагрузка: сохранённая Реплика несёт ровно те же факты цитат, что и поток.
    messages = (await api_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()
    user_message, agent_message = messages["items"]
    assert user_message["citations"] == []
    assert agent_message["content"] == CITED_ANSWER
    assert agent_message["citations"] == [citation]


async def test_ответ_без_корпуса_обходится_без_событий_citation(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    """Обычный статистический ответ: цитат нет ни в потоке, ни в сохранённой Реплике."""
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client)

    events = await ask(api_client, conversation_id, QUESTION)

    assert "citation" not in [event["type"] for event in events]
    messages = (await api_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()
    assert [message["citations"] for message in messages["items"]] == [[], []]


async def test_история_диалога_не_включает_кухню_инструментов(
    api_client: httpx.AsyncClient, session: AsyncSession, provider: ProviderFactory
) -> None:
    """Реплика — это вопрос или ответ; вызовы Инструментов Репликой не являются (CONTEXT.md)."""
    provider(*answer_replies())
    conversation_id = await new_conversation(api_client)
    await ask(api_client, conversation_id, QUESTION)

    messages = (await api_client.get(f"{CONVERSATIONS}/{conversation_id}/messages")).json()

    assert len(messages["items"]) == 2
    assert all("get_player_stats" not in item["content"] for item in messages["items"])
    # Учёт при этом ведётся — по строке на каждый вызов модели, и оба шага хода в нём есть.
    usage = await session.execute(select(func.count()).select_from(LlmUsage))
    assert usage.scalar_one() == 2
