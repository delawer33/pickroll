# Court Vision

AI-ассистент баскетбольного скаута: отвечает на вопросы об NBA на естественном языке, считая цифры
из реальных box scores, а не из памяти модели.

Продуктовая спека — [spec.md](spec.md), глоссарий — [CONTEXT.md](CONTEXT.md), архитектура —
[docs/architecture.md](docs/architecture.md), конвенции кода — [docs/conventions.md](docs/conventions.md).

## Состав репозитория

```
backend/    FastAPI + SQLAlchemy 2.0 async, зависимости через uv
frontend/   Vite + React + TypeScript, зависимости через pnpm
docs/       архитектура, конвенции, ADR, спринты
docker-compose.yml   Postgres+pgvector, Redis, API, frontend
```

## Требования

- Docker и Docker Compose
- [uv](https://docs.astral.sh/uv/) — для локальной работы с бэкендом вне контейнера
- Node 22 и pnpm 10 — для локальной работы с фронтендом вне контейнера

## Запуск

```bash
cp .env.example .env
docker compose up --build
```

- API — http://localhost:8000 (health-проба: `GET /health`, OpenAPI: `/docs`)
- Frontend — http://localhost:5173

## Данные

Схема ведётся Alembic-миграциями; контейнер API их не применяет автоматически:

```bash
docker compose exec api alembic upgrade head
```

Наполнение историческими данными (сезоны с 2005-06, ~29 тыс. матчей и ~650 тыс. строк
Box Score) — идемпотентный one-off модуль. Повторный запуск безопасен:

```bash
docker compose exec api python -m court_vision.seed
```

Датасет — [eoinamoore/historical-nba-data-and-player-box-scores](https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores),
версия запинена в коде (`court_vision/seed/source.py`): датасет обновляется еженощно, а seed
обязан быть воспроизводим. Скачивание идёт публичным эндпоинтом Kaggle — учётные данные не
нужны. Сырые CSV кладутся в `backend/data/raw/` и в git не попадают.

Маленькая фикстура того же формата лежит в `backend/tests/fixtures/dataset/` и грузится тем же
кодом — на ней работают интеграционные тесты:

```bash
cd backend && uv run python -m court_vision.seed --source-dir tests/fixtures/dataset
```

## Разработка

Бэкенд:

```bash
cd backend
uv sync
uv run uvicorn court_vision.main:app --reload
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Фронтенд:

```bash
cd frontend
pnpm install
pnpm dev
pnpm lint && pnpm typecheck && pnpm test
```

Хуки pre-commit (используют те же конфиги, что и CI):

```bash
uv run --project backend pre-commit install
```

## Процесс

- **Trunk-based**: короткоживущие ветки → PR в `main`. Прямые пуши в `main` не практикуются.
- **Зелёный CI обязателен**: ruff, mypy --strict, pytest на бэкенде; eslint, tsc --noEmit, vitest
  на фронтенде. Джобы включаются path-фильтрами — правка только фронтенда не гоняет бэкенд.
- **Conventional commits**: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.
  Пример: `feat(players): фаззи-резолюция имени игрока через pg_trgm`.
- Задачи ведутся GitHub Issues; PR ссылается на issue.

## Конфигурация

Всё через окружение (12-factor). В git лежит только [.env.example](.env.example); реальный `.env`
игнорируется. Сырые CSV датасета в git не коммитятся — в репозитории живёт только маленькая
тестовая фикстура.
