FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Слой зависимостей отдельно от кода — пересобирается только при смене lock-файла.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --from=builder --chown=app:app /app /app

# Сам WORKDIR создан root'ом: --chown выше переносит владельца содержимого, но не каталога,
# а seed'у нужно создать в нём кеш датасета.
RUN mkdir -p /app/data && chown -R app:app /app

USER app

ENV API_PORT_INTERNAL=8000

CMD ["sh", "-c", "uvicorn court_vision.main:app --host 0.0.0.0 --port ${API_PORT_INTERNAL}"]
