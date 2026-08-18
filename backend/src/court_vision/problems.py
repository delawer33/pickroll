"""Единственный формат ошибок API — RFC 9457 Problem Details.

Все обработчики зарегистрированы в одном месте: и доменные ошибки, и валидация FastAPI,
и необработанные исключения выходят наружу одинаково — иначе фронт и агент разбирали бы
три разных формата ошибок.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from court_vision.errors import DomainError

log = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Идентификаторы типов ошибок — URN, а не URL: тип ошибки обязан быть стабильным
# идентификатором, а обещать по нему живую страницу документации мы не готовы.
PROBLEM_TYPE_PREFIX = "urn:court-vision:problem:"
VALIDATION_PROBLEM = f"{PROBLEM_TYPE_PREFIX}invalid-request"
INTERNAL_PROBLEM = f"{PROBLEM_TYPE_PREFIX}internal-error"


def problem_body(
    *,
    status: int,
    title: str,
    detail: str,
    instance: str,
    problem_type: str = "about:blank",
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Тело Problem Details отдельно от ответа: тот же формат ошибки уезжает и внутрь
    SSE-потока событием `error`, где ответа с кодом статуса уже нет."""
    # Расширяющие члены кладутся первыми, зарегистрированные — поверх: иначе расширение
    # с именем `status` перебило бы стандартный член, и тело противоречило бы статусной
    # строке ответа.
    return {
        **(extensions or {}),
        "type": problem_type,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }


def domain_problem(error: DomainError, *, instance: str) -> dict[str, Any]:
    """Проекция доменной ошибки на Problem Details — одна для HTTP-ответа и для SSE."""
    return problem_body(
        status=error.status,
        title=error.title,
        detail=error.detail,
        instance=instance,
        problem_type=error.problem_type,
        extensions=error.extensions(),
    )


def problem_response(
    *,
    status: int,
    title: str,
    detail: str,
    instance: str,
    problem_type: str = "about:blank",
    extensions: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = problem_body(
        status=status,
        title=title,
        detail=detail,
        instance=instance,
        problem_type=problem_type,
        extensions=extensions,
    )
    return JSONResponse(
        status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE, headers=headers
    )


class ProblemDetailsMiddleware:
    """Ловит необработанные исключения внутри CORS-слоя.

    Обработчик, зарегистрированный на `Exception`, Starlette переносит в
    ServerErrorMiddleware — самый внешний слой, снаружи CORSMiddleware. Ответ оттуда уходит
    без `Access-Control-Allow-Origin`, и браузер вместо наших Problem Details видит сетевую
    ошибку — ровно тогда, когда диагностика нужнее всего.

    Сырой ASGI, а не BaseHTTPMiddleware: последний ломает стриминг, а в спринте 1 приезжает
    SSE.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            path = str(scope.get("path", ""))
            log.exception("request.unhandled_error", path=path)
            if response_started:
                # Заголовки уже ушли к клиенту — подменить ответ нечем, пусть падает наружу.
                raise
            response = problem_response(
                status=500,
                title="Internal server error",
                detail="Внутренняя ошибка сервера.",
                instance=path,
                problem_type=INTERNAL_PROBLEM,
            )
            await response(scope, receive, send)


def install_problem_handlers(app: FastAPI) -> None:
    """Вызывать до `add_middleware(CORSMiddleware)`: Starlette делает самым внешним слой,
    добавленный последним, а наш обработчик обязан оказаться внутри CORS."""
    app.add_middleware(ProblemDetailsMiddleware)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    # Подстраховка на исключения, брошенные снаружи ProblemDetailsMiddleware, — например
    # в самом CORS-слое. Формат ответа тот же, но CORS-заголовков на нём уже не будет.
    app.add_exception_handler(Exception, _unhandled_handler)


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(
        status_code=exc.status,
        content=domain_problem(exc, instance=request.url.path),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=exc.headers or None,
    )


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return problem_response(
        status=422,
        title="Invalid request",
        detail="Параметры запроса не прошли валидацию.",
        instance=request.url.path,
        problem_type=VALIDATION_PROBLEM,
        # `jsonable_encoder` не нужен: ошибки pydantic содержат исходное значение, которое
        # не всегда сериализуемо, поэтому оставляем только адрес и причину.
        extensions={
            "errors": [
                {"location": list(error["loc"]), "message": error["msg"], "kind": error["type"]}
                for error in exc.errors()
            ]
        },
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return problem_response(
        status=exc.status_code,
        title=_http_title(exc.status_code),
        detail=str(exc.detail),
        instance=request.url.path,
        # Часть статусов без своих заголовков не имеет смысла: 405 обязан нести `Allow`,
        # 429 — `Retry-After`. Стандартный обработчик Starlette их отдаёт, и подменяя его,
        # мы обязаны их сохранить.
        headers=exc.headers or None,
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Наружу — ровное 500 без внутренностей; подробности уходят в лог с трейсом."""
    log.exception("request.unhandled_error", path=request.url.path)
    return problem_response(
        status=500,
        title="Internal server error",
        detail="Внутренняя ошибка сервера.",
        instance=request.url.path,
        problem_type=INTERNAL_PROBLEM,
    )


def _http_title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"
