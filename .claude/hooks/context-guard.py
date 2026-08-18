#!/usr/bin/env python3
"""PreToolUse-guard: блокирует операции, которые возвращают файл в контекст по второму разу.

Правила из секции «Экономия контекста» в CLAUDE.md не соблюдаются сами собой —
агент нарушает инструкцию, но не нарушает deny. Здесь запреты, а не просьбы.

Fail-open: любая внутренняя ошибка = разрешить (гард не должен блокировать работу).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Пути, на которые правила не распространяются: временное и сгенерированное.
EXEMPT_PATH = re.compile(r"/tmp/|scratchpad|\.venv/|node_modules/")

# Команды, которые пишут в файлы проекта за спиной харнесса.
BASH_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bruff\s+format\b"),
        "ruff format правит файлы вне Write/Edit — снапшот харнесса инвалидируется, "
        "и файл целиком приезжает в контекст второй раз. Форматирует pre-commit на "
        "`git commit`. Если формат нужен сейчас — `ruff format --diff` (не пишет).",
    ),
    (
        re.compile(r"\bruff\s+check\b[^|;&]*--(fix|unsafe-fixes)\b"),
        "`ruff check --fix` перезаписывает файлы → полное эхо файла в контекст. "
        "Запусти `ruff check` без --fix и поправь найденное одним Edit.",
    ),
    (
        re.compile(r"\bprettier\b[^|;&]*(--write|\s-w\b)"),
        "`prettier --write` перезаписывает файлы → полное эхо в контекст. "
        "Форматирует pre-commit на коммите; для проверки — `prettier --check`.",
    ),
    (
        re.compile(r"\beslint\b[^|;&]*--fix\b"),
        "`eslint --fix` перезаписывает файлы → полное эхо в контекст. "
        "Запусти `eslint` без --fix и поправь найденное через Edit.",
    ),
    (
        re.compile(r"\bpre-commit\s+run\b"),
        "`pre-commit run` прогоняет форматтеры по файлам → эхо всех затронутых файлов. "
        "Хуки сработают сами на `git commit`, когда содержимое файлов уже не нужно.",
    ),
    (
        re.compile(
            r"\b(ruff\s+check|mypy|eslint|tsc)\b|\bpnpm\s+(lint|typecheck)\b|\bruff\s+format\b"
        ),
        "Линт и тайпчек вручную не запускаются: pre-commit прогоняет ruff, mypy --strict, "
        "eslint и tsc на `git commit` — один раз, теми же конфигами, что и CI. Восемь "
        "ручных прогонов = восемь порций вывода в контекст за тот же результат. "
        "Нужна проверка — коммить; упавший хук покажет ровно те же ошибки. "
        "Коммить дробно, а не одним куском в конце. Тесты (`pytest`, `pnpm test`) — "
        "запускай сколько нужно, их в pre-commit нет.",
    ),
    (
        re.compile(r"\bsed\s+-i|\bperl\s+-[a-zA-Z]*i|\bawk\b[^|;&]*>\s*\S+\.(py|ts|tsx)"),
        "In-place codemod правит файл вне Write/Edit → полное эхо файла. "
        "Сделай ту же правку через Edit (в т.ч. Edit с replace_all).",
    ),
    (
        re.compile(r"\b(python3?|node|ruby|perl)\b[^|;&]*<<"),
        "Heredoc-скрипт, который пишет в файлы, — тот же codemod вне Write/Edit, "
        "с бонусом: он может молча не примениться, и придётся перечитывать файл. "
        "Правь через Edit/Write.",
    ),
    (
        re.compile(r">\s*[^\s|;&]*\.(py|ts|tsx|jsx|js|md)\b"),
        "Редирект в исходник пишет файл вне Write/Edit → полное эхо файла. "
        "Используй Write.",
    ),
    (
        re.compile(r"\bgit\s+(checkout|restore|stash)\b"),
        "git checkout/restore/stash меняет файлы на диске за спиной харнесса — всё, "
        "что ты держишь в контексте, приедет заново. Если правда нужно — сначала "
        "убедись, что затронутые файлы больше не нужны, и запусти через отдельный "
        "инструмент/подтверждение у пользователя.",
    ),
]

# Write по существующему файлу такого размера и больше = переписывание вместо правки.
WRITE_REWRITE_MIN_LINES = 60

# Read без limit по файлу такого размера и больше.
READ_FULL_MAX_LINES = 600


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def line_count(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def check_bash(cmd: str) -> None:
    if EXEMPT_PATH.search(cmd):
        return
    for pattern, reason in BASH_RULES:
        if pattern.search(cmd):
            deny(reason)


def check_write(tool_input: dict) -> None:
    raw = tool_input.get("file_path") or ""
    if not raw or EXEMPT_PATH.search(raw):
        return
    path = Path(raw)
    if not path.is_file():
        return
    lines = line_count(path)
    if lines < WRITE_REWRITE_MIN_LINES:
        return
    deny(
        f"Write по существующему файлу ({lines} строк) = весь файл ещё раз в контекст, "
        "плюс риск потерять то, чего ты не держишь в голове. Вноси изменения через Edit. "
        "Если файл правда надо переписать целиком — сначала реши форму до конца, "
        "затем удали файл и напиши заново."
    )


def check_read(tool_input: dict) -> None:
    raw = tool_input.get("file_path") or ""
    if not raw or EXEMPT_PATH.search(raw):
        return
    if tool_input.get("limit"):
        return
    path = Path(raw)
    if not path.is_file():
        return
    lines = line_count(path)
    if lines < READ_FULL_MAX_LINES:
        return
    deny(
        f"{path.name} — {lines} строк целиком в контекст. Если нужен один факт, найди его "
        "через Grep; если нужен фрагмент — Read с offset/limit. Если правда нужен весь "
        "файл, повтори Read с limit."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if tool == "Bash":
        check_bash(tool_input.get("command") or "")
    elif tool == "Write":
        check_write(tool_input)
    elif tool == "Read":
        check_read(tool_input)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — гард не имеет права ломать работу
        pass
