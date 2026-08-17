import json
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env лежит в корне монорепо и общий для Compose и бэкенда. Путь абсолютный: относительный
# зависел бы от рабочей директории, а бэкенд запускают и из корня, и из backend/.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Конфигурация приложения. 12-factor: всё приезжает из окружения."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "court-vision"
    environment: str = "local"

    database_url: str = "postgresql+asyncpg://court_vision:court_vision@localhost:5432/court_vision"

    cors_origins: list[str] = ["http://localhost:5173"]

    # JWKS инстанса Clerk: `https://<frontend-api>/.well-known/jwks.json`. Источник ключей
    # конфигурируем намеренно — тесты подставляют сюда локальный стаб и подписывают токены
    # собственной RSA-парой, поэтому auth-код в CI и в проде один и тот же.
    clerk_jwks_url: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Принимаем и JSON-массив, и привычную для 12-factor запятую."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        if isinstance(value, str):
            return json.loads(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
