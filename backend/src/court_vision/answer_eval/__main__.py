"""CLI eval ответов: `uv run python -m court_vision.answer_eval`.

Гоняет Золотой набор ответов (`backend/evals/answers.yaml`) через прод-агента на той базе,
что указана в `DATABASE_URL`, судит ответы LLM-судьёй, печатает сводку и пишет отчёт
JSON + markdown в `backend/evals/reports/` (каталог игнорируется git). Код выхода 1 — прогон
хуже коммитимых порогов (`evals/answers_thresholds.yaml`).

`--slice` — прогон на герметичном Eval-срезе (`python -m court_vision.eval_slice`): база из
`DATABASE_URL` должна быть пустой, срез заливается в неё сам.

Прогон стоит денег: пока итерируешь над одной проблемой — `--category` или `--id`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from court_vision.answer_eval import report, tracking
from court_vision.answer_eval.golden import (
    DEFAULT_ANSWERS_PATH,
    DEFAULT_THRESHOLDS_PATH,
    EVALS_DIR,
    load_answers,
    select,
)
from court_vision.answer_eval.judge import make_judge
from court_vision.answer_eval.runner import (
    Usage,
    agent_answer_fn,
    collect_usage,
    db_now,
    evaluate_answers,
)
from court_vision.config import Settings, get_settings
from court_vision.db import create_engine, create_session_factory
from court_vision.eval_slice.config import DEFAULT_SLICE_DIR
from court_vision.eval_slice.load import check_run_fits_slice, prepare_slice
from court_vision.eval_slice.manifest import read_manifest
from court_vision.logging import configure_logging
from court_vision.observability import configure_llm_observability

log = structlog.get_logger(__name__)

DEFAULT_REPORT_DIR = EVALS_DIR / "reports"


async def run(
    settings: Settings,
    *,
    categories: list[str] | None,
    ids: list[str] | None,
    answers_path: Path,
    thresholds_path: Path | None,
    report_dir: Path,
    concurrency: int,
    slice_dir: Path | None = None,
) -> int:
    all_cases = load_answers(answers_path)
    cases = select(all_cases, categories=categories, ids=ids)
    full_run = len(cases) == len(all_cases)

    slice_info: dict[str, Any] | None = None
    if slice_dir is not None:
        # Срез проверяется до загрузки и до первого платного вызова: устаревший или с чужим
        # эмбеддером он дал бы прогон, в котором агент честно отвечает «данных нет».
        manifest = read_manifest(slice_dir)
        check_run_fits_slice(manifest, cases, embedding_model=settings.embedding_model)
        await prepare_slice(settings.database_url, slice_dir)
        slice_info = {"seasons": list(manifest.seasons), "documents": list(manifest.documents)}

    configure_llm_observability(settings)
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        started = await db_now(session_factory)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        results = await evaluate_answers(
            cases,
            answer_fn=agent_answer_fn(session_factory, settings, run_id=run_id),
            judge_fn=make_judge(session_factory, settings),
            concurrency=concurrency,
        )
        usage = await collect_usage(session_factory, since=started)
    finally:
        await engine.dispose()

    summary = report.summarize(results)
    violations: list[str] = []
    if thresholds_path is not None:
        violations = report.compare(
            summary, report.load_thresholds(thresholds_path), full_run=full_run
        )

    meta = {
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent_model": settings.llm_model,
        "judge_model": settings.eval_judge_model,
        "full_run": full_run,
        "filters": {"category": categories, "id": ids},
        "slice": slice_info,
    }
    path = report.write_reports(
        report_dir, results, summary, usage, meta=meta, violations=violations
    )
    await asyncio.to_thread(
        tracking.publish_dataset_run, settings, results, run_name=f"answers-{run_id}"
    )

    _print_summary(summary, usage, violations, path)
    return 1 if violations else 0


def _print_summary(
    summary: report.Summary, usage: Usage, violations: list[str], path: Path
) -> None:
    """print, а не лог: сводка и есть результат команды."""
    overall = summary.overall
    print(f"pass rate: {overall.passed}/{overall.total} = {overall.pass_rate:.1%}")
    for name, stats in summary.by_category.items():
        print(f"  {name:12}{stats.passed}/{stats.total}")
    print(
        f"агент: {usage.agent_tokens} токенов, cost {usage.agent_cost_usd}; "
        f"судья: {usage.judge_tokens} токенов, cost {usage.judge_cost_usd}"
    )
    for item in violations:
        print(f"ПРОВАЛ: {item}")
    print(f"отчёт: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="court_vision.answer_eval", description=__doc__)
    parser.add_argument(
        "--category", action="append", help="только эта категория (можно несколько раз)"
    )
    parser.add_argument("--id", action="append", help="только этот вопрос (можно несколько раз)")
    parser.add_argument(
        "--concurrency", type=int, default=2, help="сколько вопросов одновременно (по умолчанию 2)"
    )
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS_PATH)
    parser.add_argument(
        "--thresholds", type=Path, default=DEFAULT_THRESHOLDS_PATH, help="файл порогов"
    )
    parser.add_argument(
        "--no-thresholds", action="store_true", help="не сравнивать с порогами (код выхода 0)"
    )
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--slice",
        action="store_true",
        help="прогон на Eval-срезе: перед вопросами миграции и заливка среза в ПУСТУЮ базу "
        "из DATABASE_URL (в базу с другими данными не грузит)",
    )
    parser.add_argument(
        "--slice-dir", type=Path, default=DEFAULT_SLICE_DIR, help="каталог среза для --slice"
    )
    args = parser.parse_args()

    configure_logging()
    code = asyncio.run(
        run(
            get_settings(),
            categories=args.category,
            ids=args.id,
            answers_path=args.answers,
            thresholds_path=None if args.no_thresholds else args.thresholds,
            report_dir=args.report_dir,
            concurrency=args.concurrency,
            slice_dir=args.slice_dir if args.slice else None,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
