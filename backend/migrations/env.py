import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import court_vision.models  # noqa: F401  — импорт ради регистрации таблиц в метаданных
from court_vision.config import get_settings
from court_vision.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL берётся из окружения (12-factor), а не из alembic.ini. Если вызывающая сторона
# уже задала его явно (тесты с testcontainers), он имеет приоритет.
if not config.get_main_option("sqlalchemy.url", default=""):
    # Экранирование `%`: значение проходит через интерполяцию configparser, и URL-кодированный
    # спецсимвол в пароле (`%40`) иначе роняет alembic до подключения к БД.
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
