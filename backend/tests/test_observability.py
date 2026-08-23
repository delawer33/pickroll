"""Трейсинг включается ключами и только ими.

Смысл проверки — не в самом Langfuse, а в том, что его отсутствие ничего не ломает: без
ключей локальная разработка обязана работать как обычно, а не спотыкаться об экспорт
трейсов в никуда.
"""

from __future__ import annotations

import litellm
import pytest

from court_vision.config import Settings
from court_vision.observability import configure_llm_observability

CALLBACK = "langfuse_otel"

WITH_KEYS = Settings(langfuse_public_key="pk-test", langfuse_secret_key="sk-test")
WITHOUT_KEYS = Settings(langfuse_public_key="", langfuse_secret_key="")


@pytest.fixture(autouse=True)
def _isolate_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Списки колбэков и окружение глобальны для процесса — правки не должны утекать в
    остальные тесты."""
    monkeypatch.setattr(litellm, "success_callback", [])
    monkeypatch.setattr(litellm, "failure_callback", [])
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


def test_без_ключей_трейсинг_не_включается() -> None:
    configure_llm_observability(WITHOUT_KEYS)

    assert litellm.success_callback == []
    assert litellm.failure_callback == []


def test_с_ключами_регистрируются_и_успех_и_отказ() -> None:
    """Отказ провайдера — самое интересное, что бывает в трейсе; на одном успехе его не видно."""
    configure_llm_observability(WITH_KEYS)

    assert CALLBACK in litellm.success_callback
    assert CALLBACK in litellm.failure_callback


def test_повторная_настройка_не_плодит_дубли() -> None:
    """`create_app` вызывается больше одного раза на процесс — в тестах и в воркерах."""
    configure_llm_observability(WITH_KEYS)
    configure_llm_observability(WITH_KEYS)

    assert litellm.success_callback.count(CALLBACK) == 1


def test_пропавшие_ключи_снимают_трейсинг() -> None:
    """Иначе приложение, поднятое без ключей, продолжало бы экспортировать вызовы по ключам,
    оставленным предыдущим."""
    configure_llm_observability(WITH_KEYS)

    configure_llm_observability(WITHOUT_KEYS)

    assert CALLBACK not in litellm.success_callback
    assert CALLBACK not in litellm.failure_callback
