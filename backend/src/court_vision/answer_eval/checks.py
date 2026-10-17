"""Детерминированные проверки ответа: всё, что можно решить без модели.

Дешёвое судье не отдаётся: провал здесь означает провал вопроса без вызова судьи (см.
`runner`), и деньги на судью уходят только на ответы, которые прошли формальный фильтр.
"""

from __future__ import annotations

import re
from typing import Final

from court_vision.agent.loop import AgentAnswer, TurnCitation
from court_vision.answer_eval.golden import GoldenAnswer, SourceName
from court_vision.corpus.sources import RULEBOOK_TITLE

_MARKER: Final = re.compile(r"\[(\d+)\]")

# Шаблоны отказа — то, чем агент отвечает, когда сдаётся. Фразы нарочно узкие («данных нет»
# целиком, а не слово «нет»): ответ, который честно оговаривает пробел в одной метрике
# («по плей-офф данных нет»), отказом не считается, если по существу вопроса данные даны.
# Но и они срабатывают на таких оговорках — для вопросов, где оговорка законна, в наборе
# `must_not_refuse: false` или формулировка вопроса, не провоцирующая её.
_REFUSAL: Final = re.compile(
    "|".join(
        (
            r"таких данных (у меня )?нет",
            r"(у меня )?нет (таких )?(данных|информации)",
            r"данных (у меня )?нет",
            r"не могу (ответить|помочь|найти|подтвердить)",
            r"не (смог|удалось) найти",
            r"i (don'?t|do not) have (any |the )?(data|information)",
            r"no (such )?(data|information) (is |was )?(available|found)",
            r"i('m| am) (not able|unable) to",
            r"i (can'?t|cannot) (answer|help|find)",
        )
    ),
    re.IGNORECASE,
)


def citation_source(citation: TurnCitation) -> SourceName:
    """Источник цитаты. В `TurnCitation` типа источника нет, а документов у корпуса два
    вида: rulebook — один документ с известным названием, всё остальное — Wikipedia."""
    return "rulebook" if citation.document_title == RULEBOOK_TITLE else "wikipedia"


def invalid_markers(answer: AgentAnswer) -> list[int]:
    """Маркеры `[n]` в тексте, за которыми нет Цитаты. Цикл превращает в Цитаты только
    выданные номера, поэтому всё остальное — маркер-сирота, то есть выдуманная ссылка."""
    cited = {citation.index for citation in answer.citations}
    markers = {int(match.group(1)) for match in _MARKER.finditer(answer.text)}
    return sorted(markers - cited)


def is_refusal(text: str) -> bool:
    return _REFUSAL.search(text) is not None


def run_checks(case: GoldenAnswer, answer: AgentAnswer) -> list[str]:
    """Причины провала детерминированных проверок; пустой список — все пройдены."""
    failures: list[str] = []

    called = {invocation.name for invocation in answer.tool_invocations}
    missing_tools = [tool for tool in case.expect_tools if tool not in called]
    if missing_tools:
        failures.append(f"expect_tools: не вызваны {missing_tools}")

    orphans = invalid_markers(answer)
    if orphans:
        failures.append(f"markers: маркеры без Цитаты {orphans}")

    cited_sources = {citation_source(citation) for citation in answer.citations}
    missing_sources = [source for source in case.expect_sources if source not in cited_sources]
    if missing_sources:
        failures.append(f"expect_sources: нет Цитат из {missing_sources}")

    if case.must_not_refuse and is_refusal(answer.text):
        failures.append("must_not_refuse: ответ — отказ")

    return failures
