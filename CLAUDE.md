# Court Vision

AI-ассистент баскетбольного скаута — сервис аналитики NBA. See [spec.md](spec.md) for the full product spec, [docs/architecture.md](docs/architecture.md) for system-level decisions, [docs/roadmap.md](docs/roadmap.md) for the sprint plan, and [docs/conventions.md](docs/conventions.md) for mandatory code conventions.

Known rough edges we've decided not to fix yet live in [docs/deferred.md](docs/deferred.md) — check it before assuming a surprising behaviour is news, and add to it instead of opening an issue when something genuinely can wait.

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
