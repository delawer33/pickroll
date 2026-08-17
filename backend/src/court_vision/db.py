from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий декларативный базовый класс: с него Alembic снимает метаданные."""


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Unit of work = HTTP-запрос: commit на выходе, rollback при исключении.

    Сервисы внутри запроса не коммитят — граница транзакции здесь.
    """
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped сессия. Фабрика живёт в `app.state` — её создаёт lifespan."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async for session in session_scope(session_factory):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
