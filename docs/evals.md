# Eval ответов

Золотой набор (`backend/evals/answers.yaml`) прогоняется через прод-агента. Ответ проверяется
детерминированно: ожидаемые Инструменты, маркеры `[n]`, источники Цитат, отказ. Потом его
оценивает LLM-судья.

## Локальный прогон

Нужна полная локальная база (seed и корпус) и ключи шлюза в `.env`. Прогон идёт на базе из
`DATABASE_URL`.

```bash
cd backend
uv run python -m court_vision.answer_eval                  # весь набор
uv run python -m court_vision.answer_eval --category stats # одна категория
uv run python -m court_vision.answer_eval --id stats-curry-2015-16
uv run python -m court_vision.answer_eval --similar-pairs  # золотые пары, без LLM
```

Команда печатает сводку и путь к отчёту: JSON и markdown в `backend/evals/reports/`, каталог в
git не попадает.

Код выхода 1 значит, что результат ниже порогов из `backend/evals/answers_thresholds.yaml`.
Пороги коммитятся. Общие пороги проверяются только на полном прогоне, пороги категорий — на
выбранных категориях.

Прогон стоит денег. Пока итерируешь, гоняй одну категорию. Весь набор — один раз перед PR.

`--similar-pairs` проверяет Золотые пары Похожих игроков (`backend/evals/similar_pairs.yaml`).
Близость отпечатков заведомо похожих игрок-сезонов должна быть выше порога, заведомо непохожих
ниже.

Модель здесь не вызывается, прогон бесплатный. Код выхода 1 при проваленной паре. Нужны полная
база и пересобранные отпечатки.

## Судья

Модель судьи задаёт `EVAL_JUDGE_MODEL`, отдельно от `LLM_MODEL`: судья должен быть сильнее
судимого. Шлюз и ключ те же.

Дефолт `anthropic/claude-sonnet-4-5` работает только с прямым адресом провайдера. Если задан
`LLM_API_BASE`, пиши имя модели шлюза с префиксом `openai/`:
`EVAL_JUDGE_MODEL=openai/anthropic/claude-sonnet-4.5`. Иначе шлюз вернёт 404, и каждый вопрос
получит `judge_error`.

С ключами Langfuse прогон записывается как dataset run. Без них этот шаг молча пропускается.

## Eval-срез

Герметичное подмножество данных: на нём eval воспроизводим на любой машине и в CI. Решение —
[ADR-0004](adr/0004-eval-slice-copy-dumps-in-git.md). Дампы (`backend/evals/slice/`, сжатый
COPY и `manifest.json`) лежат в git.

Что входит в срез:

- последний сезон базы, сезоны из `backend/evals/slice.yaml` и все сезоны из вопросов набора;
- игроки и команды целиком;
- rulebook целиком;
- статьи Wikipedia из `expect_documents` набора, с готовыми эмбеддингами.

### Пересборка

Пересобирай, когда меняется Золотой набор или `slice.yaml`. Нужна полная локальная база с seed
и проиндексированным корпусом. Сборщик её только читает.

```bash
cd backend
uv run python -m court_vision.eval_slice build
```

Сборщик проверяет, что в срезе есть каждый сезон и каждая статья, о которых спрашивает набор.
Он падает, если статьи нет в корпусе полной базы или срез больше 15 МБ.

Если срез не умещается в бюджет, его выкладывают asset'ом релиза:
`tar -czf eval-slice.tar.gz -C backend/evals/slice .`. Тег релиза указывают в форме запуска
workflow.

### Прогон на срезе

`--slice` работает с чистой базой: применяет миграции, заливает срез, пересчитывает производные
данные (`court_vision/eval_slice/derived.py`) и запускает eval.

```bash
createdb court_vision_slice    # пустая база на том же сервере; расширение vector ставит миграция
DATABASE_URL=postgresql+asyncpg://…/court_vision_slice \
  uv run python -m court_vision.answer_eval --slice --category stats
```

Загрузчик пишет только в пустую базу или в ту, где этот срез уже лежит. Полную локальную базу
`--slice` не тронет.

До первого платного вызова ловятся две ошибки: устаревший срез (набор дописали, срез не
пересобрали) и `EMBEDDING_MODEL`, не совпадающая с моделью векторов среза.

Ограничение: на вопросы с сезоном срез отвечает так же, как полная база. Инструмент без сезона
(карьера) считает только по сезонам среза.

## Eval в CI

Workflow [`answer-eval.yml`](../.github/workflows/answer-eval.yml) запускается только вручную:
Actions → Answer eval → Run workflow. Категорию или id можно сузить. Автоматического гейта на PR
нет, он запланирован на спринт 5.

Workflow поднимает Postgres (`pgvector/pgvector:pg17`), загружает срез и гоняет eval. Отчёт
попадает в summary джобы и в артефакт `answer-eval-report`.

Прогон стоит денег. Для проверки workflow запускай с категорией или id, весь набор — один раз.

Секреты и переменные кладёт владелец репозитория в Settings → Secrets and variables → Actions.

| имя | вид | обязателен | что это |
|---|---|---|---|
| `LLM_API_KEY` | secret | да | ключ LLM-шлюза: агент, судья и эмбеддинги запросов |
| `LLM_API_BASE` | secret | если через шлюз | адрес шлюза (`https://<шлюз>/v1`) |
| `RERANK_API_KEY` | secret | нет | ключ реранкера; пусто — поиск без реранка |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | secret | нет | прогон как dataset run в Langfuse |
| `LLM_MODEL`, `EVAL_JUDGE_MODEL`, `EMBEDDING_MODEL` | variable | нет | имена моделей так, как их зовёт шлюз; пусто — дефолты из `.env.example` |
