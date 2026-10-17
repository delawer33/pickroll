"""Скаутинг-отчёты: генерация, валидация, хранение, приватность, API и Инструменты агента.

Подменяется только граница litellm (`acompletion` и `aembedding`). Данные — синтетический
сезон из тестов Похожих игроков: шесть игроков разных амплуа, отпечатки пересобраны настоящим
кодом, так что вход отчёта, Перцентили и сопоставимые игроки — настоящие.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import litellm
import pytest
import structlog
from fastapi import FastAPI
from litellm.types.utils import EmbeddingResponse, Usage
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.evidence import ReportView
from court_vision.agent.loop import (
    AgentAnswer,
    CitationRegistry,
    ToolContext,
    ToolResulted,
    stream_answer,
)
from court_vision.agent.loop import Turn as AgentTurn
from court_vision.agent.models import LlmUsage
from court_vision.agent.tools import evidence_for, execute_tool
from court_vision.config import Settings
from court_vision.players.fingerprints import rebuild_fingerprints
from court_vision.reports.errors import ReportGenerationFailed
from court_vision.reports.generate import ReportSnapshot, check_grounding
from court_vision.reports.inputs import ReportInput, build_report_input
from court_vision.reports.models import ScoutingReport
from court_vision.reports.schemas import (
    Comparable,
    PartialComparable,
    PartialReportPoint,
    PartialScoutingReport,
    ReportPoint,
    ScoutingReportContent,
)
from court_vision.reports.service import produce_report
from tests.conftest import MODEL, ProviderFactory, text_reply, tool_call_reply
from tests.test_similar_players import CAST, _insert_season

pytestmark = pytest.mark.usefixtures("clean_database")

REPORTS = "/api/v1/reports"
USER = "user_test"
OTHER = "user_other"
SETTINGS = Settings(llm_model=MODEL)


def _basis(index: int) -> list[float]:
    vector = [0.0] * 768
    vector[index] = 1.0
    return vector


def _vector_for(text_: str) -> list[float]:
    """Тема текста → ось: так «поиск по смыслу» в тесте детерминирован."""
    if "Zorbax" in text_:
        return _basis(0)
    if "Telgast" in text_:
        return _basis(1)
    return _basis(2)


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> EmbeddingResponse:
        texts: list[str] = kwargs["input"]
        return EmbeddingResponse(
            model="gemini-embedding-001",
            data=[
                {"embedding": _vector_for(item), "index": index, "object": "embedding"}
                for index, item in enumerate(texts)
            ],
            usage=Usage(prompt_tokens=7, completion_tokens=0, total_tokens=7),
        )

    monkeypatch.setattr(litellm, "aembedding", fake)


@pytest.fixture
async def world(clean_database: None, session: AsyncSession) -> None:
    """2015-16 — с отпечатками и Похожими игроками; 1975-76 — без (метрики не считались)."""
    await _insert_season(session, "2015-16", CAST)
    await _insert_season(session, "1975-76", CAST)
    await rebuild_fingerprints(session)
    await session.commit()


def valid_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "summary": "Zorbax scored 28.0 points per game in 2015-16 on 10 games.",
        "archetype": "High-volume scoring guard",
        "strengths": [
            {"claim": "Elite scoring: 28.0 points per game.", "metrics": ["points"]},
            {
                "claim": "Scoring-heavy profile, +2.0 z in points per 36.",
                "metrics": ["points_per36"],
            },
        ],
        "weaknesses": [{"claim": "Low rebounding for a scorer.", "metrics": ["rebounds"]}],
        "stat_profile": "Well above the league in scoring, near it elsewhere.",
        "comparables": [
            {
                "player_name": "Lucan Fenwrick",
                "season": "2015-16",
                "reason": "Same scoring-first profile.",
            }
        ],
        "outlook": "Scoring role is stable; sample is 10 games.",
    }
    return report | overrides


def reply(report: dict[str, Any], *, pieces: int = 3) -> list[Any]:
    return text_reply(json.dumps(report, ensure_ascii=False), pieces=pieces)


async def zorbax_input(
    session_factory: async_sessionmaker[AsyncSession], *, season: str = "2015-16"
) -> ReportInput:
    async with session_factory() as session:
        return await build_report_input(
            session, player_name="Quentin Zorbax", season=season, language="en"
        )


async def produce(
    session_factory: async_sessionmaker[AsyncSession], report_input: ReportInput, user: str = USER
) -> list[ReportSnapshot | ScoutingReport]:
    return [
        item
        async for item in produce_report(
            session_factory, settings=SETTINGS, clerk_user_id=user, report_input=report_input
        )
    ]


async def insert_report(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user: str,
    player_id: int = 101,
    season: str = "2015-16",
    embedding: list[float] | None = None,
) -> UUID:
    async with session_factory() as session:
        report = ScoutingReport(
            clerk_user_id=user,
            player_id=player_id,
            season=season,
            content=valid_report(),
            schema_version=1,
            model=MODEL,
            summary_embedding=embedding or _basis(0),
        )
        session.add(report)
        await session.commit()
        return report.id


# --- генерация и валидация ---------------------------------------------------------------------


@pytest.mark.usefixtures("world", "embedder")
async def test_отчёт_стримится_снапшотами_и_сохраняется_валидным(
    session_factory: async_sessionmaker[AsyncSession], provider: ProviderFactory
) -> None:
    fake = provider(reply(valid_report(), pieces=14))
    report_input = await zorbax_input(session_factory)

    items = await produce(session_factory, report_input)

    snapshots = [item.report for item in items if isinstance(item, ReportSnapshot)]
    saved = items[-1]
    assert isinstance(saved, ScoutingReport)
    assert len(snapshots) > 2, "отчёт пришёл одним куском — это не поток"
    # Разделы появляются по мере написания: в начале нет прогноза, в конце есть всё.
    assert snapshots[0].outlook is None
    assert snapshots[-1].outlook is not None
    assert snapshots[-1].model_dump(exclude_none=True) == valid_report()
    assert saved.content == valid_report()
    assert saved.clerk_user_id == USER and saved.player_id == 101 and saved.season == "2015-16"
    assert saved.model == MODEL

    # Structured output: модель получила JSON Schema отчёта и вход с разрешёнными метриками.
    call = fake.calls[0]
    assert call["response_format"]["json_schema"]["strict"] is True
    assert "allowed_metric_keys" in call["messages"][1]["content"]


@pytest.mark.usefixtures("world", "embedder")
async def test_метрика_вне_входа_даёт_повтор_с_ошибкой_в_промпте(
    session_factory: async_sessionmaker[AsyncSession], provider: ProviderFactory
) -> None:
    bad = valid_report(strengths=[{"claim": "Fast.", "metrics": ["sprint_speed"]}])
    fake = provider(reply(bad), reply(valid_report()))
    report_input = await zorbax_input(session_factory)

    with structlog.testing.capture_logs() as logs:
        items = await produce(session_factory, report_input)

    assert isinstance(items[-1], ScoutingReport)
    assert len(fake.calls) == 2
    retry_prompt = fake.calls[1]["messages"][-1]["content"]
    assert "sprint_speed" in retry_prompt
    # Ответ-нарушитель остался в истории повтора: модель видит, что именно она написала.
    assert fake.calls[1]["messages"][-2]["role"] == "assistant"
    # Повтор начинается с пустого снапшота: клиент сбрасывает разделы первой попытки.
    snapshots = [item.report for item in items if isinstance(item, ReportSnapshot)]
    assert PartialScoutingReport() in snapshots
    # Счётчик: исход каждой попытки в логе и отдельная операция повтора в llm_usage.
    validations = [
        (log["attempt"], log["valid"]) for log in logs if log["event"] == "reports.validation"
    ]
    assert validations == [(1, False), (2, True)]
    async with session_factory() as session:
        operations = list(await session.scalars(select(LlmUsage.operation).order_by(LlmUsage.id)))
    assert operations == ["reports.generate", "reports.generate_retry", "reports.embed"]


@pytest.mark.usefixtures("world", "embedder")
async def test_выдуманный_сопоставимый_игрок_после_повтора_даёт_доменную_ошибку(
    session_factory: async_sessionmaker[AsyncSession], provider: ProviderFactory
) -> None:
    invented = valid_report(
        comparables=[{"player_name": "Michael Jordan", "season": "1995-96", "reason": "GOAT."}]
    )
    fake = provider(reply(invented), reply(invented))
    report_input = await zorbax_input(session_factory)

    with pytest.raises(ReportGenerationFailed) as raised:
        await produce(session_factory, report_input)

    assert len(fake.calls) == 2, "третьей попытки быть не должно"
    assert raised.value.attempts == 2
    assert any("Michael Jordan" in violation for violation in raised.value.violations)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ScoutingReport)) == 0
        operations = list(await session.scalars(select(LlmUsage.operation)))
    assert "reports.embed" not in operations, "невалидный отчёт не должен тратить эмбеддинг"


@pytest.mark.usefixtures("world", "embedder")
async def test_негодный_json_и_обрыв_по_длине_тоже_повод_для_повтора(
    session_factory: async_sessionmaker[AsyncSession], provider: ProviderFactory
) -> None:
    fake = provider(
        text_reply('{"summary": "Zorbax', finish_reason="length"),
        text_reply("не JSON вовсе"),
        text_reply(json.dumps(valid_report())),
    )
    report_input = await zorbax_input(session_factory)

    with pytest.raises(ReportGenerationFailed):
        await produce(session_factory, report_input)

    assert len(fake.calls) == 2


@pytest.mark.usefixtures("world")
async def test_сезон_без_отпечатка_запрещает_сопоставимых_и_метрики_без_значения(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 1975-76: минуты считались, а перехваты и блоки — нет; отпечатка у сезона нет.
    report_input = await zorbax_input(session_factory, season="1975-76")
    assert report_input.similar is None
    assert "steals" not in report_input.metric_keys()
    assert "points_per36" not in report_input.metric_keys()

    content = ScoutingReportContent.model_validate(
        valid_report(
            strengths=[{"claim": "Steals!", "metrics": ["steals"]}],
            comparables=[{"player_name": "Lucan Fenwrick", "season": "1975-76", "reason": "x"}],
        )
    )
    violations = check_grounding(content, report_input)

    assert any("`steals`" in violation for violation in violations)
    assert any("comparables[0]" in violation for violation in violations)
    clean = ScoutingReportContent.model_validate(
        valid_report(strengths=[{"claim": "28.0 points.", "metrics": ["points"]}], comparables=[])
    )
    assert check_grounding(clean, report_input) == []


@pytest.mark.usefixtures("world")
async def test_пустые_разделы_и_пункты_без_метрик_отвергаются(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    report_input = await zorbax_input(session_factory)
    content = ScoutingReportContent.model_validate(
        valid_report(summary=" ", strengths=[], weaknesses=[{"claim": "x", "metrics": []}])
    )

    violations = check_grounding(content, report_input)

    assert any(violation.startswith("summary") for violation in violations)
    assert any(violation.startswith("strengths") for violation in violations)
    assert any(
        "weaknesses[0]" in violation and "без метрики" in violation for violation in violations
    )


def test_частичная_модель_повторяет_поля_схемы() -> None:
    """Снапшот — та же схема, но с необязательными полями: разъезд имён сломал бы поток молча."""
    assert PartialScoutingReport.model_fields.keys() == ScoutingReportContent.model_fields.keys()
    assert PartialReportPoint.model_fields.keys() == ReportPoint.model_fields.keys()
    assert PartialComparable.model_fields.keys() == Comparable.model_fields.keys()


# --- хранение: неизменяемость, приватность -----------------------------------------------------


@pytest.mark.usefixtures("world")
async def test_сохранённый_отчёт_неизменяем_на_уровне_базы(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    report_id = await insert_report(session_factory, user=USER)

    async with session_factory() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text("UPDATE scouting_reports SET season = '2000-01' WHERE id = :id"),
                {"id": report_id},
            )


@pytest.mark.usefixtures("world")
async def test_у_api_нет_правки_отчёта(
    api_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    report_id = await insert_report(session_factory, user=USER)

    for method in ("PUT", "PATCH", "DELETE"):
        response = await api_client.request(method, f"{REPORTS}/{report_id}")
        assert response.status_code == 405, method


# --- HTTP-контракт -----------------------------------------------------------------------------


async def order(client: httpx.AsyncClient, **body: Any) -> list[dict[str, Any]]:
    """Заказывает отчёт и вычерпывает SSE до конца. Возвращает разобранные события."""
    events: list[dict[str, Any]] = []
    async with client.stream("POST", REPORTS, json=body) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    return events


@pytest.fixture
def app_with_model(app: FastAPI) -> FastAPI:
    app.state.settings = app.state.settings.model_copy(update={"llm_model": MODEL})
    return app


@pytest.mark.usefixtures("world", "embedder", "app_with_model")
async def test_post_reports_стримит_снапшоты_и_заканчивает_done(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    provider(reply(valid_report(), pieces=14))

    events = await order(api_client, player_name="Quentin Zorbax", season="2015-16")

    kinds = [event["type"] for event in events]
    assert kinds[-1] == "done" and set(kinds[:-1]) == {"snapshot"}
    assert kinds.count("snapshot") > 2
    assert events[0]["report"].get("outlook") is None
    assert events[-2]["report"]["outlook"] == valid_report()["outlook"]
    report_id = events[-1]["report_id"]

    stored = await api_client.get(f"{REPORTS}/{report_id}")
    assert stored.status_code == 200
    body = stored.json()
    assert body["content"] == valid_report()
    assert body["player"] == {"player_id": 101, "full_name": "Quentin Zorbax"}
    assert body["season"] == "2015-16"
    assert body["schema_version"] == 1
    assert body["model"] == MODEL


@pytest.mark.usefixtures("world", "embedder", "app_with_model")
async def test_post_reports_ошибка_генерации_приходит_событием_error(
    api_client: httpx.AsyncClient, provider: ProviderFactory
) -> None:
    invented = valid_report(strengths=[{"claim": "x", "metrics": ["sprint_speed"]}])
    provider(reply(invented), reply(invented))

    events = await order(api_client, player_name="Quentin Zorbax", season="2015-16")

    assert events[-1]["type"] == "error"
    problem = events[-1]["problem"]
    assert problem["type"].endswith("report-generation-failed")
    assert problem["status"] == 502
    assert problem["attempts"] == 2
    assert "done" not in [event["type"] for event in events]
    assert (await api_client.get(REPORTS)).json()["items"] == []


@pytest.mark.usefixtures("world")
async def test_неизвестный_игрок_и_сезон_без_данных_отвечают_статусом_до_потока(
    api_client: httpx.AsyncClient,
) -> None:
    unknown = await api_client.post(REPORTS, json={"player_name": "Zzyzx Qwertyuiop"})
    no_season = await api_client.post(
        REPORTS, json={"player_name": "Quentin Zorbax", "season": "1990-91"}
    )
    bad_season = await api_client.post(
        REPORTS, json={"player_name": "Quentin Zorbax", "season": "nonsense"}
    )

    assert unknown.status_code == 404
    assert unknown.json()["type"].endswith("player-not-found")
    assert no_season.status_code == 404
    assert no_season.json()["type"].endswith("season-data-missing")
    assert bad_season.status_code == 422


@pytest.mark.usefixtures("world")
async def test_список_только_свои_с_фильтром_по_игроку_и_пагинацией(
    api_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    mine = [await insert_report(session_factory, user=USER, player_id=101) for _ in range(2)]
    mine.append(await insert_report(session_factory, user=USER, player_id=102))
    await insert_report(session_factory, user=OTHER, player_id=101)

    first = (await api_client.get(REPORTS, params={"limit": 2})).json()
    second = (
        await api_client.get(REPORTS, params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()

    listed = [item["report_id"] for item in (*first["items"], *second["items"])]
    assert sorted(listed) == sorted(str(item) for item in mine), "чужого в списке быть не должно"
    assert len(set(listed)) == 3, "страницы пересекаются"
    assert first["next_cursor"] is not None and second["next_cursor"] is None
    assert first["items"][0]["created_at"] >= first["items"][1]["created_at"]

    filtered = (await api_client.get(REPORTS, params={"player_id": 102})).json()
    assert [item["player"]["full_name"] for item in filtered["items"]] == ["Marvin Telgast"]
    assert (await api_client.get(REPORTS, params={"cursor": "мусор"})).status_code == 400


@pytest.mark.usefixtures("world")
async def test_чужой_отчёт_и_несуществующий_неотличимы_404(
    api_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    foreign = await insert_report(session_factory, user=OTHER)

    stranger = await api_client.get(f"{REPORTS}/{foreign}")
    missing = await api_client.get(f"{REPORTS}/{uuid4()}")

    assert stranger.status_code == missing.status_code == 404
    assert stranger.json()["type"] == missing.json()["type"]
    assert "Zorbax" not in stranger.text


async def test_без_токена_reports_закрыты(anonymous_client: httpx.AsyncClient) -> None:
    assert (await anonymous_client.get(REPORTS)).status_code == 401
    assert (await anonymous_client.post(REPORTS, json={"player_name": "x"})).status_code == 401


async def test_владелец_отчёта_не_задаётся_телом_запроса(app_with_model: FastAPI) -> None:
    """Владелец берётся из токена: в теле заказа поля пользователя нет."""
    schema = app_with_model.openapi()["components"]["schemas"]["ReportCreate"]
    assert set(schema["properties"]) == {"player_name", "season", "language"}


# --- Инструменты агента ------------------------------------------------------------------------


def tool_context(
    session_factory: async_sessionmaker[AsyncSession], user: str = USER
) -> ToolContext:
    return ToolContext(
        citations=CitationRegistry(),
        tool_call_id="call_1",
        session_factory=session_factory,
        settings=SETTINGS,
        clerk_user_id=user,
    )


@pytest.mark.usefixtures("world", "embedder")
async def test_search_reports_находит_свой_отчёт_и_не_находит_чужие(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    mine = await insert_report(session_factory, user=USER, embedding=_basis(0))
    await insert_report(session_factory, user=OTHER, player_id=102, embedding=_basis(1))

    found = await execute_tool(
        session,
        name="search_reports",
        arguments={"query": "что я писал про Zorbax"},
        ctx=tool_context(session_factory),
    )
    stranger = await execute_tool(
        session,
        name="search_reports",
        arguments={"query": "что я писал про Telgast"},
        ctx=tool_context(session_factory, OTHER),
    )
    nobody = await execute_tool(
        session,
        name="search_reports",
        arguments={"query": "Zorbax"},
        ctx=tool_context(session_factory, "user_without_reports"),
    )

    assert [item["report_id"] for item in found["reports"]] == [str(mine)]
    assert found["reports"][0]["url"] == f"/reports/{mine}"
    assert found["reports"][0]["similarity"] == pytest.approx(1.0, abs=1e-3)
    # Чужой отчёт (Telgast) находит только его владелец.
    assert [item["player"]["full_name"] for item in stranger["reports"]] == ["Marvin Telgast"]
    assert nobody["reports"] == []

    evidence = evidence_for(name="search_reports", result=found)
    assert evidence.view is not None
    view = evidence.view.root
    assert isinstance(view, ReportView)
    assert view.items[0].url == f"/reports/{mine}"
    assert view.items[0].player == "Quentin Zorbax"
    assert evidence_for(name="search_reports", result=nobody).view is None


@pytest.mark.usefixtures("world", "embedder")
async def test_search_reports_сужается_по_игроку_и_объясняет_неопознанное_имя(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await insert_report(session_factory, user=USER, player_id=101, embedding=_basis(0))
    telgast = await insert_report(session_factory, user=USER, player_id=102, embedding=_basis(1))

    narrowed = await execute_tool(
        session,
        name="search_reports",
        arguments={"query": "Zorbax", "player_name": "Marvin Telgast"},
        ctx=tool_context(session_factory),
    )
    unknown = await execute_tool(
        session,
        name="search_reports",
        arguments={"query": "x", "player_name": "Zzyzx Qwertyuiop"},
        ctx=tool_context(session_factory),
    )

    assert [item["report_id"] for item in narrowed["reports"]] == [str(telgast)]
    assert unknown["error"]["kind"] == "player-not-found"


@pytest.mark.usefixtures("world", "embedder")
async def test_create_scouting_report_в_цикле_агента_отдаёт_карточку_и_сохраняет_отчёт(
    session_factory: async_sessionmaker[AsyncSession], provider: ProviderFactory
) -> None:
    fake = provider(
        tool_call_reply(
            "create_scouting_report",
            json.dumps({"player_name": "Quentin Zorbax", "season": "2015-16", "language": "ru"}),
        ),
        reply(valid_report()),
        text_reply("Отчёт готов: высокообъёмный скорер."),
    )

    events = [
        event
        async for event in stream_answer(
            session_factory,
            history=[AgentTurn(role="user", content="Сделай отчёт по Zorbax за 2015-16")],
            settings=SETTINGS,
            clerk_user_id=USER,
        )
    ]

    resulted = next(event for event in events if isinstance(event, ToolResulted))
    assert resulted.failure is None
    assert resulted.view is not None
    view = resulted.view.root
    assert isinstance(view, ReportView)
    assert view.items[0].player == "Quentin Zorbax" and view.items[0].season == "2015-16"
    assert isinstance(events[-1], AgentAnswer)

    async with session_factory() as session:
        saved = (await session.scalars(select(ScoutingReport))).all()
    assert len(saved) == 1
    assert saved[0].clerk_user_id == USER, "владелец — пользователь хода, не аргумент модели"
    assert view.items[0].url == f"/reports/{saved[0].id}"
    # Модель получила результат Инструмента с содержимым, а язык ушёл во вход генерации.
    assert '"language": "ru"' in fake.calls[1]["messages"][1]["content"]
    tool_message = next(m for m in fake.calls[2]["messages"] if m.get("role") == "tool")
    assert saved[0].content["archetype"] in tool_message["content"]


@pytest.mark.usefixtures("world", "embedder")
async def test_create_scouting_report_отказ_генерации_доезжает_до_модели_без_записи(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    provider: ProviderFactory,
) -> None:
    broken = valid_report(
        comparables=[{"player_name": "Nobody", "season": "2015-16", "reason": "x"}]
    )
    provider(reply(broken), reply(broken))

    result = await execute_tool(
        session,
        name="create_scouting_report",
        arguments={"player_name": "Quentin Zorbax", "season": "2015-16"},
        ctx=tool_context(session_factory),
    )

    assert result["error"]["kind"] == "report-generation-failed"
    assert evidence_for(name="create_scouting_report", result=result).failure is not None
    async with session_factory() as other:
        assert await other.scalar(select(func.count()).select_from(ScoutingReport)) == 0


async def test_создание_отчёта_отвергает_кривые_аргументы(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    result = await execute_tool(
        session,
        name="create_scouting_report",
        arguments={"player_name": "x", "season": "2015"},
        ctx=tool_context(session_factory),
    )

    assert result["error"]["kind"] == "invalid-arguments"
