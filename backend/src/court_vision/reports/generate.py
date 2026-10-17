"""Генерация отчёта: structured output потоком, частичные снапшоты, финальная валидация.

Модель получает вход (`ReportInput`) и JSON Schema отчёта и отвечает одним JSON. Пока он
пишется, сервис отдаёт снапшоты — частичный JSON разбирает pydantic (`allow_partial`), а не
ручной парсер. Готовый ответ проходит три проверки: схема, метрики есть во входе, сопоставимые
игроки есть во входе. Невалидный ответ получает один повтор с текстом нарушений в промпте;
второй невалидный — `ReportGenerationFailed`.

Каждая попытка — вызов через общий транспорт, то есть своя строка `llm_usage`. Повтор
пишется под отдельной операцией (`reports.generate_retry`): доля повторов среди вызовов и есть
счётчик отчётов, не прошедших проверку с первого раза. Исход каждой попытки — ещё и событие
structlog `reports.validation`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Final

import structlog
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.agent.errors import IncompleteAnswer
from court_vision.agent.prompts import load_prompt
from court_vision.agent.transport import LlmReply, TextDelta, stream
from court_vision.config import Settings
from court_vision.reports.errors import ReportGenerationFailed
from court_vision.reports.inputs import ReportInput
from court_vision.reports.schemas import (
    PartialScoutingReport,
    ScoutingReportContent,
    content_json_schema,
)

log = structlog.get_logger(__name__)

OPERATION: Final = "reports.generate"
RETRY_OPERATION: Final = "reports.generate_retry"
# Одна повторная попытка с ошибкой в промпте: вторая ошибка подряд — это не случайность, а
# вход или модель, которым повтор не поможет, и платить за третью попытку незачем.
MAX_ATTEMPTS: Final = 2

_PARTIAL: Final = TypeAdapter(PartialScoutingReport)
# Часть шлюзов оборачивает structured output в забор ```json — терпим и в финале, и в потоке.
_FENCE: Final = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    """Что модель успела написать к этому моменту; заменяет предыдущий снапшот целиком."""

    report: PartialScoutingReport


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    """Отчёт, прошедший все проверки. Последнее событие потока генерации."""

    content: ScoutingReportContent
    model: str


def response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": "scouting_report", "strict": True, "schema": content_json_schema()},
    }


def check_grounding(content: ScoutingReportContent, report_input: ReportInput) -> list[str]:
    """Нарушения, которые схема выразить не может: пустые разделы, метрики и сопоставимые
    игроки вне входа. Пустой список — отчёт чист."""
    violations: list[str] = []
    for name in ("summary", "archetype", "stat_profile", "outlook"):
        if not getattr(content, name).strip():
            violations.append(f"{name}: раздел пуст")

    allowed = set(report_input.metric_keys())
    for section in ("strengths", "weaknesses"):
        points = getattr(content, section)
        if not points:
            violations.append(f"{section}: нужен хотя бы один пункт")
        for index, point in enumerate(points):
            where = f"{section}[{index}]"
            if not point.claim.strip():
                violations.append(f"{where}: пустое утверждение")
            if not point.metrics:
                violations.append(f"{where}: утверждение без метрики")
            for key in point.metrics:
                if key not in allowed:
                    violations.append(
                        f"{where}: метрики `{key}` нет среди allowed_metric_keys входа"
                    )

    known = report_input.comparable_keys()
    for index, comparable in enumerate(content.comparables):
        if (comparable.player_name.casefold(), comparable.season) not in known:
            violations.append(
                f"comparables[{index}]: «{comparable.player_name}» ({comparable.season}) "
                "нет среди Похожих игроков входа"
            )
    return violations


async def generate_report(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    report_input: ReportInput,
    trace_metadata: dict[str, Any] | None = None,
) -> AsyncGenerator[ReportSnapshot | GeneratedReport]:
    """Снапшоты по мере генерации, последним — `GeneratedReport`; иначе доменная ошибка."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": load_prompt("reports/generate")},
        {"role": "user", "content": f"## Вход отчёта\n\n```json\n{report_input.prompt()}\n```"},
    ]
    violations: list[str] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            # Повтор пишет отчёт с нуля: клиент, не получив сброса, показал бы разделы
            # первой попытки под началом второй.
            yield ReportSnapshot(PartialScoutingReport())

        reply: LlmReply | None = None
        buffer = ""
        last: PartialScoutingReport | None = None
        async with aclosing(
            stream(
                session_factory,
                model=settings.llm_model,
                messages=messages,
                tools=[],
                timeout=settings.llm_timeout_seconds,
                operation=OPERATION if attempt == 1 else RETRY_OPERATION,
                api_key=settings.llm_api_key,
                api_base=settings.llm_api_base,
                trace_metadata=trace_metadata,
                response_format=response_format(),
            )
        ) as parts:
            async for part in parts:
                if isinstance(part, TextDelta):
                    buffer += part.text
                    snapshot = _partial(buffer)
                    if snapshot is not None and snapshot != last:
                        last = snapshot
                        yield ReportSnapshot(snapshot)
                else:
                    reply = part
        if reply is None:
            # Транспорт заканчивает поток ровно одним LlmReply либо бросает доменную ошибку.
            raise IncompleteAnswer("no_reply")

        content, violations = _validate(reply, report_input)
        log.info(
            "reports.validation",
            valid=content is not None,
            attempt=attempt,
            violations=len(violations),
            player_id=report_input.player.player_id,
            season=report_input.season,
        )
        if content is not None:
            yield GeneratedReport(content=content, model=settings.llm_model)
            return
        log.warning("reports.invalid", attempt=attempt, violations=violations)
        messages.extend([reply.message, {"role": "user", "content": _retry_prompt(violations)}])

    raise ReportGenerationFailed(violations, attempts=MAX_ATTEMPTS)


def _partial(buffer: str) -> PartialScoutingReport | None:
    """Снапшот из недописанного JSON. Пока в буфере нет ни одного значения, разбирать нечего."""
    try:
        return _PARTIAL.validate_json(_FENCE.sub("", buffer), experimental_allow_partial=True)
    except ValidationError:
        return None


def _validate(
    reply: LlmReply, report_input: ReportInput
) -> tuple[ScoutingReportContent | None, list[str]]:
    if reply.finish_reason == "length":
        # Обрезанный JSON не годится, даже если случайно оборвался на границе поля.
        return None, ["ответ оборван по лимиту токенов: сделай отчёт короче"]
    try:
        content = ScoutingReportContent.model_validate_json(_FENCE.sub("", reply.text.strip()))
    except ValidationError as error:
        return None, [
            f"{'.'.join(str(part) for part in item['loc']) or 'JSON'}: {item['msg']}"
            for item in error.errors()
        ]
    violations = check_grounding(content, report_input)
    return (None, violations) if violations else (content, [])


def _retry_prompt(violations: list[str]) -> str:
    listed = "\n".join(f"- {violation}" for violation in violations[:20])
    return (
        "Отчёт не прошёл проверку:\n"
        f"{listed}\n\n"
        "Верни отчёт целиком заново, исправив именно это. Ссылайся только на метрики из "
        "`allowed_metric_keys`, сопоставимых игроков бери только из "
        "`fingerprint.similar_players` (без отпечатка `comparables` — пустой список)."
    )
