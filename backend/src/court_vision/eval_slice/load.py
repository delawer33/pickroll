"""Загрузчик среза: миграции на пустую базу, COPY дампов, пересчёт производных.

Грузит только в пустую базу (или в ту, где срез уже лежит): полная локальная база разработчика
под этот загрузчик не подходит, и отказ здесь дешевле, чем тихо перемешанные данные. Поэтому
`answer_eval --slice` безопасно запускать с любым `DATABASE_URL`.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO, Final, Literal, cast

import asyncpg
import structlog
from alembic import command
from alembic.config import Config

from court_vision.answer_eval.golden import GoldenAnswer
from court_vision.db import create_engine, create_session_factory
from court_vision.eval_slice.config import coverage_problems
from court_vision.eval_slice.derived import rebuild_derived
from court_vision.eval_slice.manifest import Manifest, read_manifest
from court_vision.seed.__main__ import asyncpg_dsn

log = structlog.get_logger(__name__)

BACKEND_ROOT: Final = Path(__file__).resolve().parents[3]

SliceState = Literal["empty", "loaded"]

# Таблицы с identity-ключом: после COPY с явными id последовательность надо подвинуть, иначе
# первая же вставка (индексация корпуса, запись usage) упадёт на дубле ключа.
_IDENTITY_TABLES: Final = ("documents", "chunks")


class SliceLoadError(Exception):
    """Срез в эту базу загрузить нельзя (чужие данные, битый дамп, чужой эмбеддер)."""


async def slice_state(connection: asyncpg.Connection[Any], manifest: Manifest) -> SliceState:
    """`empty` — таблиц срез ещё нет или они пусты; `loaded` — в базе ровно этот срез.

    Остальное — чужие данные: загрузчик отказывается, не трогая базу. Совпадение определяется
    по числу строк каждой таблицы: срез собирается целыми таблицами/сезонами, и полная база
    или другой срез с теми же счётчиками практически невозможны.
    """
    names = [table.name for table in manifest.tables]
    present = [
        name
        for name in names
        if await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{name}")
    ]
    if not present:
        return "empty"
    if len(present) != len(names):
        raise SliceLoadError(f"схема базы неполная (есть {present}) — это не пустая база")
    counts = {name: await connection.fetchval(f"SELECT count(*) FROM {name}") for name in names}
    if not any(counts.values()):
        return "empty"
    if all(counts[table.name] == table.rows for table in manifest.tables):
        return "loaded"
    raise SliceLoadError(
        f"в базе уже есть данные ({counts}), и это не Eval-срез: загрузчик грузит срез "
        "только в пустую базу. Создай чистую (`createdb`) и укажи её в DATABASE_URL"
    )


def migrate(database_url: str) -> None:
    """`alembic upgrade head`. Синхронно: env.py Alembic сам запускает event loop, поэтому
    из async-кода это зовётся через `asyncio.to_thread`."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def verify_files(directory: Path, manifest: Manifest) -> None:
    for table in manifest.tables:
        path = directory / table.file
        if not path.exists():
            raise SliceLoadError(f"нет файла {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != table.sha256:
            raise SliceLoadError(f"{path.name}: контрольная сумма не совпадает с манифестом")


async def load_slice(
    connection: asyncpg.Connection[Any], directory: Path, manifest: Manifest
) -> None:
    """Заливает дампы одной транзакцией: либо весь срез, либо ничего."""
    verify_files(directory, manifest)
    async with connection.transaction():
        for table in manifest.tables:
            with gzip.open(directory / table.file, "rb") as source:
                await connection.copy_to_table(
                    table.name, source=cast(BinaryIO, source), columns=table.columns, format="text"
                )
            loaded = await connection.fetchval(f"SELECT count(*) FROM {table.name}")
            if loaded != table.rows:
                raise SliceLoadError(f"{table.name}: загружено {loaded}, в манифесте {table.rows}")
        for name in _IDENTITY_TABLES:
            await connection.execute(
                f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                f"(SELECT max(id) FROM {name}))"
            )
    # Статистика планировщика: без неё первый же запрос по свежезалитому box_scores идёт по
    # устаревшим оценкам и может выбрать плохой план.
    await connection.execute("ANALYZE")
    log.info("eval_slice.loaded", tables=len(manifest.tables), bytes=manifest.total_bytes)


def check_run_fits_slice(
    manifest: Manifest, answers: Iterable[GoldenAnswer], *, embedding_model: str
) -> None:
    """Проверки до первого платного вызова: срез отвечает на выбранные вопросы и совместим
    с эмбеддером. Устаревший срез (набор дописали, срез не пересобрали) падает здесь, а не
    молчаливым «данных нет» в ответе агента."""
    problems = coverage_problems(
        answers,
        slice_seasons=manifest.seasons,
        source_seasons=manifest.source_seasons,
        slice_documents=manifest.documents,
    )
    if problems:
        raise SliceLoadError(
            "срез устарел относительно Золотого набора, пересобери его "
            "(`python -m court_vision.eval_slice build`): " + "; ".join(problems)
        )
    if manifest.embedding_models and embedding_model not in manifest.embedding_models:
        raise SliceLoadError(
            f"векторы среза посчитаны {list(manifest.embedding_models)}, а EMBEDDING_MODEL="
            f"{embedding_model!r}: поиск по корпусу вернёт мусор. Выставь ту же модель или "
            "пересобери срез"
        )


async def prepare_slice(database_url: str, directory: Path) -> Manifest:
    """Приводит базу к состоянию «срез загружен»: миграции → заливка → производные.

    Идемпотентно: если срез уже лежит в базе, заливка пропускается, а миграции и пересчёт
    производных повторяются (оба безопасны).
    """
    manifest = read_manifest(directory)
    dsn = asyncpg_dsn(database_url)

    connection = await asyncpg.connect(dsn)
    try:
        state = await slice_state(connection, manifest)
    finally:
        await connection.close()

    await asyncio.to_thread(migrate, database_url)

    if state == "empty":
        connection = await asyncpg.connect(dsn)
        try:
            await load_slice(connection, directory, manifest)
        finally:
            await connection.close()
    else:
        log.info("eval_slice.already_loaded")

    engine = create_engine(database_url)
    try:
        await rebuild_derived(create_session_factory(engine))
    finally:
        await engine.dispose()
    return manifest
