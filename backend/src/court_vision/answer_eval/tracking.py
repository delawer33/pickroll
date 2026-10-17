"""Прогон eval как dataset run в Langfuse (Experiments).

Включается теми же ключами, что и трейсинг агента, и так же молча выключается без них.
Сбой Langfuse никогда не роняет прогон: результат уже посчитан, оплачен и лежит в отчёте,
а внешний сервис — удобство для сравнения прогонов, не источник истины.
"""

from __future__ import annotations

from typing import Any

import structlog

from court_vision.answer_eval.runner import CaseResult
from court_vision.config import Settings

log = structlog.get_logger(__name__)

DATASET_NAME = "court-vision/answers"


def langfuse_enabled(settings: Settings) -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def publish_dataset_run(settings: Settings, results: list[CaseResult], *, run_name: str) -> bool:
    """Записывает прогон: вопросы — элементы датасета, ответы — выход задачи, баллы —
    оценки. Синхронная: Experiments SDK крутит собственный цикл событий, поэтому из
    асинхронного кода вызывается через `asyncio.to_thread`. True — прогон записан."""
    if not langfuse_enabled(settings):
        log.info("answer_eval.langfuse_skipped")
        return False
    try:
        return _publish(settings, results, run_name=run_name)
    except Exception:
        log.exception("answer_eval.langfuse_failed")
        return False


def _publish(settings: Settings, results: list[CaseResult], *, run_name: str) -> bool:
    from langfuse import Evaluation, Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host or None,
    )
    by_id = {result.case.id: result for result in results}
    client.create_dataset(name=DATASET_NAME, description="Золотой набор ответов Court Vision")
    for result in results:
        case = result.case
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=case.id,
            input={"question": case.question},
            expected_output={"reference": list(case.reference)},
            metadata={"id": case.id, "category": case.category},
        )
    items = [item for item in client.get_dataset(DATASET_NAME).items if item.id in by_id]

    def task(*, item: Any, **kwargs: object) -> str:
        answer = by_id[item.id].answer
        return answer.text if answer else ""

    def evaluator(*, metadata: dict[str, str] | None = None, **kwargs: object) -> list[Evaluation]:
        result = by_id[(metadata or {})["id"]]
        scores = [Evaluation(name="passed", value=1.0 if result.passed else 0.0)]
        if result.verdict is not None:
            scores += [
                Evaluation(name="correctness", value=float(result.verdict.correctness)),
                Evaluation(name="grounding", value=float(result.verdict.grounding)),
            ]
        return scores

    client.run_experiment(
        name="answers",
        run_name=run_name,
        data=items,
        task=task,
        evaluators=[evaluator],
    )
    client.flush()
    log.info("answer_eval.langfuse_published", run=run_name, items=len(items))
    return True
