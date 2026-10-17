"""Отчёт прогона: сводка, JSON и markdown, сравнение с коммитимыми порогами."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from court_vision.answer_eval.runner import CaseResult, Usage


@dataclass(frozen=True, slots=True)
class Stats:
    """Агрегаты по набору результатов. Средние баллы — только по тем, до кого дошёл судья:
    ответ, провалившийся формально, баллов не получал, и нули вместо них топили бы среднее."""

    total: int
    passed: int
    judged: int
    avg_correctness: float | None
    avg_grounding: float | None

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class Summary:
    overall: Stats
    by_category: dict[str, Stats]


def _stats(results: list[CaseResult]) -> Stats:
    verdicts = [result.verdict for result in results if result.verdict is not None]
    return Stats(
        total=len(results),
        passed=sum(1 for result in results if result.passed),
        judged=len(verdicts),
        avg_correctness=sum(v.correctness for v in verdicts) / len(verdicts) if verdicts else None,
        avg_grounding=sum(v.grounding for v in verdicts) / len(verdicts) if verdicts else None,
    )


def summarize(results: list[CaseResult]) -> Summary:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.case.category].append(result)
    return Summary(
        overall=_stats(results),
        by_category={category: _stats(items) for category, items in sorted(grouped.items())},
    )


class Thresholds(BaseModel):
    """Пороги из коммитимого файла: бейзлайн первого прогона с запасом на шум судьи.

    Любое поле можно опустить — тогда оно не проверяется. Категории проверяются те, что
    попали в прогон, поэтому `--category stats` не падает из-за порога по `rules`.
    """

    model_config = ConfigDict(extra="forbid")

    pass_rate: float | None = None
    avg_correctness: float | None = None
    avg_grounding: float | None = None
    by_category: dict[str, float] = Field(default_factory=dict)


def load_thresholds(path: Path) -> Thresholds:
    return Thresholds.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def compare(summary: Summary, thresholds: Thresholds, *, full_run: bool) -> list[str]:
    """Нарушения порогов; пусто — прогон не хуже бейзлайна.

    Общие пороги проверяются только на полном прогоне: pass rate одной категории с общим
    порогом не сравнить, и `--id` с одним вопросом падал бы на любом провале.
    """
    violations: list[str] = []
    if full_run:
        overall = summary.overall
        if thresholds.pass_rate is not None and overall.pass_rate < thresholds.pass_rate:
            violations.append(f"pass rate {overall.pass_rate:.3f} < порога {thresholds.pass_rate}")
        for name, value, limit in (
            ("avg correctness", overall.avg_correctness, thresholds.avg_correctness),
            ("avg grounding", overall.avg_grounding, thresholds.avg_grounding),
        ):
            if limit is not None and (value is None or value < limit):
                shown = "нет оценок" if value is None else f"{value:.3f}"
                violations.append(f"{name} {shown} < порога {limit}")
    for category, limit in sorted(thresholds.by_category.items()):
        stats = summary.by_category.get(category)
        if stats is not None and stats.pass_rate < limit:
            violations.append(f"pass rate {category} {stats.pass_rate:.3f} < порога {limit}")
    return violations


def _failure_reasons(result: CaseResult) -> list[str]:
    if result.agent_error:
        return [f"агент: {result.agent_error}"]
    reasons = list(result.check_failures)
    if result.judge_error:
        reasons.append(f"судья: {result.judge_error}")
    if result.verdict is not None and result.verdict.verdict == "fail":
        reasons.append(
            f"судья (correctness {result.verdict.correctness}, "
            f"grounding {result.verdict.grounding}): {result.verdict.reasons}"
        )
    return reasons


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def to_json(
    results: list[CaseResult],
    summary: Summary,
    usage: Usage,
    *,
    meta: dict[str, Any],
    violations: list[str],
) -> dict[str, Any]:
    def stats(item: Stats) -> dict[str, Any]:
        return {
            "total": item.total,
            "passed": item.passed,
            "pass_rate": item.pass_rate,
            "judged": item.judged,
            "avg_correctness": item.avg_correctness,
            "avg_grounding": item.avg_grounding,
        }

    return {
        "meta": meta,
        "summary": {
            "overall": stats(summary.overall),
            "by_category": {name: stats(item) for name, item in summary.by_category.items()},
        },
        "violations": violations,
        "usage": {
            "agent": {
                "calls": usage.agent_calls,
                "tokens": usage.agent_tokens,
                "cost_usd": _money(usage.agent_cost_usd),
            },
            "judge": {
                "calls": usage.judge_calls,
                "tokens": usage.judge_tokens,
                "cost_usd": _money(usage.judge_cost_usd),
            },
        },
        "cases": [
            {
                "id": result.case.id,
                "category": result.case.category,
                "question": result.case.question,
                "passed": result.passed,
                "duration_s": round(result.duration_s, 2),
                "answer": result.answer.text if result.answer else None,
                "tools": [i.name for i in result.answer.tool_invocations] if result.answer else [],
                "citations": [c.index for c in result.answer.citations] if result.answer else [],
                "check_failures": list(result.check_failures),
                "agent_error": result.agent_error,
                "judge_error": result.judge_error,
                "verdict": result.verdict.model_dump() if result.verdict else None,
                "failure_reasons": _failure_reasons(result),
            }
            for result in results
        ],
    }


def _cost_line(label: str, calls: int, tokens: int, cost: Decimal | None) -> str:
    price = f"${cost:.4f}" if cost is not None else "цена недоступна (шлюз без прайслиста)"
    return f"- {label}: {calls} вызовов, {tokens} токенов, {price}"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def to_markdown(
    results: list[CaseResult],
    summary: Summary,
    usage: Usage,
    *,
    meta: dict[str, Any],
    violations: list[str],
) -> str:
    overall = summary.overall
    lines = [
        f"# Eval ответов — {meta['started_at']}",
        "",
        f"Модель агента: `{meta['agent_model']}`, судья: `{meta['judge_model']}`, "
        f"вопросов: {overall.total}.",
        "",
        f"**Pass rate: {overall.passed}/{overall.total} = {overall.pass_rate:.1%}**, "
        f"correctness {_num(overall.avg_correctness)}, grounding {_num(overall.avg_grounding)} "
        f"(по {overall.judged} оценённым судьёй).",
        "",
        "## По категориям",
        "",
        "| категория | pass | pass rate | correctness | grounding |",
        "|---|---|---|---|---|",
        *(
            f"| {name} | {s.passed}/{s.total} | {s.pass_rate:.0%} | "
            f"{_num(s.avg_correctness)} | {_num(s.avg_grounding)} |"
            for name, s in summary.by_category.items()
        ),
        "",
        "## Стоимость",
        "",
        _cost_line("агент", usage.agent_calls, usage.agent_tokens, usage.agent_cost_usd),
        _cost_line("судья", usage.judge_calls, usage.judge_tokens, usage.judge_cost_usd),
        "",
        "## Пороги",
        "",
        *(f"- ПРОВАЛ: {item}" for item in violations),
        *(["- не хуже бейзлайна"] if not violations else []),
        "",
        "## Провалы",
        "",
    ]
    failed = [result for result in results if not result.passed]
    if not failed:
        lines.append("Нет.")
    for result in failed:
        lines.append(f"### `{result.case.id}` ({result.case.category})")
        lines.append("")
        lines.append(f"Вопрос: {result.case.question}")
        lines.append("")
        lines.extend(f"- {reason}" for reason in _failure_reasons(result))
        if result.answer is not None:
            quoted = (f"> {row}" for row in result.answer.text.split("\n"))
            lines.extend(["", "Ответ:", "", *quoted])
        lines.append("")
    return "\n".join(lines) + "\n"


def write_reports(
    report_dir: Path,
    results: list[CaseResult],
    summary: Summary,
    usage: Usage,
    *,
    meta: dict[str, Any],
    violations: list[str],
) -> Path:
    """Пишет JSON и markdown рядом; возвращает путь к markdown (его читает человек)."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"answers-{stamp}.json"
    md_path = report_dir / f"answers-{stamp}.md"
    payload = to_json(results, summary, usage, meta=meta, violations=violations)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        to_markdown(results, summary, usage, meta=meta, violations=violations), encoding="utf-8"
    )
    return md_path
