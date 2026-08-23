#!/usr/bin/env python3
"""PreToolUse-guard: два правила, которые возвращают файл в контекст по второму разу.

Правил нарочно мало. Каждый отказ стоит раунд-трип и рискует уйти в перебор
вариантов, поэтому здесь только то, у чего есть очевидная и более дешёвая
альтернатива. Остальное — вопрос того, как нарезана задача, а не запретов.

Fail-open: любая внутренняя ошибка = разрешить.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Пути, на которые правила не распространяются.
EXEMPT_PATH = re.compile(r"/tmp/|scratchpad|\.venv/|node_modules/")

# Тела heredoc'ов и содержимое кавычек — это данные (сообщения коммитов, паттерны
# grep), а не команды. Матчить по ним = ложные отказы.
HEREDOC_BODY = re.compile(r"<<-?\s*'?(\w+)'?.*?^\1", re.S | re.M)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# 1. Запись в файлы проекта за спиной харнесса: снапшот инвалидируется, и файл
#    целиком, с номерами строк, приезжает в контекст второй раз.
EXTERNAL_WRITE = re.compile(
    r"\bruff\s+format\b"
    r"|\bruff\s+check\b[^|;&]*--(fix|unsafe-fixes)\b"
    r"|\bprettier\b[^|;&]*(--write|\s-w\b)"
    r"|\beslint\b[^|;&]*--fix\b"
    r"|\bpre-commit\s+run\b"
    r"|\bsed\s+-i"
    r"|\bperl\s+-[a-zA-Z]*i"
    r"|\b(python3?|node|ruby|perl)\b[^|;&]*<<"
    r"|>\s*[^\s|;&]*\.(py|ts|tsx|jsx|js)\b"
)

EXTERNAL_WRITE_REASON = (
    "Команда пишет в файлы проекта вне Write/Edit — снапшот харнесса "
    "инвалидируется, и каждый затронутый файл целиком приезжает в контекст второй "
    "раз (в прошлый раз так ушло ~70k токенов). Правь через Edit/Write; "
    "форматирование сделает pre-commit на `git commit`, когда содержимое файлов "
    "уже не нужно."
)

# 2. Read без limit по большому файлу.
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


def check_bash(cmd: str) -> None:
    # Исключения смотрим по исходной команде: пути могут быть внутри heredoc'а,
    # который ниже вырезается. Ошибка в сторону «разрешить» здесь дешевле.
    if EXEMPT_PATH.search(cmd):
        return
    probe = QUOTED.sub(" ", HEREDOC_BODY.sub("<<BODY", cmd))
    if EXTERNAL_WRITE.search(probe):
        deny(EXTERNAL_WRITE_REASON)


def check_read(tool_input: dict) -> None:
    raw = tool_input.get("file_path") or ""
    if not raw or EXEMPT_PATH.search(raw) or tool_input.get("limit"):
        return
    path = Path(raw)
    if not path.is_file():
        return
    with path.open("rb") as fh:
        lines = sum(1 for _ in fh)
    if lines < READ_FULL_MAX_LINES:
        return
    deny(
        f"{path.name} — {lines} строк целиком в контекст. Нужен один факт — найди "
        "Grep'ом; нужен фрагмент — Read с offset/limit. Нужен весь файл — повтори "
        "Read с limit."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Bash":
        check_bash(tool_input.get("command") or "")
    elif payload.get("tool_name") == "Read":
        check_read(tool_input)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — гард не имеет права ломать работу
        pass
