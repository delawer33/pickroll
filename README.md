# Court Vision

AI-ассистент баскетбольного скаута: отвечает на вопросы об NBA, а цифры берёт из box scores в
Postgres, не из памяти модели.

- Отвечает на вопросы о статистике, биографии и правилах, с цитатами из Wikipedia и rulebook.
- Показывает Перцентили игрока среди Квалифицированных игроков сезона.
- Сравнивает до четырёх игроков на radar-чарте.
- Ищет Похожих игроков по Статистическому отпечатку сезона.
- Пишет Скаутинг-отчёты, сохраняет их и находит прошлые отчёты.
- Качество ответов проверяет eval на Золотом наборе с LLM-судьёй.

Документы: [spec.md](spec.md) (продукт), [CONTEXT.md](CONTEXT.md) (глоссарий),
[docs/architecture.md](docs/architecture.md), [docs/conventions.md](docs/conventions.md),
[docs/design-system.md](docs/design-system.md), [docs/evals.md](docs/evals.md).

## Запуск

Нужны Docker и Docker Compose. uv, Node 22 и pnpm 10 нужны только для работы вне контейнеров.

```bash
cp .env.example .env
docker compose up --build
docker compose exec api alembic upgrade head
docker compose exec api python -m court_vision.seed
docker compose exec api python -m court_vision.corpus
```

В `.env` заполни `CLERK_JWKS_URL` и `VITE_CLERK_PUBLISHABLE_KEY` из Clerk Dashboard и
`LLM_API_KEY`: без него агент не ответит. `LANGFUSE_*` необязательны.

Миграции контейнер API сам не применяет. Полный seed качает датасет Kaggle и идёт несколько
минут. Для быстрого старта хватит фикстуры:
`docker compose exec api python -m court_vision.seed --source-dir tests/fixtures/dataset`.

API: http://localhost:8000 (`GET /health`, OpenAPI на `/docs`). Фронтенд: http://localhost:5173.

## Демо

1. Войди на http://localhost:5173 через Clerk. Всё под `/api/v1` требует токена, чужой Диалог не
   откроется даже по прямой ссылке.
2. Спроси «How many points did LeBron James average in 2012-13?». Справа в рейке Evidence
   появится карточка Инструмента с аргументами, затем ответ потоком.
3. Сверь цифру: 26.8 очка за игру — агрегат по строкам Box Score. То же отдаёт
   `GET /api/v1/players/stats?player_name=LeBron%20James&season=2012-13`.
4. Перезагрузи страницу. Диалог остаётся в истории слева, с заголовком из первого вопроса.
   Follow-up «А в 2015-16?» работает без имени игрока: агент получает всю переписку.

Если провайдер LLM не отвечает, поток не обрывается: в ленте появляется ошибка с причиной и
кнопкой повтора.

## Данные

Датасет: [eoinamoore/historical-nba-data-and-player-box-scores](https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores),
сезоны с 1946-47, около 1.56 млн строк Box Score. Датасет обновляется каждую ночь, поэтому
версия запинена в `court_vision/seed/source.py`. Учётные данные Kaggle не нужны.

Seed идемпотентен, повторный запуск безопасен. Сырые CSV лежат в `backend/data/raw/` (в
контейнере — volume `dataset_cache`) и в git не попадают.

Сезон матча берётся из его id, а не из даты. Сезон 2019-20 доигрывали в «пузыре» до октября
2020, и по дате эти матчи не отличить от следующего сезона.

После загрузки seed пересобирает Статистические отпечатки (таблица `player_fingerprints`, по
ней работает `find_similar_players`). Отдельно это занимает около 10 секунд:

```bash
docker compose exec api python -m court_vision.players.rebuild_fingerprints
```

Корпус (Wikipedia и rulebook) индексирует `court_vision.corpus`. Повторный запуск продолжает с
места остановки, `--reindex` пересчитывает всё.

Интеграционные тесты работают на фикстуре того же формата в `backend/tests/fixtures/dataset/`.

## LLM

Провайдера задаёт строка модели litellm в `LLM_MODEL` (`anthropic/claude-haiku-4-5`,
`gemini/...`, `groq/...`) и `LLM_API_KEY`. Код при смене провайдера не меняется.

Для OpenAI-совместимого шлюза (свой прокси, OpenRouter, корпоративный) добавь `openai/` в
`LLM_MODEL` и адрес шлюза в `LLM_API_BASE`. Токены считаются, стоимость нет: прайслиста шлюза у
litellm нет (см. [deferred.md](docs/deferred.md)).

litellm здесь только транспорт ([ADR-0001](docs/adr/0001-litellm-transport-own-agent-loop.md)).
Агентный цикл, Инструменты и учёт стоимости написаны в `backend/src/court_vision/agent/`.
Промпты лежат файлами в `agent/prompts/`, поэтому их правка видна в диффе PR.

Каждый вызов модели пишет в таблицу `llm_usage` токены, стоимость и латентность. Трейсы уходят в
Langfuse, если заданы `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY`. Ходы одного Диалога
собираются в одну сессию.

## API

`POST /api/v1/conversations/{id}/messages` отдаёт SSE. В `data` каждого события объект с полем
`type`: `token`, `tool_call`, `citation`, `done` или `error`. Ошибка провайдера приходит событием
`error` с телом RFC 9457.

Скаутинг-отчёты ([ADR-0006](docs/adr/0006-scouting-report-generation.md)):

- `POST /api/v1/reports` (`{player_name, season?, language?}`) отдаёт SSE: `snapshot` с
  частичным отчётом, затем `done` с `report_id` или `error`.
- `GET /api/v1/reports` — свои отчёты, с курсором и фильтром `player_id`.
- `GET /api/v1/reports/{id}` — чужой отчёт отдаёт 404.

В чате то же делают Инструменты `create_scouting_report` и `search_reports`.

Невалидный с первого раза отчёт генерируется повторно. Их долю видно в `llm_usage`: операции
`reports.generate_retry` среди `reports.generate`. Исход каждой попытки пишется в лог событием
`reports.validation`.

Типы событий описаны в OpenAPI. TS-типы фронтенда генерируются из него и лежат в
`frontend/src/shared/api/schema.d.ts`. После правки схем на бэкенде: `cd frontend && pnpm api:types`.

## Разработка

```bash
cd backend
uv sync
uv run uvicorn court_vision.main:app --reload
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

```bash
cd frontend
pnpm install
pnpm dev
pnpm lint && pnpm typecheck && pnpm test
```

Хуки pre-commit используют те же конфиги, что CI:

```bash
uv run --project backend pre-commit install
```

Eval ответов, Eval-срез и ручной прогон в CI описаны в [docs/evals.md](docs/evals.md).

## Процесс

- Trunk-based: короткие ветки, PR в `main`, задачи в GitHub Issues, PR ссылается на issue.
- CI обязан быть зелёным: ruff, mypy --strict и pytest на бэкенде; eslint, tsc и vitest на
  фронтенде. Джобы включаются по путям: правка фронтенда не гоняет бэкенд.
- Conventional commits, например `feat(players): фаззи-резолюция имени игрока через pg_trgm`.

Вся конфигурация через окружение. В git лежит только [.env.example](.env.example).
