# Court Vision

AI-ассистент баскетбольного скаута: отвечает на вопросы об NBA на естественном языке, считая цифры
из реальных box scores, а не из памяти модели.

Продуктовая спека — [spec.md](spec.md), глоссарий — [CONTEXT.md](CONTEXT.md), архитектура —
[docs/architecture.md](docs/architecture.md), конвенции кода — [docs/conventions.md](docs/conventions.md),
визуальный язык — [docs/design-system.md](docs/design-system.md).

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

## Демо-сценарий

Сквозной путь: вопрос на естественном языке → стриминговый ответ с цифрами из Postgres.

1. **Ключи.** `cp .env.example .env` и заполнить: `CLERK_JWKS_URL` и
   `VITE_CLERK_PUBLISHABLE_KEY` (Clerk Dashboard), `LLM_API_KEY` — без него агент отвечать не
   сможет. `LANGFUSE_*` необязательны: без них трейсинг просто не включается.
2. **Поднять систему.** `docker compose up --build` — Postgres, Redis, API, фронтенд.
3. **Схема и данные.**

   ```bash
   docker compose exec api alembic upgrade head
   docker compose exec api python -m court_vision.seed
   ```

   Полный seed качает датасет Kaggle (~29 тыс. матчей, ~650 тыс. строк Box Score) — это
   несколько минут. Для быстрого прогона хватит тестовой фикстуры:
   `docker compose exec api python -m court_vision.seed --source-dir tests/fixtures/dataset`.
4. **Войти.** http://localhost:5173 → Clerk. Всё под `/api/v1` требует токена, Диалог
   принадлежит вошедшему пользователю: чужой не откроется даже по прямой ссылке.
5. **Спросить.** На пустом экране — предложенные вопросы, например «How many points did
   LeBron James average in 2012-13?». В ответе: карточка вызванного Инструмента с
   аргументами в рейке Evidence справа, затем текст, приезжающий токенами.
6. **Проверить цифры.** 26.8 очка за игру — это агрегат по строкам Box Score, а не память
   модели: тот же ответ отдаёт `GET /api/v1/players/stats?player_name=LeBron%20James&season=2012-13`.
7. **Перезагрузить страницу.** Диалог на месте: он в истории в левой рейке вместе с
   заголовком из первого вопроса. Follow-up «А в 2015-16?» работает без имени игрока —
   агент получает переписку целиком.

Если ключа провайдера нет или он не отвечает, поток не обрывается: в ленте появляется
состояние ошибки с причиной и кнопкой повтора.

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
нужны. Сырые CSV кладутся в `backend/data/raw/` (в контейнере — в volume `dataset_cache`) и в
git не попадают.

Сезон матча берётся из его идентификатора, а не из даты: сезон 2019-20 доигрывался в «пузыре»
до октября 2020, и по дате эти матчи неотличимы от начала следующего сезона.

Маленькая фикстура того же формата лежит в `backend/tests/fixtures/dataset/` и грузится тем же
кодом — на ней работают интеграционные тесты:

```bash
cd backend && uv run python -m court_vision.seed --source-dir tests/fixtures/dataset
```

## LLM

Провайдер задаётся строкой модели litellm в `LLM_MODEL` (`anthropic/claude-haiku-4-5`,
`gemini/...`, `groq/...`) плюс `LLM_API_KEY` — смена провайдера не требует правок кода. Модель
за OpenAI-совместимым шлюзом (свой прокси, OpenRouter, корпоративный шлюз) — это `openai/` в
`LLM_MODEL` и адрес шлюза в `LLM_API_BASE`; учёт токенов при этом работает, стоимость — нет
(прайслиста шлюза у litellm нет, см. [deferred.md](docs/deferred.md)).
По [ADR-0001](docs/adr/0001-litellm-transport-own-agent-loop.md)
litellm — только транспорт: агентный цикл, Инструменты и учёт стоимости написаны в
`backend/src/court_vision/agent/`.

Промпты лежат файлами в `backend/src/court_vision/agent/prompts/` — правка формулировки видна
в диффе PR, а не прячется в коде.

Каждый вызов модели пишет строку в таблицу `llm_usage`: токены, стоимость, латентность.

Ответ приезжает потоком: `POST /api/v1/conversations/{id}/messages` отдаёт SSE, в `data`
каждого события — объект с полем `type` (`token`, `tool_call`, `citation`, `done`, `error`).
Типы событий описаны моделями в OpenAPI, TS-типы фронтенда генерируются из него. Ошибка
провайдера приходит событием `error` с телом RFC 9457, а не обрывом соединения.

Трейсы агентного цикла уходят в Langfuse, если заданы `LANGFUSE_PUBLIC_KEY` и
`LANGFUSE_SECRET_KEY`; без них трейсинг не включается и локальная разработка не требует
внешнего сервиса. Ходы одного Диалога собираются в Langfuse в одну сессию.

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

TS-типы контракта API генерируются из OpenAPI и лежат в git
(`frontend/src/shared/api/schema.d.ts`) — руками они не пишутся. После правки схем на
бэкенде:

```bash
cd frontend && pnpm api:types
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
