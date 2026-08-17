from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from jwt import PyJWKClient

from court_vision.auth.dependencies import get_current_user_id
from court_vision.auth.router import router as auth_router
from court_vision.config import Settings, get_settings
from court_vision.db import create_engine, create_session_factory
from court_vision.health.router import router as health_router
from court_vision.logging import configure_logging
from court_vision.players.router import router as players_router
from court_vision.problems import install_problem_handlers

API_V1_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    app = FastAPI(title="Court Vision API", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    # Не в lifespan: клиент не ходит в сеть при создании (ключи скачиваются лениво и
    # кешируются), а тестовый ASGITransport lifespan не проигрывает. Без URL клиента нет:
    # приложение поднимается (health, docs), а запрос под auth упадёт как misconfiguration.
    # Таймаут короткий: дефолтные 30 секунд на поход за JWKS держали бы слот тредпула
    # на каждый токен с неизвестным kid.
    app.state.jwks_client = (
        PyJWKClient(settings.clerk_jwks_url, cache_keys=True, timeout=5)
        if settings.clerk_jwks_url
        else None
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_problem_handlers(app)

    app.include_router(health_router)
    app.include_router(_api_v1_router())

    return app


def _api_v1_router() -> APIRouter:
    """Все версионированные роутеры собираются в один: общие для `/api/v1` зависимости
    (в первую очередь auth) навешиваются здесь, а не в каждом модуле по отдельности."""
    router = APIRouter(prefix=API_V1_PREFIX, dependencies=[Depends(get_current_user_id)])
    router.include_router(auth_router)
    router.include_router(players_router)
    return router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Движок — объект уровня приложения: один пул соединений на процесс."""
    settings: Settings = app.state.settings
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        await engine.dispose()


app = create_app()
