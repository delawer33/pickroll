"""Промпты — файлы репозитория, а не строки в коде.

Смысл правила процессный: правка формулировки обязана быть видна в диффе PR отдельной
строкой и проходить прогон eval-сьюта (спринт 5), а не прятаться внутри выражения на
Python рядом с логикой.
"""

from functools import lru_cache
from pathlib import Path
from typing import Final

PROMPTS_DIR: Final = Path(__file__).parent / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    """Промпт по имени относительно `prompts/`: `system`, `tools/get_player_stats`.

    Кешируется: файл на диске за время жизни процесса не меняется, а читается он на каждый
    запрос пользователя.
    """
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
