# Court Vision

AI-ассистент баскетбольного скаута. Отвечает на вопросы об NBA и показывает, откуда взял каждую цифру.

Языковые модели уверенно отвечают про статистику и так же уверенно ошибаются. Court Vision устроен иначе: цифры приходят из box scores в Postgres, а не из памяти модели. Агент сам решает, какой инструмент вызвать, и каждый вызов виден в интерфейсе рядом с ответом. Если ответ про 26.8 очка за игру, можно открыть карточку и увидеть запрос, который их посчитал.

<!-- TODO: скриншот чата с рейкой Evidence -->

## Что умеет

- **Отвечает на вопросы** о статистике, биографии и правилах. Цитаты приходят из Wikipedia и rulebook, цифры из базы.
- **Считает перцентили** игрока среди квалифицированных игроков сезона.
- **Сравнивает до четырёх игроков** на radar-чарте.
- **Ищет похожих игроков** по статистическому отпечатку сезона.
- **Пишет скаутинг-отчёты**, сохраняет их и находит прошлые.
- **Проверяет сам себя:** eval на золотом наборе вопросов с LLM-судьёй.

Термины вроде «квалифицированный игрок» и «статистический отпечаток» определены в [CONTEXT.md](CONTEXT.md).

## Запуск

Нужны Docker и Docker Compose. uv, Node 22 и pnpm 10 нужны только для работы вне контейнеров.

```bash
cp .env.example .env
docker compose up --build
docker compose exec api alembic upgrade head
docker compose exec api python -m court_vision.seed
docker compose exec api python -m court_vision.corpus
```

> [!IMPORTANT]
> Перед `up` заполни в `.env` три переменные: `CLERK_JWKS_URL` и `VITE_CLERK_PUBLISHABLE_KEY` из Clerk Dashboard и `LLM_API_KEY`. Без ключа LLM агент не ответит. `LANGFUSE_*` необязательны.

> [!NOTE]
> Миграции контейнер API сам не применяет, поэтому `alembic upgrade head` обязателен. Полный seed качает датасет Kaggle и идёт несколько минут. Для быстрого старта хватит фикстуры:
> `docker compose exec api python -m court_vision.seed --source-dir tests/fixtures/dataset`

После этого API на http://localhost:8000 (`GET /health`, OpenAPI на `/docs`), фронтенд на http://localhost:5173.

## Первые пять минут

1. Войди на http://localhost:5173 через Clerk. Всё под `/api/v1` требует токена: чужой диалог не откроется даже по прямой ссылке.
2. Спроси «How many points did LeBron James average in 2012-13?». Справа в рейке Evidence появится карточка инструмента с аргументами, затем ответ потоком.
3. Сверь цифру. 26.8 очка за игру — агрегат по строкам box score. То же отдаёт `GET /api/v1/players/stats?player_name=LeBron%20James&season=2012-13`.
4. Перезагрузи страницу. Диалог остался в истории слева, с заголовком из первого вопроса. Спроси «А в 2015-16?» без имени игрока: агент получает всю переписку и поймёт, о ком речь.

Если провайдер LLM не отвечает, поток не обрывается. В ленте появляется ошибка с причиной и кнопкой повтора.

## Как устроено

```
вопрос ──▶ агентный цикл ──▶ инструменты ──▶ Postgres (box scores, отпечатки)
   ▲            │                  └────────▶ корпус (Wikipedia, rulebook)
   │            ▼
 браузер ◀── SSE: token / tool_call / citation / done
```

Агентный цикл, инструменты и учёт стоимости написаны руками в `backend/src/court_vision/agent/`. litellm здесь только транспорт к провайдеру ([ADR-0001](docs/adr/0001-litellm-transport-own-agent-loop.md)). Промпты лежат файлами в `agent/prompts/`, поэтому их правка видна в диффе PR.

Подробнее: [docs/architecture.md](docs/architecture.md).

### Откуда данные

Датасет [eoinamoore/historical-nba-data-and-player-box-scores](https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores): сезоны с 1946-47, около 1.56 млн строк box score. Учётные данные Kaggle не нужны.

- **Версия датасета запинена** в `court_vision/seed/source.py`, потому что на Kaggle он обновляется каждую ночь.
- **Seed идемпотентен.** Повторный запуск безопасен. Сырые CSV лежат в `backend/data/raw/` (в контейнере это volume `dataset_cache`) и в git не попадают.
- **Сезон берётся из id матча, а не из даты.** Сезон 2019-20 доигрывали в «пузыре» до октября 2020, и по дате эти матчи не отличить от следующего сезона.
- **Отпечатки игроков** (таблица `player_fingerprints`, по ней работает `find_similar_players`) seed пересобирает после загрузки. Отдельно это занимает около 10 секунд: `docker compose exec api python -m court_vision.players.rebuild_fingerprints`.
- **Корпус** из Wikipedia и rulebook индексирует `court_vision.corpus`. Повторный запуск продолжает с места остановки, `--reindex` пересчитывает всё.
- **Интеграционные тесты** работают на фикстуре того же формата в `backend/tests/fixtures/dataset/`.

### Какая модель отвечает

Провайдера задаёт строка модели litellm в `LLM_MODEL` и ключ в `LLM_API_KEY`. Код при смене провайдера не меняется.

- **Anthropic, Gemini, Groq:** `anthropic/claude-haiku-4-5`, `gemini/...`, `groq/...`.
- **OpenAI-совместимый шлюз** (свой прокси, OpenRouter, корпоративный): префикс `openai/` в `LLM_MODEL` и адрес шлюза в `LLM_API_BASE`. Токены считаются, стоимость нет: прайслиста шлюза у litellm нет (см. [deferred.md](docs/deferred.md)).
- **Учёт.** Каждый вызов модели пишет в таблицу `llm_usage` токены, стоимость и латентность. Трейсы уходят в Langfuse, если заданы `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY`; ходы одного диалога собираются в одну сессию.

### API

Чат идёт через `POST /api/v1/conversations/{id}/messages` и отдаёт SSE. В `data` каждого события объект с полем `type`: `token`, `tool_call`, `citation`, `done` или `error`. Ошибка провайдера приходит событием `error` с телом по RFC 9457.

Скаутинг-отчёты ([ADR-0006](docs/adr/0006-scouting-report-generation.md)):

- `POST /api/v1/reports` с `{player_name, season?, language?}` отдаёт SSE: `snapshot` с частичным отчётом, затем `done` с `report_id` или `error`.
- `GET /api/v1/reports` — свои отчёты, с курсором и фильтром `player_id`.
- `GET /api/v1/reports/{id}` — чужой отчёт отдаёт 404.

В чате то же самое делают инструменты `create_scouting_report` и `search_reports`. Отчёт, невалидный с первой попытки, генерируется повторно; долю повторов видно в `llm_usage` как операции `reports.generate_retry` среди `reports.generate`, а исход каждой попытки пишется в лог событием `reports.validation`.

Типы событий описаны в OpenAPI. TS-типы фронтенда генерируются из него в `frontend/src/shared/api/schema.d.ts`. После правки схем на бэкенде: `cd frontend && pnpm api:types`.

## Для разработки

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

Хуки pre-commit используют те же конфиги, что CI:

```bash
uv run --project backend pre-commit install
```

Как мы работаем:

- **Trunk-based.** Короткие ветки, PR в `main`, задачи в GitHub Issues, PR ссылается на issue.
- **CI обязан быть зелёным:** ruff, mypy --strict и pytest на бэкенде; eslint, tsc и vitest на фронтенде. Джобы включаются по путям, правка фронтенда не гоняет бэкенд.
- **Conventional commits,** например `feat(players): фаззи-резолюция имени игрока через pg_trgm`.
- **Вся конфигурация через окружение.** В git лежит только [.env.example](.env.example).

Что ещё почитать:

- [spec.md](spec.md) — что это за продукт и куда движется.
- [CONTEXT.md](CONTEXT.md) — глоссарий: одно понятие, одно имя.
- [docs/architecture.md](docs/architecture.md), [docs/conventions.md](docs/conventions.md), [docs/design-system.md](docs/design-system.md).
- [docs/evals.md](docs/evals.md) — eval ответов, eval-срез и ручной прогон в CI.
- [docs/adr/](docs/adr/) — почему решено именно так.
