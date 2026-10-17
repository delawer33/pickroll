"""Раннер eval ответов: прогон прод-агента по Золотому набору + проверки + судья.

Агент гоняется тем же `stream_answer`, что и в проде, но без Диалога: ничего не пишется в
`conversations`/`messages`, история — одна реплика-вопрос. Учёт стоимости остаётся прежним
(строки `llm_usage`), поэтому стоимость прогона читается из той же таблицы.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from time import perf_counter

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent import loop
from court_vision.agent.loop import AgentAnswer, Turn, stream_answer
from court_vision.agent.models import LlmUsage
from court_vision.answer_eval import judge as judge_module
from court_vision.answer_eval.checks import run_checks
from court_vision.answer_eval.golden import GoldenAnswer
from court_vision.answer_eval.judge import JudgeFn, Verdict
from court_vision.config import Settings

log = structlog.get_logger(__name__)

AnswerFn = Callable[[GoldenAnswer], Awaitable[AgentAnswer]]

# От чьего имени прогон создаёт и ищет Скаутинг-отчёты. Отдельный «пользователь»: отчёты eval
# не смешиваются с отчётами живых людей и не находятся их поиском.
EVAL_USER_ID = "eval-runner"

# Операции, под которыми агент пишет в llm_usage: шаги цикла, эмбеддинги запросов Инструментов
# и генерация отчёта, которую запускает Инструмент `create_scouting_report`.
_AGENT_OPERATIONS = (
    loop.OPERATION,
    "agent.search_corpus",
    "agent.search_reports",
    "reports.generate",
    "reports.generate_retry",
    "reports.embed",
)


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Итог одного вопроса: что ответил агент, что сказали проверки и судья."""

    case: GoldenAnswer
    answer: AgentAnswer | None
    # Агент упал (ошибка провайдера, потолок шагов): ответа нет, судья не зовётся.
    agent_error: str | None
    check_failures: tuple[str, ...]
    verdict: Verdict | None
    judge_error: str | None
    duration_s: float

    @property
    def passed(self) -> bool:
        return (
            self.answer is not None
            and not self.check_failures
            and self.verdict is not None
            and self.verdict.verdict == "pass"
        )


def agent_answer_fn(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, *, run_id: str
) -> AnswerFn:
    """Прод-агент как `AnswerFn`: вычерпывает `stream_answer` до итога."""

    async def answer(case: GoldenAnswer) -> AgentAnswer:
        history = [Turn(role="user", content=case.question)]
        async with aclosing(
            stream_answer(
                session_factory,
                history=history,
                settings=settings,
                clerk_user_id=EVAL_USER_ID,
                # Прогон eval — одна сессия Langfuse: трейсы вопросов собираются вместе.
                trace_metadata={"session_id": f"eval-{run_id}"},
            )
        ) as events:
            async for event in events:
                if isinstance(event, AgentAnswer):
                    return event
        raise RuntimeError("цикл закончился без ответа и без ошибки")

    return answer


async def evaluate_answers(
    cases: Sequence[GoldenAnswer],
    *,
    answer_fn: AnswerFn,
    judge_fn: JudgeFn,
    concurrency: int = 4,
) -> list[CaseResult]:
    """Прогоняет вопросы с ограниченной конкурентностью; порядок результата — порядок `cases`.

    Падение одного вопроса (агента или судьи) записывается в его результат и на остальные не
    влияет: прогон стоит денег, и терять из-за одного сбоя оплаченные ответы нельзя.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: GoldenAnswer) -> CaseResult:
        async with semaphore:
            return await _run_case(case, answer_fn=answer_fn, judge_fn=judge_fn)

    return list(await asyncio.gather(*(one(case) for case in cases)))


async def _run_case(case: GoldenAnswer, *, answer_fn: AnswerFn, judge_fn: JudgeFn) -> CaseResult:
    started = perf_counter()

    def finish(
        answer: AgentAnswer | None = None,
        *,
        agent_error: str | None = None,
        check_failures: tuple[str, ...] = (),
        verdict: Verdict | None = None,
        judge_error: str | None = None,
    ) -> CaseResult:
        return CaseResult(
            case=case,
            answer=answer,
            agent_error=agent_error,
            check_failures=check_failures,
            verdict=verdict,
            judge_error=judge_error,
            duration_s=perf_counter() - started,
        )

    try:
        answer = await answer_fn(case)
    except Exception as error:
        # Широкий except намеренно: тип сбоя (доменная ошибка, таймаут, баг Инструмента) для
        # отчёта вторичен, важно, что вопрос не получил ответа.
        log.warning("answer_eval.agent_failed", id=case.id, cause=type(error).__name__)
        return finish(agent_error=f"{type(error).__name__}: {error}")

    failures = tuple(run_checks(case, answer))
    if failures:
        # Дешёвое судье не отдаётся: ответ, провалившийся формально, уже провал.
        return finish(answer, check_failures=failures)

    try:
        verdict = await judge_fn(case, answer)
    except Exception as error:
        log.warning("answer_eval.judge_failed", id=case.id, cause=type(error).__name__)
        return finish(answer, judge_error=f"{type(error).__name__}: {error}")
    return finish(answer, verdict=verdict)


@dataclass(frozen=True, slots=True)
class Usage:
    """Расход прогона по таблице `llm_usage`: агент и судья отдельно.

    `cost_usd` None — у вызовов нет цены (за шлюзом прайслиста у litellm нет, см.
    docs/deferred.md); тогда осмысленны токены.
    """

    agent_calls: int
    agent_tokens: int
    agent_cost_usd: Decimal | None
    judge_calls: int
    judge_tokens: int
    judge_cost_usd: Decimal | None


async def db_now(session_factory: async_sessionmaker[AsyncSession]) -> datetime:
    """Время БД: границу прогона берём по часам той же машины, что пишет created_at."""
    async with session_factory() as session:
        return (await session.execute(select(func.now()))).scalar_one()


async def collect_usage(
    session_factory: async_sessionmaker[AsyncSession], *, since: datetime
) -> Usage:
    """Суммы по `llm_usage` с момента `since`.

    Таблица общая, а не прогонная: строки чужих процессов в окне прогона попадут в сумму.
    Для локального прогона на своей базе это допущение приемлемо; точнее не получится без
    привязки строки учёта к прогону (в `llm_usage` такого поля нет).
    """

    async def totals(operations: Sequence[str]) -> tuple[int, int, Decimal | None]:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(
                        func.count(),
                        func.coalesce(func.sum(LlmUsage.total_tokens), 0),
                        func.sum(LlmUsage.cost_usd),
                    ).where(LlmUsage.created_at >= since, LlmUsage.operation.in_(operations))
                )
            ).one()
        return int(row[0]), int(row[1]), row[2]

    agent = await totals(_AGENT_OPERATIONS)
    judge = await totals((judge_module.OPERATION,))
    return Usage(agent[0], agent[1], agent[2], judge[0], judge[1], judge[2])
