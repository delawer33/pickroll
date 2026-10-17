"""Eval ответов: детерминированные проверки, раннер, судья и отчёт — на фейковых ответах.

Живых LLM-вызовов здесь нет: агент и судья подменяются функциями (`AnswerFn`, `JudgeFn`), а
транспорт судьи проверяется на границе litellm, как и транспорт агента. Сам Золотой набор в
обычном CI не гоняется — он стоит денег (см. README, «Eval ответов»).
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.loop import AgentAnswer, ToolInvocation, TurnCitation
from court_vision.agent.models import LlmUsage
from court_vision.answer_eval import report, tracking
from court_vision.answer_eval.checks import citation_source, is_refusal, run_checks
from court_vision.answer_eval.golden import (
    DEFAULT_ANSWERS_PATH,
    DEFAULT_THRESHOLDS_PATH,
    GoldenAnswer,
    load_answers,
)
from court_vision.answer_eval.golden import select as select_cases
from court_vision.answer_eval.judge import (
    JudgeError,
    Verdict,
    build_messages,
    compress_tool_results,
    make_judge,
    parse_verdict,
)
from court_vision.answer_eval.runner import CaseResult, Usage, evaluate_answers
from court_vision.config import Settings
from court_vision.corpus.sources import RULEBOOK_TITLE
from tests.conftest import MODEL, ProviderFactory, text_reply


def case(**overrides: Any) -> GoldenAnswer:
    fields: dict[str, Any] = {
        "id": "q1",
        "category": "stats",
        "question": "Сколько набирал Леброн в 2012-13?",
        "reference": ["26.8 очка за игру"],
        "expect_tools": ["get_player_stats"],
    }
    return GoldenAnswer.model_validate({**fields, **overrides})


def citation(index: int = 1, *, title: str = "LeBron James") -> TurnCitation:
    return TurnCitation(
        index=index,
        tool_call_id="call_1",
        chunk_id=index,
        document_title=title,
        locator="Career",
        snippet="Он выиграл четыре титула.",
        source_url=None,
    )


def invocation(name: str, result: dict[str, Any] | None = None) -> ToolInvocation:
    return ToolInvocation(
        name=name, arguments={"player_name": "LeBron James"}, result=result or {}, duration_ms=5
    )


def answer(
    text: str = "26.8 очка за игру.",
    *,
    tools: tuple[str, ...] = ("get_player_stats",),
    citations: tuple[TurnCitation, ...] = (),
) -> AgentAnswer:
    return AgentAnswer(
        text=text,
        tool_invocations=tuple(invocation(name) for name in tools),
        citations=citations,
    )


PASS = Verdict(correctness=2, grounding=2, verdict="pass", reasons="Все факты на месте.")
FAIL = Verdict(correctness=0, grounding=1, verdict="fail", reasons="Число неверно: 25.0.")


# --- детерминированные проверки ---


def test_чистый_ответ_проходит_все_проверки() -> None:
    assert run_checks(case(), answer()) == []


def test_не_вызванный_ожидаемый_инструмент_проваливает_проверку() -> None:
    failures = run_checks(case(), answer(tools=()))
    assert failures == ["expect_tools: не вызваны ['get_player_stats']"]


def test_лишние_инструменты_не_штрафуются() -> None:
    assert run_checks(case(), answer(tools=("get_player_stats", "search_corpus"))) == []


def test_маркер_без_цитаты_проваливает_проверку() -> None:
    failures = run_checks(case(), answer("Он очень хорош [3].", citations=(citation(1),)))
    assert failures == ["markers: маркеры без Цитаты [3]"]


def test_валидный_маркер_проходит() -> None:
    text = "Он выиграл четыре титула [1]."
    assert run_checks(case(), answer(text, citations=(citation(1),))) == []


def test_ответ_без_цитат_нужного_источника_проваливает_проверку() -> None:
    golden = case(expect_tools=["search_corpus"], expect_sources=["rulebook"])
    got = answer("Пробежка — это нарушение [1].", tools=("search_corpus",), citations=(citation(),))
    assert run_checks(golden, got) == ["expect_sources: нет Цитат из ['rulebook']"]


def test_цитата_rulebook_определяется_по_названию_документа() -> None:
    assert citation_source(citation(title=RULEBOOK_TITLE)) == "rulebook"
    assert citation_source(citation(title="Stephen Curry")) == "wikipedia"


def test_нужный_источник_засчитывается() -> None:
    golden = case(expect_tools=["search_corpus"], expect_sources=["rulebook"])
    cited = citation(title=RULEBOOK_TITLE)
    got = answer("Шагов не больше двух [1].", tools=("search_corpus",), citations=(cited,))
    assert run_checks(golden, got) == []


@pytest.mark.parametrize(
    "text",
    [
        "К сожалению, таких данных у меня нет.",
        "Данных нет.",
        "У меня нет информации о семье игрока.",
        "Не могу ответить на этот вопрос.",
        "I don't have any data on that.",
        "I'm unable to answer.",
    ],
)
def test_шаблон_отказа_проваливает_проверку_если_отказ_недопустим(text: str) -> None:
    assert is_refusal(text)
    assert run_checks(case(), answer(text)) == ["must_not_refuse: ответ — отказ"]


def test_отказ_допустим_если_вопрос_его_ожидает() -> None:
    golden = case(must_not_refuse=False, expect_tools=[])
    assert run_checks(golden, answer("Такого игрока в данных нет.", tools=())) == []


def test_обычный_ответ_не_считается_отказом() -> None:
    assert not is_refusal("Леброн набирал 26.8 очка, 8.0 подбора и 7.3 передачи.")


def test_проверки_накапливают_все_причины() -> None:
    golden = case(expect_tools=["get_player_stats", "search_corpus"], expect_sources=["rulebook"])
    failures = run_checks(golden, answer("Данных нет [2].", tools=()))
    assert len(failures) == 4


# --- Золотой набор ---


def test_золотой_набор_ответов_валиден_и_покрывает_категории() -> None:
    cases = load_answers(DEFAULT_ANSWERS_PATH)
    assert len(cases) >= 20
    assert {item.category for item in cases} >= {
        "stats",
        "rules",
        "biography",
        "old_era",
        "ambiguity",
    }


def test_повторяющиеся_id_отвергаются(tmp_path: Path) -> None:
    path = tmp_path / "answers.yaml"
    entry = "- {id: a, category: stats, question: q, reference: [r]}\n"
    path.write_text(entry * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="повторяющиеся id"):
        load_answers(path)


def test_expect_sources_без_search_corpus_невыполним() -> None:
    with pytest.raises(ValueError, match="требует"):
        case(expect_sources=["rulebook"], expect_tools=["get_player_stats"])


def test_фильтры_категории_и_id() -> None:
    cases = [case(id="a"), case(id="b", category="rules"), case(id="c", category="rules")]
    assert [c.id for c in select_cases(cases, categories=["rules"], ids=None)] == ["b", "c"]
    assert [c.id for c in select_cases(cases, categories=None, ids=["a", "c"])] == ["a", "c"]
    assert [c.id for c in select_cases(cases, categories=["rules"], ids=["a", "c"])] == ["c"]


def test_неизвестный_фильтр_это_ошибка_а_не_пустой_прогон() -> None:
    with pytest.raises(ValueError, match="нет вопроса"):
        select_cases([case()], categories=None, ids=["nope"])
    with pytest.raises(ValueError, match="нет вопросов категории"):
        select_cases([case()], categories=["rules"], ids=None)


# --- раннер ---


async def fake_judge_pass(_: GoldenAnswer, __: AgentAnswer) -> Verdict:
    return PASS


async def test_формальный_провал_не_доходит_до_судьи() -> None:
    judged: list[str] = []

    async def judge(golden: GoldenAnswer, _: AgentAnswer) -> Verdict:
        judged.append(golden.id)
        return PASS

    async def agent(_: GoldenAnswer) -> AgentAnswer:
        return answer(tools=())

    [result] = await evaluate_answers([case()], answer_fn=agent, judge_fn=judge)

    assert judged == []
    assert not result.passed
    assert result.verdict is None
    assert result.check_failures


async def test_ответ_прошедший_проверки_судится_и_вердикт_решает() -> None:
    async def agent(_: GoldenAnswer) -> AgentAnswer:
        return answer()

    async def judge_fail(_: GoldenAnswer, __: AgentAnswer) -> Verdict:
        return FAIL

    [ok] = await evaluate_answers([case()], answer_fn=agent, judge_fn=fake_judge_pass)
    [bad] = await evaluate_answers([case()], answer_fn=agent, judge_fn=judge_fail)

    assert ok.passed
    assert not bad.passed
    assert bad.verdict == FAIL


async def test_падение_агента_на_одном_вопросе_не_теряет_остальные() -> None:
    async def agent(golden: GoldenAnswer) -> AgentAnswer:
        if golden.id == "boom":
            raise RuntimeError("шлюз лёг")
        return answer()

    results = await evaluate_answers(
        [case(id="ok"), case(id="boom"), case(id="ok2")],
        answer_fn=agent,
        judge_fn=fake_judge_pass,
    )

    assert [r.case.id for r in results] == ["ok", "boom", "ok2"]
    assert [r.passed for r in results] == [True, False, True]
    assert results[1].agent_error == "RuntimeError: шлюз лёг"


async def test_падение_судьи_записывается_в_результат() -> None:
    async def agent(_: GoldenAnswer) -> AgentAnswer:
        return answer()

    async def judge(_: GoldenAnswer, __: AgentAnswer) -> Verdict:
        raise JudgeError("не по схеме")

    [result] = await evaluate_answers([case()], answer_fn=agent, judge_fn=judge)

    assert not result.passed
    assert result.judge_error == "JudgeError: не по схеме"


async def test_конкурентность_ограничена() -> None:
    in_flight = 0
    peak = 0

    async def agent(_: GoldenAnswer) -> AgentAnswer:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return answer()

    cases = [case(id=f"q{n}") for n in range(8)]
    await evaluate_answers(cases, answer_fn=agent, judge_fn=fake_judge_pass, concurrency=3)

    assert peak == 3


# --- судья ---


def test_вердикт_разбирается_из_json_и_из_забора() -> None:
    body = '{"correctness": 2, "grounding": 1, "verdict": "pass", "reasons": "ок"}'
    assert parse_verdict(body).grounding == 1
    assert parse_verdict(f"```json\n{body}\n```").verdict == "pass"


@pytest.mark.parametrize(
    "body",
    [
        "не json",
        '{"correctness": 3, "grounding": 1, "verdict": "pass", "reasons": "x"}',
        '{"correctness": 2, "grounding": 1, "verdict": "maybe", "reasons": "x"}',
        '{"correctness": 2, "grounding": 1, "verdict": "pass"}',
    ],
)
def test_ответ_судьи_не_по_схеме_это_ошибка_судьи(body: str) -> None:
    with pytest.raises(JudgeError):
        parse_verdict(body)


def test_судья_видит_вопрос_эталон_ответ_и_цитаты() -> None:
    cited = citation(1)
    got = AgentAnswer(
        text="Четыре титула [1].",
        tool_invocations=(invocation("search_corpus", {"results": []}),),
        citations=(cited,),
    )

    messages = build_messages(case(reference=["4 титула"]), got)
    prompt = messages[1]["content"]

    assert messages[0]["role"] == "system"
    assert "Сколько набирал Леброн" in prompt
    assert "- 4 титула" in prompt
    assert "Четыре титула [1]." in prompt
    assert cited.snippet in prompt


def test_результат_search_corpus_сжимается_до_заголовков() -> None:
    long_text = "очень длинный текст чанка " * 200
    got = AgentAnswer(
        text="x",
        tool_invocations=(
            invocation(
                "search_corpus",
                {"results": [{"n": 1, "title": "A", "locator": "L", "text": long_text}]},
            ),
        ),
    )

    compressed = compress_tool_results(got)

    assert "очень длинный текст" not in compressed
    assert '"title": "A"' in compressed


def test_огромный_результат_инструмента_обрезается() -> None:
    got = AgentAnswer(
        text="x",
        tool_invocations=(invocation("get_player_stats", {"seasons": ["x" * 50] * 500}),),
    )
    assert "обрезано" in compress_tool_results(got)


async def test_судья_идёт_через_транспорт_со_structured_output_и_учётом(
    provider: ProviderFactory,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    body = json.dumps({"correctness": 2, "grounding": 2, "verdict": "pass", "reasons": "ок"})
    fake = provider(text_reply(body))
    settings = Settings(llm_model=MODEL, eval_judge_model="anthropic/claude-sonnet-4-5")
    # llm_usage общая для тестов и коммитится транспортом: считаем прирост, а не всё содержимое.
    judge_rows = select(LlmUsage).where(LlmUsage.operation == "eval.judge")
    before = len((await session.execute(judge_rows)).scalars().all())

    verdict = await make_judge(session_factory, settings)(case(), answer())

    assert verdict.verdict == "pass"
    [call] = fake.calls
    assert call["model"] == "anthropic/claude-sonnet-4-5"
    assert call["response_format"]["type"] == "json_schema"
    assert call["tools"] is None
    after = len((await session.execute(judge_rows)).scalars().all())
    assert after == before + 1


# --- отчёт и пороги ---


def result_of(
    golden: GoldenAnswer, verdict: Verdict | None, *, failures: tuple[str, ...] = ()
) -> CaseResult:
    return CaseResult(
        case=golden,
        answer=answer(),
        agent_error=None,
        check_failures=failures,
        verdict=verdict,
        judge_error=None,
        duration_s=1.0,
    )


def sample_results() -> list[CaseResult]:
    return [
        result_of(case(id="s1"), PASS),
        result_of(case(id="s2"), FAIL),
        result_of(case(id="r1", category="rules"), None, failures=("expect_tools: не вызваны",)),
        result_of(case(id="r2", category="rules"), PASS),
    ]


def test_сводка_считает_pass_rate_и_средние_только_по_оценённым() -> None:
    summary = report.summarize(sample_results())

    assert summary.overall.total == 4
    assert summary.overall.passed == 2
    assert summary.overall.pass_rate == 0.5
    # r1 не дошёл до судьи: его нет в знаменателе средних баллов.
    assert summary.overall.judged == 3
    assert summary.overall.avg_correctness == pytest.approx(4 / 3)
    assert summary.by_category["rules"].pass_rate == 0.5


def test_ниже_порога_это_нарушение() -> None:
    summary = report.summarize(sample_results())
    thresholds = report.Thresholds(pass_rate=0.75, by_category={"rules": 0.5, "stats": 0.9})

    violations = report.compare(summary, thresholds, full_run=True)

    assert len(violations) == 2
    assert any("pass rate 0.500 < порога 0.75" in v for v in violations)
    assert any("stats" in v for v in violations)


def test_не_хуже_порога_нарушений_нет() -> None:
    summary = report.summarize(sample_results())
    thresholds = report.Thresholds(pass_rate=0.5, avg_correctness=1.0, by_category={"rules": 0.5})
    assert report.compare(summary, thresholds, full_run=True) == []


def test_на_фильтрованном_прогоне_общие_пороги_не_применяются() -> None:
    results = [r for r in sample_results() if r.case.category == "rules"]
    summary = report.summarize(results)
    thresholds = report.Thresholds(pass_rate=0.9, by_category={"stats": 0.9, "rules": 0.5})

    assert report.compare(summary, thresholds, full_run=False) == []


def test_коммитимый_файл_порогов_читается() -> None:
    thresholds = report.load_thresholds(DEFAULT_THRESHOLDS_PATH)
    assert thresholds.pass_rate is not None


def test_отчёты_json_и_markdown_содержат_провалы_и_стоимость(tmp_path: Path) -> None:
    results = sample_results()
    summary = report.summarize(results)
    usage = Usage(10, 5000, Decimal("0.0123"), 3, 900, None)
    meta = {"started_at": "2026-10-17T00:00:00+00:00", "agent_model": "a", "judge_model": "j"}

    path = report.write_reports(
        tmp_path, results, summary, usage, meta=meta, violations=["pass rate 0.5 < 0.9"]
    )

    markdown = path.read_text(encoding="utf-8")
    assert "Pass rate: 2/4 = 50.0%" in markdown
    assert "### `s2` (stats)" in markdown
    assert "Число неверно: 25.0." in markdown
    assert "expect_tools: не вызваны" in markdown
    assert "ПРОВАЛ: pass rate 0.5 < 0.9" in markdown
    assert "$0.0123" in markdown
    payload = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["summary"]["overall"]["pass_rate"] == 0.5
    assert payload["usage"]["agent"]["cost_usd"] == "0.0123"
    assert [c["id"] for c in payload["cases"]] == ["s1", "s2", "r1", "r2"]


# --- Langfuse ---


def test_без_ключей_langfuse_прогон_молча_пропускает_dataset_run() -> None:
    settings = Settings(langfuse_public_key="", langfuse_secret_key="")
    assert not tracking.langfuse_enabled(settings)
    assert tracking.publish_dataset_run(settings, sample_results(), run_name="x") is False


def test_сбой_langfuse_не_роняет_прогон(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("langfuse лёг")

    monkeypatch.setattr(tracking, "_publish", boom)
    settings = Settings(langfuse_public_key="pk", langfuse_secret_key="sk")

    assert tracking.publish_dataset_run(settings, sample_results(), run_name="x") is False
