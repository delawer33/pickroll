"""Единственный формат ошибок API — RFC 9457 Problem Details.

Все обработчики зарегистрированы в одном месте: и доменные ошибки, и валидация FastAPI,
и необработанные исключения выходят наружу одинаково — иначе фронт и агент разбирали бы
три разных формата ошибок.
"""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from court_vision.errors import DomainError

log = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Идентификаторы типов ошибок — URN, а не URL: тип ошибки обязан быть стабильным
# идентификатором, а обещать по нему живую страницу документации мы не готовы.
PROBLEM_TYPE_PREFIX = "urn:court-vision:problem:"
VALIDATION_PROBLEM = f"{PROBLEM_TYPE_PREFIX}invalid-request"
INTERNAL_PROBLEM = f"{PROBLEM_TYPE_PREFIX}internal-error"


def problem_response(
    *,
    status: int,
    title: str,
    detail: str,
    instance: str,
    problem_type: str = "about:blank",
    extensions: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": problem_type,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    if extensions:
        body.update(extensions)
    return JSONResponse(
        status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE, headers=headers
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_handler)


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return problem_response(
        status=exc.status,
        title=exc.title,
        detail=exc.detail,
        instance=request.url.path,
        problem_type=exc.problem_type,
        extensions=exc.extensions(),
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
