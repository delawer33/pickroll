"""Трейсинг агентного цикла в Langfuse — через колбэк litellm.

Колбэк регистрируется только при заданных ключах. Без них система работает как обычно: у
локальной разработки не должно быть внешней зависимости, которая либо требует ключей, либо
шумит ошибками экспорта на каждый вопрос.

Свою таблицу `llm_usage` это не заменяет (см. `agent/models.py`): Langfuse — инструмент
отладки с собственным сроком хранения, продуктовые агрегаты считаются по своим данным.
"""

import os
from typing import Any

import litellm
import structlog

from court_vision.config import Settings

log = structlog.get_logger(__name__)

# `langfuse_otel`, а не `langfuse`: трейсы уходят по OTLP, минуя SDK Langfuse — тот в
# третьей мажорной версии сменил API, и колбэк `langfuse` работает только со второй.
_CALLBACK = "langfuse_otel"


def configure_llm_observability(settings: Settings) -> None:
    """Включает трейсинг, если ключи есть, и выключает, если их нет.

    Симметрично намеренно: списки колбэков и окружение глобальны для процесса, и без
    снятия приложение, поднятое без ключей, продолжало бы экспортировать вызовы по ключам,
    оставленным предыдущим.
    """
    enabled = bool(settings.langfuse_public_key and settings.langfuse_secret_key)
    if enabled:
        # Колбэк читает ключи из окружения сам, а Settings умеет брать их и из `.env`-файла —
        # то есть в процессе они могут быть, а в `os.environ` нет. Кладём явно, иначе
        # трейсинг включён по настройкам и выключен по факту.
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        if settings.langfuse_host:
            os.environ["LANGFUSE_HOST"] = settings.langfuse_host

    # Оба списка: неудавшийся вызов провайдера — самое интересное, что бывает в трейсе, и
    # именно его не видно, если зарегистрировать только успех.
    _apply(litellm.success_callback, enabled=enabled)
    _apply(litellm.failure_callback, enabled=enabled)
    log.info(
        "observability.langfuse_enabled" if enabled else "observability.langfuse_disabled",
        host=settings.langfuse_host or "cloud",
    )


def _apply(callbacks: list[Any], *, enabled: bool) -> None:
    """Идемпотентно: `create_app` вызывается и в тестах, а списки колбэков глобальны для
    процесса — без проверки один и тот же колбэк накапливался бы дублями."""
    if enabled and _CALLBACK not in callbacks:
        callbacks.append(_CALLBACK)
    elif not enabled:
        while _CALLBACK in callbacks:
            callbacks.remove(_CALLBACK)
