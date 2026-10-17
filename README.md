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

После загрузки seed сам пересобирает Статистические отпечатки игрок-сезонов (таблица
`player_fingerprints`, по ней работает `find_similar_players`). Пересобрать отдельно —
идемпотентная замена таблицы в одной транзакции, около 10 секунд на полной базе:

```bash
docker compose exec api python -m court_vision.players.rebuild_fingerprints
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

### Eval ответов

Золотой набор ответов (`backend/evals/answers.yaml`) прогоняется через прод-агента, ответы
проверяются детерминированно (ожидаемые Инструменты, маркеры `[n]`, источники Цитат, отказ) и
оцениваются LLM-судьёй. Прогон идёт на базе из `DATABASE_URL` — нужна полная локальная база
(seed + корпус) и ключи шлюза в `.env`:

```bash
cd backend
uv run python -m court_vision.answer_eval                  # весь набор
uv run python -m court_vision.answer_eval --category stats # только категория
uv run python -m court_vision.answer_eval --id stats-curry-2015-16
uv run python -m court_vision.answer_eval --similar-pairs  # золотые пары, без LLM
```

`--similar-pairs` проверяет Золотые пары Похожих игроков (`backend/evals/similar_pairs.yaml`):
близость отпечатков заведомо похожих игрок-сезонов выше порога, заведомо непохожих — ниже.
Детерминированно и бесплатно (модель не вызывается), код выхода 1 при проваленной паре; нужны
полная база и пересобранные отпечатки.

Команда печатает сводку и путь к отчёту (JSON + markdown в `backend/evals/reports/`, каталог в
git не попадает). Код выхода 1 — результат ниже порогов из `backend/evals/answers_thresholds.yaml`
(пороги коммитятся; общие пороги проверяются только на полном прогоне, пороги категорий — на
выбранных категориях). Прогон
стоит денег: итерируешь — гоняй по категории, весь набор — один раз перед PR.

Модель судьи — `EVAL_JUDGE_MODEL` (отдельно от `LLM_MODEL`: судья должен быть сильнее
судимого), шлюз и ключ — те же. Дефолт `anthropic/claude-sonnet-4-5` — для прямого адреса
провайдера; при заданном `LLM_API_BASE` пиши имя шлюза с префиксом `openai/`
(`EVAL_JUDGE_MODEL=openai/anthropic/claude-sonnet-4.5`), иначе шлюз вернёт 404 и каждый
вопрос получит `judge_error`. При ключах Langfuse прогон записывается как dataset run,
без них шаг молча пропускается.

### Eval-срез

Герметичное подмножество данных, на котором eval ответов воспроизводим на любой машине и в CI
(решение — [ADR-0004](docs/adr/0004-eval-slice-copy-dumps-in-git.md)). Состав: полные сезоны
(последний сезон базы, сезоны из `backend/evals/slice.yaml` и все сезоны, названные в вопросах
Золотого набора), игроки и команды целиком, rulebook целиком и статьи Wikipedia из
`expect_documents` Золотого набора — с готовыми эмбеддингами. Дампы (`backend/evals/slice/`,
сжатый COPY + `manifest.json`) лежат в git.

Пересобрать — когда меняется Золотой набор или `slice.yaml` (нужна ПОЛНАЯ локальная база:
seed + индексация корпуса; она только читается):

```bash
cd backend
uv run python -m court_vision.eval_slice build
```

Сборщик проверяет, что каждый сезон и статья, о которых спрашивает набор, есть в срезе, и падает,
если статьи нет в корпусе полной базы или срез не умещается в 15 МБ. Сверх бюджета срез
выкладывается asset'ом релиза: `tar -czf eval-slice.tar.gz -C backend/evals/slice .`, а в форме
запуска workflow указывается тег релиза.

Прогон на срезе — с нуля на чистой базе: `--slice` применяет миграции, заливает срез и
пересчитывает производные данные (`court_vision/eval_slice/derived.py`), потом идёт eval:

```bash
createdb court_vision_slice    # пустая база на том же сервере; расширение vector ставит миграция
DATABASE_URL=postgresql+asyncpg://…/court_vision_slice \
  uv run python -m court_vision.answer_eval --slice --category stats
```

Загрузчик грузит только в ПУСТУЮ базу (или ту, где срез уже лежит), иначе отказывается: полную
локальную базу `--slice` не тронет. Устаревший срез (набор дописали, срез не пересобрали) и
несовпадение `EMBEDDING_MODEL` с моделью, которой посчитаны векторы среза, ловятся до первого
платного вызова. Ограничение: вопросы, привязанные к сезону, на срезе дают те же данные, что на
полной базе; Инструмент без сезона (карьера) считает по сезонам среза.

### Eval в CI

Workflow [`answer-eval.yml`](.github/workflows/answer-eval.yml) — только ручной запуск
(`workflow_dispatch`: Actions → Answer eval → Run workflow; можно сузить категорию или id).
Поднимает сервис Postgres (`pgvector/pgvector:pg17`), загружает срез, гоняет eval; отчёт —
в summary джобы и артефактом `answer-eval-report`. Автоматического гейта на PR нет (спринт 5).

Секреты и переменные кладёт владелец репозитория (Settings → Secrets and variables → Actions):

| имя | вид | обязателен | что это |
|---|---|---|---|
| `LLM_API_KEY` | secret | да | ключ LLM-шлюза: агент, судья и эмбеддинги запросов |
| `LLM_API_BASE` | secret | если через шлюз | адрес шлюза (`https://<шлюз>/v1`) |
| `RERANK_API_KEY` | secret | нет | ключ реранкера; пусто — поиск без реранка |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | secret | нет | прогон как dataset run в Langfuse |
| `LLM_MODEL`, `EVAL_JUDGE_MODEL`, `EMBEDDING_MODEL` | variable | нет | имена моделей так, как их зовёт шлюз; пусто — дефолты из `.env.example` |

Прогон стоит денег: для проверки workflow запускай с категорией или id, весь набор — один раз.

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
