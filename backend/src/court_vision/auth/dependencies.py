"""Валидация Bearer-токенов по JWKS (ADR-0002: Clerk как managed IdP).

Бэкенд — resource server: секретов IdP он не знает и доверяет подписи, проверяемой по
публичному JWKS. Источник ключей приходит из окружения (`CLERK_JWKS_URL`), поэтому в
тестах на его месте локальный стаб с собственной RSA-парой — auth-код один и тот же в
проде и в CI, dev-байпасов нет намеренно.
"""

from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from court_vision.auth.errors import (
    AuthNotConfigured,
    IdentityProviderUnavailable,
    Unauthenticated,
)

# Clerk подписывает сессии RS256; список фиксирован, чтобы исключить подмену алгоритма.
_ALGORITHMS = ["RS256"]

# Clerk-токены короткоживущие и используются сразу после выпуска, поэтому даже небольшое
# отставание наших часов от часов IdP (iat в «будущем») забраковало бы весь трафик.
# 5 секунд — допуск из официального Clerk SDK.
_CLOCK_SKEW_SECONDS = 5

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str:
    """Достаёт `clerk_user_id` (claim `sub`) из проверенного токена.

    Пользователь для системы — опаковая строка Clerk: локальной таблицы `users` нет,
    пока у пользователя не появятся собственные атрибуты.
    """
    if credentials is None:
        raise Unauthenticated("Запрос требует Bearer-токен.")

    token = credentials.credentials
    jwks_client: jwt.PyJWKClient | None = request.app.state.jwks_client
    if jwks_client is None:
        raise AuthNotConfigured("CLERK_JWKS_URL не задан — аутентификация не сконфигурирована.")
    try:
        # PyJWKClient ходит за ключами синхронно (и кеширует их) — уводим в поток,
        # чтобы не блокировать event loop.
        signing_key = await run_in_threadpool(jwks_client.get_signing_key_from_jwt, token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            leeway=_CLOCK_SKEW_SECONDS,
            # Контракт audience пока не определён (один API — один фронт); без этого флага
            # PyJWT отвергает любой токен, в шаблоне которого появится claim `aud`.
            options={"require": ["exp", "sub"], "verify_aud": False},
        )
    except jwt.exceptions.PyJWKClientConnectionError as exc:
        # Недоступность JWKS — это 503, а не 401: иначе каждый сбой IdP массово
        # «разлогинивал» бы пользователей валидными токенами.
        raise IdentityProviderUnavailable("Не удалось получить ключи подписи от IdP.") from exc
    except jwt.PyJWTError as exc:
        raise Unauthenticated("Токен не прошёл проверку.") from exc

    clerk_user_id = str(claims["sub"])
    if not clerk_user_id:
        # Пустой sub прошёл бы `require` (claim есть) и слил бы всех таких пользователей
        # в одну идентичность — ключ Вотчлиста спринта 4.
        raise Unauthenticated("Токен не прошёл проверку.")
    return clerk_user_id


CurrentUserId = Annotated[str, Depends(get_current_user_id)]
