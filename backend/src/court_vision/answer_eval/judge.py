"""LLM-судья: оценивает готовый ответ против эталона по фиксированной рубрике.

Судья видит ровно то, что положено по CONTEXT.md: вопрос, эталон, ответ, тексты
процитированных Чанков и сжатые результаты Инструментов. Не видит ни вызовов Инструментов
как таковых, ни результатов детерминированных проверок — то, что решается без модели, ему
не отдаётся (и ответы, провалившие эти проверки, до него не доходят).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.loop import AgentAnswer
from court_vision.agent.transport import LlmReply, stream
from court_vision.answer_eval.golden import GoldenAnswer
from court_vision.config import Settings

OPERATION: Final = "eval.judge"
_RUBRIC: Final = (Path(__file__).parent / "prompts" / "judge.md").read_text(encoding="utf-8")

# Потолок на один результат Инструмента в промпте судьи. Статистика игрока за карьеру — это
# десятки сезонов; судье нужны числа, о которых спрашивали, а не весь JSON, и платить за
# него на каждом вопросе незачем.
_TOOL_RESULT_LIMIT: Final = 4000


class Verdict(BaseModel):
    """Вердикт судьи. Он же — схема structured output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Literal, а не ge/le: шлюзы со strict-режимом структурированного вывода принимают enum
    # и отвергают minimum/maximum.
    correctness: Literal[0, 1, 2]
    grounding: Literal[0, 1, 2]
    verdict: Literal["pass", "fail"]
    reasons: str


class JudgeError(Exception):
    """Судья ответил не по схеме. Для раннера это провал вопроса с причиной, а не падение
    прогона: один кривой ответ не должен стоить остальных уже оплаченных."""


JudgeFn = Callable[[GoldenAnswer, AgentAnswer], Awaitable[Verdict]]


def response_format() -> dict[str, Any]:
    schema = Verdict.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "json_schema",
        "json_schema": {"name": "verdict", "strict": True, "schema": schema},
    }


def compress_tool_results(answer: AgentAnswer) -> str:
    """Сжатые результаты Инструментов для промпта судьи.

    `search_corpus` сводится к списку «что найдено» — тексты нужных Чанков судья получает
    отдельно, как процитированные; остальное идёт JSON'ом с потолком длины.
    """
    parts: list[str] = []
    for invocation in answer.tool_invocations:
        result = invocation.result
        if invocation.name == "search_corpus" and "results" in result:
            body = json.dumps(
                [
                    {"n": item.get("n"), "title": item.get("title"), "locator": item.get("locator")}
                    for item in result["results"]
                ],
                ensure_ascii=False,
            )
        else:
            body = json.dumps(result, ensure_ascii=False)
        if len(body) > _TOOL_RESULT_LIMIT:
            body = body[:_TOOL_RESULT_LIMIT] + "…(обрезано)"
        arguments = json.dumps(invocation.arguments, ensure_ascii=False)
        parts.append(f"### {invocation.name}({arguments})\n{body}")
    return "\n\n".join(parts) or "(Инструменты не вызывались)"


def build_messages(case: GoldenAnswer, answer: AgentAnswer) -> list[dict[str, Any]]:
    reference = "\n".join(f"- {fact}" for fact in case.reference)
    cited = (
        "\n\n".join(
            f"[{citation.index}] {citation.document_title}, {citation.locator}\n{citation.snippet}"
            for citation in answer.citations
        )
        or "(цитат нет)"
    )
    user = (
        f"## Вопрос\n{case.question}\n\n"
        f"## Эталонные факты\n{reference}\n\n"
        f"## Ответ ассистента\n{answer.text}\n\n"
        f"## Процитированные фрагменты корпуса\n{cited}\n\n"
        f"## Результаты Инструментов (сжато)\n{compress_tool_results(answer)}"
    )
    return [{"role": "system", "content": _RUBRIC}, {"role": "user", "content": user}]


_FENCE: Final = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_verdict(text: str) -> Verdict:
    """Вердикт из текста ответа судьи. Забор ```json терпим: часть шлюзов оборачивает в него
    structured output, и валить из-за этого прогон незачем."""
    try:
        return Verdict.model_validate_json(_FENCE.sub("", text.strip()))
    except ValidationError as error:
        raise JudgeError(f"судья ответил не по схеме: {text[:200]!r}") from error


def make_judge(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> JudgeFn:
    """Судья на настоящем транспорте. Подменяемость — через `JudgeFn`: тесты передают раннеру
    фейковую функцию, а сам транспорт проверяются отдельно на границе litellm."""

    async def judge(case: GoldenAnswer, answer: AgentAnswer) -> Verdict:
        reply: LlmReply | None = None
        async for part in stream(
            session_factory,
            model=settings.eval_judge_model,
            messages=build_messages(case, answer),
            tools=[],
            timeout=settings.llm_timeout_seconds,
            operation=OPERATION,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            response_format=response_format(),
        ):
            if isinstance(part, LlmReply):
                reply = part
        if reply is None:
            raise JudgeError("судья не вернул ответа")
        return parse_verdict(reply.text)

    return judge
