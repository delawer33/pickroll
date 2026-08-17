"""Доменные ошибки аутентификации.

Причина отказа в 401 (нет токена, истёк, чужая подпись) наружу не детализируется дальше
`detail`: клиенту достаточно знать, что нужен валидный токен, а подробная диагностика —
подарок для перебора. Ошибки конфигурации и недоступность IdP — отдельные типы: это
серверные проблемы, и выдавать их за «плохой токен» значило бы разлогинивать пользователей
при каждом сбое.
"""

from typing import ClassVar

from court_vision.errors import DomainError
from court_vision.problems import PROBLEM_TYPE_PREFIX


class Unauthenticated(DomainError):
    status: ClassVar[int] = 401
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}unauthenticated"
    title: ClassVar[str] = "Unauthenticated"
    # RFC 6750: 401 обязан указывать схему, иначе клиент не знает, чем аутентифицироваться.
    headers: ClassVar[dict[str, str]] = {"WWW-Authenticate": "Bearer"}


class AuthNotConfigured(DomainError):
    """`CLERK_JWKS_URL` не задан: приложение поднялось, но проверять токены нечем."""

    status: ClassVar[int] = 500
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}auth-not-configured"
    title: ClassVar[str] = "Authentication not configured"


class IdentityProviderUnavailable(DomainError):
    """JWKS недоступен (сеть, таймаут): токен не «плохой», проверить его сейчас нельзя."""

    status: ClassVar[int] = 503
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}idp-unavailable"
    title: ClassVar[str] = "Identity provider unavailable"
