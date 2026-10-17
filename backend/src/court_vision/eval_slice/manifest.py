"""Манифест среза: что лежит в дампах и чем это проверить.

Манифест — единственный файл, который читает загрузчик до открытия дампов: по нему он
решает, пуста ли база, не битые ли файлы и совпадает ли срез с настройками эмбеддера.
Времени сборки в нём нет намеренно: пересборка на тех же данных даёт те же байты, и git не
видит пустого диффа.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

MANIFEST_NAME: Final = "manifest.json"


class TableDump(BaseModel):
    """Дамп одной таблицы: `COPY ... TO STDOUT` в text-формате, сжатый gzip."""

    model_config = ConfigDict(frozen=True)

    name: str
    file: str
    # Колонки в порядке COPY. Загрузчик берёт их отсюда, а не из текущих моделей: новая
    # колонка с default'ом, добавленная миграцией после сборки среза, срез не ломает.
    columns: tuple[str, ...]
    rows: int
    bytes: int
    sha256: str


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = 1
    # Ревизия Alembic полной базы на момент сборки: справка, почему срез мог устареть.
    alembic_revision: str
    # Чем посчитаны векторы чанков. Запросы eval эмбеддятся настроенной моделью, и если она
    # другая, поиск по срезу молча деградирует, — `--slice` сверяет это до первого вопроса.
    embedding_models: tuple[str, ...]
    seasons: tuple[str, ...]
    # Все сезоны полной базы: по ним проверка покрытия отличает «сезон не взяли» от «сезона
    # нет нигде» (вопрос про 2035-36 — законная проверка отказа).
    source_seasons: tuple[str, ...]
    # Заголовки документов среза (rulebook и статьи Wikipedia).
    documents: tuple[str, ...]
    tables: tuple[TableDump, ...]

    @property
    def total_bytes(self) -> int:
        return sum(table.bytes for table in self.tables)

    def table(self, name: str) -> TableDump:
        return next(item for item in self.tables if item.name == name)


def read_manifest(directory: Path) -> Manifest:
    path = directory / MANIFEST_NAME
    if not path.exists():
        raise FileNotFoundError(
            f"нет {path}: срез не собран — `uv run python -m court_vision.eval_slice build`"
        )
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
