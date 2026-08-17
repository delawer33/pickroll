"""Интеграционные тесты аутентификации: JWKS-валидация токенов на границе `/api/v1`.

Токены подписываются собственной RSA-парой из conftest, JWKS раздаётся локальным
HTTP-сервером — живой Clerk в тестах не участвует, при этом проверяется настоящий
auth-код без каких-либо dev-байпасов.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI

from conftest import SigningKey, TokenFactory
from court_vision.config import Settings
from court_vision.main import create_app

ME_URL = "/api/v1/me"
PROBLEM_CONTENT_TYPE = "application/problem+json"
UNAUTHENTICATED_PROBLEM = "urn:court-vision:problem:unauthenticated"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_без_токена_401_в_формате_problem_details(
    anonymous_client: httpx.AsyncClient,
) -> None:
    response = await anonymous_client.get(ME_URL)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    # RFC 6750: 401 обязан называть схему аутентификации.
    assert response.headers["www-authenticate"] == "Bearer"
    problem = response.json()
    assert problem["type"] == UNAUTHENTICATED_PROBLEM
    assert problem["status"] == 401


async def test_валидный_токен_даёт_clerk_user_id_обработчику(
    anonymous_client: httpx.AsyncClient, make_token: TokenFactory
) -> None:
    token = make_token(sub="user_2abcDEF")

    response = await anonymous_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"clerk_user_id": "user_2abcDEF"}


async def test_истёкший_токен_отклоняется(
    anonymous_client: httpx.AsyncClient, make_token: TokenFactory
) -> None:
    token = make_token(expires_in=timedelta(minutes=-5))

    response = await anonymous_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["type"] == UNAUTHENTICATED_PROBLEM


async def test_токен_подписанный_чужим_ключом_отклоняется(
    anonymous_client: httpx.AsyncClient,
    make_token: TokenFactory,
    foreign_signing_key: SigningKey,
) -> None:
    token = make_token(key=foreign_signing_key)

    response = await anonymous_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["type"] == UNAUTHENTICATED_PROBLEM


async def test_мусор_вместо_токена_отклоняется(anonymous_client: httpx.AsyncClient) -> None:
    response = await anonymous_client.get(ME_URL, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.json()["type"] == UNAUTHENTICATED_PROBLEM


async def test_пустой_sub_отклоняется(
    anonymous_client: httpx.AsyncClient, make_token: TokenFactory
) -> None:
    # Claim присутствует, поэтому `require` его пропускает; пустая строка слила бы всех
    # таких пользователей в одну идентичность.
    token = make_token(sub="")

    response = await anonymous_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_не_bearer_схема_отклоняется(anonymous_client: httpx.AsyncClient) -> None:
    response = await anonymous_client.get(ME_URL, headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code == 401


async def test_подмена_алгоритма_на_hs256_отклоняется(
    anonymous_client: httpx.AsyncClient, signing_key: SigningKey
) -> None:
    # Классическая alg-confusion: токен подписан HMAC'ом, где секрет — публичный ключ
    # из JWKS. Если бы список алгоритмов не был зажат до RS256, подпись бы сошлась.
    public_pem = signing_key.private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    token = _hs256_token(secret=public_pem, kid=signing_key.kid)

    response = await anonymous_client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_без_clerk_jwks_url_это_500_misconfiguration(make_token: TokenFactory) -> None:
    # Ошибка конфигурации — не «плохой токен»: валидный токен при незаданном JWKS-URL
    # обязан дать 500 с внятным типом, а не 401.
    app = create_app(Settings(clerk_jwks_url=""))

    async with _client(app) as client:
        response = await client.get(ME_URL, headers={"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 500
    assert response.json()["type"] == "urn:court-vision:problem:auth-not-configured"


async def test_недоступный_jwks_это_503_а_не_401(make_token: TokenFactory) -> None:
    # Сбой IdP не должен маскироваться под невалидный токен: 401 массово разлогинивал бы
    # пользователей при каждом сетевом сбое.
    app = create_app(Settings(clerk_jwks_url="http://127.0.0.1:9/jwks.json"))

    async with _client(app) as client:
        response = await client.get(ME_URL, headers={"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 503
    assert response.json()["type"] == "urn:court-vision:problem:idp-unavailable"


def _hs256_token(*, secret: bytes, kid: str) -> str:
    """Токен HS256, собранный руками: PyJWT сам отказывается подписывать HMAC'ом
    материал, похожий на публичный ключ."""

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    expires_at = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    payload = b64url(json.dumps({"sub": "user_test", "exp": expires_at}).encode())
    signature = b64url(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


async def test_весь_api_v1_закрыт_токеном(anonymous_client: httpx.AsyncClient) -> None:
    # Auth навешан на общий версионированный роутер, а не на отдельные модули: любой
    # существующий эндпоинт без токена обязан отвечать 401 ещё до своей логики.
    response = await anonymous_client.get(
        "/api/v1/players/stats", params={"player_name": "LeBron James"}
    )

    assert response.status_code == 401


async def test_health_доступен_без_токена(anonymous_client: httpx.AsyncClient) -> None:
    response = await anonymous_client.get("/health")

    assert response.status_code == 200
