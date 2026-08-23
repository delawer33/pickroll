"""Структурный чанкинг: документ режется по своим заголовкам, а не по окну символов.

Каждый чанк несёт локатор — человекочитаемый адрес внутри документа ("Rule 5 — Sec. II",
"Playing career > Chicago Bulls"): он уезжает в цитату и позволяет проверить ответ руками.
Инфобоксы, таблицы и ссылочная обвязка Wikipedia выбрасываются — это не текст, а разметка,
и в эмбеддинге она только шумит.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from court_vision.corpus.sources import RawDocument

# Потолок размера чанка. Токены оцениваются по символам (~4 символа на токен для
# английского): точный подсчёт требовал бы токенизатор конкретной модели, а границы
# чанков от этой точности не выигрывают.
MAX_CHUNK_TOKENS: Final = 500
_CHARS_PER_TOKEN: Final = 4
_MAX_CHUNK_CHARS: Final = MAX_CHUNK_TOKENS * _CHARS_PER_TOKEN

# Локатор чанка, который идёт до первого заголовка (преамбула статьи Wikipedia).
LEAD_LOCATOR: Final = "Lead"

# Заголовки rulebook как их отдаёт pypdf: "RULE NO. 1—COURT DIMENSIONS—EQUIPMENT",
# "Section I—Court and Dimensions". Тире встречается и em dash, и обычное.
_RULE_HEADING: Final = re.compile(r"^RULE NO\.\s*(\d+)\s*[—–-]\s*(.+)$")
_SECTION_HEADING: Final = re.compile(r"^Section\s+([IVXLC]+)\s*[—–-]\s*(.*)$")
# Колонтитулы страниц: "- 9 -" и повторяющийся титул "Official Rules".
_PAGE_ARTIFACT: Final = re.compile(r"^(-\s*\d+\s*-|Official Rules)$")

# Заголовки wikitext: "== Playing career ==", "=== Chicago Bulls ===".
_WIKI_HEADING: Final = re.compile(r"^(={2,6})\s*(.+?)\s*=+\s*$")
# Служебные секции Wikipedia: ссылки и списки литературы — не знания о предмете.
_WIKI_SKIPPED_SECTIONS: Final = frozenset(
    {"references", "external links", "see also", "notes", "further reading", "bibliography"}
)


@dataclass(frozen=True)
class ChunkDraft:
    """Чанк до записи в БД: текст для хранения и текст для эмбеддинга.

    `embed_text` дополняет текст адресом ("title > locator"): без него чанк «Section II»
    из правила 5 и такой же из правила 12 неразличимы для векторного поиска.
    """

    locator: str
    text: str
    embed_text: str


def chunk_document(doc: RawDocument) -> list[ChunkDraft]:
    """Режет документ по структуре источника; секции длиннее потолка делятся дальше."""
    if doc.source_type == "rulebook":
        sections = _rulebook_sections(doc.text)
    else:
        sections = _wikipedia_sections(doc.text)
    return [
        _draft(doc.title, locator, piece)
        for locator, text in sections
        for piece in _split_oversized(text)
    ]


def _draft(title: str, locator: str, text: str) -> ChunkDraft:
    return ChunkDraft(locator=locator, text=text, embed_text=f"{title} > {locator}\n{text}")


def _split_oversized(text: str) -> list[str]:
    """Секция длиннее потолка делится по строкам — жадно, без разрыва строки пополам."""
    if len(text) <= _MAX_CHUNK_CHARS:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        if current and size + len(line) > _MAX_CHUNK_CHARS:
            pieces.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


# --- rulebook -------------------------------------------------------------------------


def _rulebook_sections(text: str) -> list[tuple[str, str]]:
    """(локатор, текст) по заголовкам RULE/Section. Всё до первого правила — обложка,
    оглавление и диаграммы площадки — выбрасывается."""
    sections: list[tuple[str, str]] = []
    rule_number: str | None = None
    locator: str | None = None
    heading: str | None = None
    lines: list[str] = []

    def flush() -> None:
        # Секция без тела (правило, сразу переходящее к Section I) чанка не даёт:
        # заголовок сам по себе — не знание, искать по нему нечего.
        body = "\n".join(lines).strip()
        if locator is not None and body:
            sections.append((locator, f"{heading}\n{body}" if heading else body))
        lines.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if _PAGE_ARTIFACT.match(stripped):
            continue
        if rule := _RULE_HEADING.match(stripped):
            flush()
            rule_number = rule.group(1)
            locator = f"Rule {rule_number}"
            heading = stripped
            continue
        if (section := _SECTION_HEADING.match(stripped)) and rule_number is not None:
            flush()
            locator = f"Rule {rule_number} — Sec. {section.group(1)}"
            heading = stripped
            continue
        if locator is not None:
            lines.append(line)
    flush()
    return sections


# --- wikipedia ------------------------------------------------------------------------


def _wikipedia_sections(text: str) -> list[tuple[str, str]]:
    """(локатор, текст) по заголовкам wikitext; путь секции — "h2 > h3 > ...".

    Служебные секции пропускаются вместе со своими подсекциями: заголовок уровня N
    закрывается только заголовком уровня ≤ N.
    """
    cleaned = _strip_markup(text)
    sections: list[tuple[str, str]] = []
    path: list[str] = []
    skipped_level: int | None = None
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body and skipped_level is None:
            sections.append((" > ".join(path) if path else LEAD_LOCATOR, body))
        lines.clear()

    for line in cleaned.splitlines():
        heading = _WIKI_HEADING.match(line.strip())
        if heading is None:
            lines.append(line)
            continue
        flush()
        level = len(heading.group(1))
        title = heading.group(2).strip()
        if skipped_level is not None and level > skipped_level:
            continue
        skipped_level = None
        # Уровень 2 — первый элемент пути: path[level - 2] и есть его место.
        del path[level - 2 :]
        path.append(title)
        if title.lower() in _WIKI_SKIPPED_SECTIONS:
            skipped_level = level
    flush()
    return sections


def _strip_markup(text: str) -> str:
    """Убирает из wikitext всё, что не текст: шаблоны (инфобоксы), таблицы, файлы,
    сноски и оформление ссылок. Заголовки остаются — по ним режем."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>/]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = _drop_balanced(text, "{{", "}}")
    text = _drop_balanced(text, "{|", "|}")
    text = _drop_balanced(text, "[[File:", "]]")
    text = _drop_balanced(text, "[[Image:", "]]")
    # [[target|label]] → label, [[target]] → target.
    text = re.sub(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]*)\]\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _drop_balanced(text: str, opening: str, closing: str) -> str:
    """Вырезает конструкции `opening ... closing` с учётом вложенности.

    Регулярным выражением вложенные шаблоны ({{Infobox ... {{height|...}} ...}}) не
    берутся — нужен счётчик глубины.
    """
    result: list[str] = []
    index = 0
    depth = 0
    while index < len(text):
        if text.startswith(opening, index):
            depth += 1
            index += len(opening)
            continue
        if depth and text.startswith(closing, index):
            depth -= 1
            index += len(closing)
            continue
        # Внутри [[File: ...]] встречаются вложенные [[ссылки]] — их скобки тоже
        # учитываются, иначе первый `]]` закрыл бы файл посередине.
        if depth and opening.startswith("[[") and text.startswith("[[", index):
            depth += 1
            index += 2
            continue
        if depth:
            index += 1
            continue
        result.append(text[index])
        index += 1
    return "".join(result)
