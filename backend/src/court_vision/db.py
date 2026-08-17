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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped сессия; фабрика живёт в `app.state` — её создаёт lifespan.

    Unit of work = HTTP-запрос: commit на выходе, rollback при исключении. Сервисы внутри
    запроса не коммитят — граница транзакции здесь.

    Транзакция закрывается ровно в этой функции, без промежуточного генератора: FastAPI
    бросает исключение обратно в точку `yield`, и делегирование через `async for` эту
    ошибку до вложенного генератора не донесло бы — тот остался бы висеть на своём `yield`,
    а соединение вернулось бы в пул в состоянии `idle in transaction`.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
