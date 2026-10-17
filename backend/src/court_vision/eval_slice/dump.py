"""Сборщик среза: полная база → сжатые COPY-дампы + манифест.

COPY-text, а не SQL-INSERT и не JSON: тот же формат, которым seed грузит датасет, а Postgres
отдаёт и принимает его без разбора на стороне Python. Вектор уезжает текстом `[0.1,...]` и
возвращается в `vector` как есть — "готовые эмбеддинги" не пересчитываются и не стоят денег.
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import asyncpg
import structlog
from sqlalchemy import Table
from sqlalchemy.orm import DeclarativeBase

from court_vision.answer_eval.golden import GoldenAnswer
from court_vision.eval_slice.config import (
    SliceConfig,
    coverage_problems,
    wanted_seasons,
    wikipedia_titles,
)
from court_vision.eval_slice.manifest import MANIFEST_NAME, Manifest, TableDump
from court_vision.models import BoxScore, Chunk, Document, Game, Player, Team

log = structlog.get_logger(__name__)

# Бюджет размера среза в git. Сверх него дампы живут asset'ом GitHub Release, а CI скачивает
# их перед прогоном (см. README).
BUDGET_BYTES: Final = 15 * 1024 * 1024


class SliceBuildError(Exception):
    """Срез собрать нельзя: в полной базе нет того, о чём спрашивает Золотой набор."""


class BudgetExceededError(SliceBuildError):
    def __init__(self, total: int) -> None:
        super().__init__(
            f"срез весит {total / 1024 / 1024:.1f} МБ при бюджете "
            f"{BUDGET_BYTES / 1024 / 1024:.0f} МБ: в git такой не кладём — выложить как asset "
            "GitHub Release (README, «Eval-срез»)"
        )
        self.total = total


@dataclass(frozen=True, slots=True)
class _Spec:
    """Одна таблица среза: что в ней выбирать и какими параметрами."""

    table: Table
    # Условие без `WHERE`; плейсхолдеры нумеруются по `uses` ($1, $2...).
    where: str = ""
    uses: tuple[str, ...] = ()


_DOCUMENTS_WHERE: Final = (
    "source_type = 'rulebook' OR (source_type = 'wikipedia' AND title = ANY($1::text[]))"
)
_SEASONS_WHERE: Final = "season = ANY($1::text[])"


def _table(model: type[DeclarativeBase]) -> Table:
    table = model.__table__
    assert isinstance(table, Table)
    return table


# Порядок = порядок загрузки: сначала то, на что ссылаются внешние ключи.
_SPECS: Final = (
    _Spec(_table(Team)),
    _Spec(_table(Player)),
    _Spec(_table(Game), _SEASONS_WHERE, ("seasons",)),
    _Spec(_table(BoxScore), _SEASONS_WHERE, ("seasons",)),
    _Spec(_table(Document), _DOCUMENTS_WHERE, ("titles",)),
    _Spec(
        _table(Chunk),
        f"document_id IN (SELECT id FROM documents WHERE {_DOCUMENTS_WHERE})",
        ("titles",),
    ),
)


def _columns(table: Table) -> tuple[str, ...]:
    """Колонки таблицы без генерируемых (`chunks.ts`): базa досчитает их сама."""
    return tuple(column.name for column in table.columns if column.computed is None)


def _select(spec: _Spec) -> str:
    columns = ", ".join(f'"{name}"' for name in _columns(spec.table))
    order = ", ".join(f'"{column.name}"' for column in spec.table.primary_key.columns)
    where = f" WHERE {spec.where}" if spec.where else ""
    return f"SELECT {columns} FROM {spec.table.name}{where} ORDER BY {order}"


async def build_slice(
    connection: asyncpg.Connection[Any],
    answers: list[GoldenAnswer],
    config: SliceConfig,
    out_dir: Path,
) -> Manifest:
    """Выгружает срез в `out_dir` и возвращает манифест.

    Читает одним снимком (`REPEATABLE READ`, read-only): срез непротиворечив, даже если
    в полную базу в это время что-то пишет индексация корпуса. Бюджет проверяется после
    записи — файлы остаются на диске, чтобы их можно было выложить asset'ом.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    async with connection.transaction(isolation="repeatable_read", readonly=True):
        source_seasons = [
            row["season"]
            for row in await connection.fetch("SELECT DISTINCT season FROM games ORDER BY 1")
        ]
        seasons = wanted_seasons(config, answers, available=source_seasons)
        titles = wikipedia_titles(answers)
        documents = await _document_titles(connection, titles)
        values = {"seasons": seasons, "titles": titles}

        dumps = [
            await _dump_table(connection, spec, [values[name] for name in spec.uses], out_dir)
            for spec in _SPECS
        ]
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
        models = await connection.fetch(
            "SELECT DISTINCT embedding_model FROM chunks WHERE "
            f"document_id IN (SELECT id FROM documents WHERE {_DOCUMENTS_WHERE}) ORDER BY 1",
            titles,
        )

    manifest = Manifest(
        alembic_revision=revision,
        embedding_models=tuple(row["embedding_model"] for row in models),
        seasons=tuple(seasons),
        source_seasons=tuple(source_seasons),
        documents=tuple(documents),
        tables=tuple(dumps),
    )
    (out_dir / MANIFEST_NAME).write_text(manifest.model_dump_json(indent=2) + "\n", "utf-8")

    problems = coverage_problems(
        answers,
        slice_seasons=manifest.seasons,
        source_seasons=manifest.source_seasons,
        slice_documents=manifest.documents,
    )
    if problems:
        raise SliceBuildError("Золотой набор не покрыт срезом: " + "; ".join(problems))
    if manifest.total_bytes > BUDGET_BYTES:
        raise BudgetExceededError(manifest.total_bytes)
    log.info("eval_slice.built", seasons=len(seasons), bytes=manifest.total_bytes)
    return manifest


async def _document_titles(connection: asyncpg.Connection[Any], titles: Iterable[str]) -> list[str]:
    """Заголовки документов среза; нет rulebook или названной статьи — срез не собрать."""
    wanted = list(titles)
    rows = await connection.fetch(
        f"SELECT title, source_type FROM documents WHERE {_DOCUMENTS_WHERE} ORDER BY id", wanted
    )
    found = [row["title"] for row in rows]
    if not any(row["source_type"] == "rulebook" for row in rows):
        raise SliceBuildError("в полной базе нет rulebook: `python -m court_vision.corpus`")
    missing = [title for title in wanted if title not in found]
    if missing:
        raise SliceBuildError(
            f"в корпусе полной базы нет статей {missing}: проиндексируй корпус "
            "(`python -m court_vision.corpus --source wikipedia`) или поправь expect_documents"
        )
    return found


async def _dump_table(
    connection: asyncpg.Connection[Any], spec: _Spec, args: list[Any], out_dir: Path
) -> TableDump:
    name = spec.table.name
    path = out_dir / f"{name}.copy.gz"

    # mtime=0 и пустое имя: в заголовок gzip не попадает ни время, ни путь, поэтому дамп
    # одних и тех же данных побайтово одинаков.
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as archive,
    ):

        async def sink(chunk: bytes) -> None:
            archive.write(chunk)

        status = await connection.copy_from_query(_select(spec), *args, output=sink, format="text")
    rows = int(status.rsplit(" ", 1)[-1])
    return TableDump(
        name=name,
        file=path.name,
        columns=_columns(spec.table),
        rows=rows,
        bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
