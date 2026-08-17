# Архитектура (верхний уровень)

Зафиксированные решения уровня системы. Детали каждого блока проектируются в своём спринте (см. [roadmap.md](roadmap.md)). Принцип выбора инструментов: прод-грейд решения по умолчанию; руками пишем только смысловое ядро проекта (агентный цикл, инструменты, поиск).

## Состав системы

- **API** — FastAPI (Python), REST + SSE для стриминга ответов агента.
- **Frontend** — SPA: Vite + React + TypeScript, Tailwind поверх собственных дизайн-токенов (палитра, типографика, сетка — свои, не библиотечные). Тонкая витрина: сложность остаётся в API.
- **БД** — PostgreSQL + pgvector: реляционная статистика, векторный поиск (RAG-корпус и статистические отпечатки игроков), JSONB для сырых payload'ов, полнотекстовый tsvector.
- **Фоновые задачи** — arq + Redis: ежедневный sync, генерация дайджеста, крон. Redis также — кеш и rate limiting.
- **LLM-слой** — litellm как транспорт/нормализация провайдеров (Anthropic / Gemini / Groq); свой код: агентный цикл, инструменты, фолбэк-политика, учёт стоимости. См. [ADR-0001](adr/0001-litellm-transport-own-agent-loop.md).
- **Auth** — Clerk (managed IdP): готовые React-компоненты на фронте, JWKS-валидация токенов в FastAPI. См. [ADR-0002](adr/0002-clerk-managed-idp.md).
- **Наблюдаемость** — structlog (структурные JSON-логи), Langfuse Cloud (трейсы агентного цикла, токены, стоимость — через колбэк litellm), своя таблица `llm_usage` для продуктовых агрегатов (дашборд стоимости, будущие лимиты на пользователя).
- **Evals** — эталонные вопросы в git (YAML), тонкий раннер поверх прод-агента, LLM-as-judge, результаты и сравнение прогонов — в Langfuse Experiments.
- **Деплой (в конце v1)** — Railway (API, воркер, Postgres, Redis) + Vercel (SPA, preview-деплои на PR). CI — GitHub Actions. Локально — Docker Compose.

## Данные

- **Исторические:** Kaggle NBA, сезоны с 2005-06, гранулярность box score (без play-by-play) → разовый seed в Postgres.
- **Свежие:** balldontlie API free tier — только teams/players/games (счета матчей, без box scores). Ежедневный sync, cache-aside.
- **Корпус RAG:** Wikipedia (игроки, команды), rulebook NBA (PDF), собственные заметки → чанкинг → эмбеддинги → pgvector.
- **Абстракция:** `DataProvider` с реализациями `LocalProvider` (Postgres) и `BalldontlieProvider`; интерфейс не должен мешать переходу на платный тир balldontlie (v2).
