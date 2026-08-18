# Court Vision

AI-ассистент баскетбольного скаута — сервис аналитики NBA. See [spec.md](spec.md) for the full product spec, [docs/architecture.md](docs/architecture.md) for system-level decisions, [docs/roadmap.md](docs/roadmap.md) for the sprint plan, and [docs/conventions.md](docs/conventions.md) for mandatory code conventions.

Known rough edges we've decided not to fix yet live in [docs/deferred.md](docs/deferred.md) — check it before assuming a surprising behaviour is news, and add to it instead of opening an issue when something genuinely can wait.

## Экономия контекста

Одно правило: **файлы проекта меняются только через Write/Edit.** Любая внешняя запись — `ruff format`, `--fix`, `prettier --write`, `sed -i`, heredoc-codemod, редирект в исходник — инвалидирует снапшот харнесса, и файл целиком, с номерами строк, приезжает в контекст второй раз. Форматирует pre-commit на `git commit`, когда содержимое файлов уже не нужно; код пишется сразу под конфиг (ruff line-length 100), чтобы коммитный формат был no-op.

Это форсирует [`.claude/hooks/context-guard.py`](.claude/hooks/context-guard.py) — он же не даёт читать файл ≥600 строк целиком (Read с `limit` проходит). Гард fail-open, исключения — `/tmp`, `scratchpad`, `.venv`, `node_modules`. Мешает — правь гард, а не обходи.

Остальное словами, коротко: не перечитывать файл после Edit «чтобы убедиться» (Edit падает сам, если не применилось); в доках искать `grep`, а не `cat`; форму решать до первого Write, а не по ходу набора; про причину баги думать до эксперимента, а не после таймаута.

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Reporting information

When reporting information to me, be extremely concise and sacrifice grammar for the sake of concision.
