"""CLI Eval-среза: `uv run python -m court_vision.eval_slice {build,load}`.

`build` читает ПОЛНУЮ базу из `DATABASE_URL` (только чтение) и пишет срез в `--dir`
(по умолчанию `backend/evals/slice/`, коммитится). `load` заливает срез в ПУСТУЮ базу из
`DATABASE_URL`: прогон `answer_eval --slice` делает это сам, отдельная команда нужна, чтобы
поднять срез для ручной работы.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncpg
import structlog

from court_vision.answer_eval.golden import DEFAULT_ANSWERS_PATH, load_answers
from court_vision.config import get_settings
from court_vision.eval_slice.config import DEFAULT_CONFIG_PATH, DEFAULT_SLICE_DIR, load_config
from court_vision.eval_slice.dump import build_slice
from court_vision.eval_slice.load import prepare_slice
from court_vision.logging import configure_logging
from court_vision.seed.__main__ import asyncpg_dsn

log = structlog.get_logger(__name__)


async def run_build(database_url: str, *, config_path: Path, answers_path: Path, out: Path) -> None:
    config = load_config(config_path)
    answers = load_answers(answers_path)
    connection = await asyncpg.connect(asyncpg_dsn(database_url))
    try:
        manifest = await build_slice(connection, answers, config, out)
    finally:
        await connection.close()
    # print, а не лог: сводка и есть результат команды.
    print(f"срез: {out}")
    print(f"сезоны ({len(manifest.seasons)}): {', '.join(manifest.seasons)}")
    print(f"документы ({len(manifest.documents)}): {', '.join(manifest.documents)}")
    for table in manifest.tables:
        print(f"  {table.name:12}{table.rows:>9} строк {table.bytes / 1024:>9.0f} КБ")
    print(f"итого {manifest.total_bytes / 1024 / 1024:.2f} МБ")


def main() -> None:
    parser = argparse.ArgumentParser(prog="court_vision.eval_slice", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="выгрузить срез из полной базы")
    build.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    build.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS_PATH)
    build.add_argument("--dir", type=Path, default=DEFAULT_SLICE_DIR)

    load = commands.add_parser("load", help="залить срез в пустую базу")
    load.add_argument("--dir", type=Path, default=DEFAULT_SLICE_DIR)

    args = parser.parse_args()
    configure_logging()
    database_url = get_settings().database_url
    if args.command == "build":
        asyncio.run(
            run_build(
                database_url, config_path=args.config, answers_path=args.answers, out=args.dir
            )
        )
    else:
        asyncio.run(prepare_slice(database_url, args.dir))


if __name__ == "__main__":
    main()
