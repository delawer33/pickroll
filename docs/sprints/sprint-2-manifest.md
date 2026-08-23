# Sprint 2 orchestration manifest (issue #13)

Mode: fast. Push policy: local main only (no push requested).
Plan: docs/sprints/sprint-2-design.md. Base: main @ 87143b1.

## Slices

### S1 Seed до основания NBA
- status: ready (verified: 50 passed; commit 8f255a8; migration b3f8a2d4c9e1 ← c1a4f7e2b830; FIRST_SEASON=1946-47, spike on real dataset; full seed on dev DB OK: 1.56M box_scores, Wilt 1961-62 blocks NULL)
- branch/worktree: feat/sprint2-s1-seed @ ../pickroll-wt-s1
- deviations: system.md + seed docstring updated (stale 2005-06 claims); schema.d.ts regenerated; era-aware DND minutes filter in service; old-boundary tests rewritten; deferred.md += minutes zero-gaps до ~1970х, rebounds_total не в METRIC_INTRODUCED
- note: dataset fills untracked metrics with zeros → parsing gates on METRIC_INTRODUCED, not empties
- write-set: backend seed/rows.py, players/{models,schemas,service}.py, prompts/tools/get_player_stats.md, migration, scripts/build_fixture.py, tests/fixtures/dataset/, tests
- done: `pytest tests/test_seed_rows.py tests/test_player_stats.py` green in worktree; old-era player → null not 0
- retries: 0

### S2 Корпус — схема и ingestion CLI
- status: ready (verified: 14 passed; commit 4034d4f; migration a7c3e9d41f28 ← c1a4f7e2b830)
- branch/worktree: feat/sprint2-s2-corpus @ ../pickroll-wt-s2
- deviations: +httpx dep; ingest cache_dir kwarg + IngestStats; wiki via rest.php wikitext; ForeignEmbeddingModelError in ingest.py; dim 768 hardcoded in schema; tokens≈chars/4; rulebook pinned to 2025-26 PDF (2023-24 URL dead)
- write-set: backend corpus/* (NEW), agent/transport.py, config.py, migration, models.py reexport, pyproject.toml
- done: `pytest tests/test_corpus_chunking.py tests/test_corpus_ingest.py` green
- retries: 0

### S3 Гибридный поиск
- status: ready (verified: 6 passed; commit f27e1d0; deviations: rerank-fail возвращает полный RRF-список, caller trims; сортировка по relevance_score у себя)
- branch/worktree: feat/sprint2-s3-search @ ../pickroll-wt-s3 (off S2 branch)
- write-set: corpus/search.py, corpus/rerank.py, config.py
- done: `pytest tests/test_corpus_search.py` green
- retries: 0

### S4 search_corpus + цитаты насквозь
- status: ready (verified: 58 backend + 69 frontend passed, tsc clean; commit ca10653; migration d4f8b2c7e915 ← a7c3e9d41f28)
- branch/worktree: feat/sprint2-s4-citations @ ../pickroll-wt-s4 (off S3)
- deviations: ToolContext несёт tool_call_id/session_factory/settings; wire.ts и useConversationStream уже покрывали citation; 2 старых теста обновлены; фикстуры фронта kind 'quote'→'quotes'; embed-операция "agent.search_corpus"
- write-set: agent/{tools,loop,evidence}.py, prompts, conversations/*, migration; frontend chat feature + wire.ts
- done: `pytest tests/test_agent_loop.py tests/test_conversations.py` + frontend tests green
- retries: 0

### S5 Ретривал-eval
- status: ready (verified: 3 passed; commit d049092; baseline recall@10=1.0, MRR=0.958; thresholds 0.95/0.90)
- branch/worktree: feat/sprint2-s5-eval @ ../pickroll-wt-s5 (off S3)
- deviations: golden source = метка by_source, не фильтр; evaluate() требует готовые векторы (CLI считает живые); синтетический rulebook-фикстур вместо целикового; +pyyaml
- write-set: backend/evals/ (NEW), corpus/eval.py, tests/test_retrieval_eval.py
- done: `pytest tests/test_retrieval_eval.py` green (CI gate with threshold)
- retries: 0

## Итог (2026-08-23)

Все 5 слайсов merged в локальный main: merge 3629ac6 (integration/sprint2).
Миграционная цепочка: c1a4f7e2b830 → a7c3e9d41f28 (S2) → d4f8b2c7e915 (S4) → b3f8a2d4c9e1 (S1, перецеплена при интеграции).
Полный гейт на integration: 160 backend + 69 frontend тестов, mypy strict, ruff, eslint, tsc — зелёные; schema.d.ts перегенерирован, дрифта нет.
Worktrees удалены, ветки слайсов удалены. Не запушено (push не запрашивался).
Issue #13: закрыть вручную — gh-закрытие заблокировано классификатором разрешений.
Blocked: нет.

## Waves
1. S1 ‖ S2 (migration heads may conflict — fix at integration)
2. S3
3. S4 ‖ S5
4. Integrate: merge in order S1,S2,S3,S4,S5 → full suite → main

## Decisions
- 2026-08-23: merged pending feat/evidence-tool-result to local main (no open PR) + committed sprint-2 docs — prerequisites for S4 and for worktree visibility.
