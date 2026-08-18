# Court Vision

AI-ассистент баскетбольного скаута — сервис аналитики NBA. See [spec.md](spec.md) for the full product spec, [docs/architecture.md](docs/architecture.md) for system-level decisions, [docs/roadmap.md](docs/roadmap.md) for the sprint plan, and [docs/conventions.md](docs/conventions.md) for mandatory code conventions.

Known rough edges we've decided not to fix yet live in [docs/deferred.md](docs/deferred.md) — check it before assuming a surprising behaviour is news, and add to it instead of opening an issue when something genuinely can wait.

## Экономия контекста

Обязательно для всех агентов. Контекст — дефицитный ресурс: каждый лишний токен приближает компакт, а компакт теряет детали задачи.

**Не давать внешним процессам трогать файлы, которые ты держишь в контексте.** Любое изменение файла вне Write/Edit (форматтер, codemod, `git checkout`) инвалидирует снапшот харнесса, и файл целиком, с номерами строк, приезжает в контекст по второму разу. Отсюда:

- **Форматтер руками не запускается никогда.** Ни `ruff format`, ни `prettier --write`, ни `pre-commit run`. Форматирование делает pre-commit на `git commit` — то есть один раз и тогда, когда содержимое файлов уже не нужно. Если хук что-то поправил, `git commit` падает — просто повторить коммит.
- **Код пишется сразу под конфиг** (ruff: line-length 100; prettier: дефолты). Тогда коммитный формат — no-op, и эха нет вообще.
- **Линт и тайпчек вручную не запускаются вообще.** Ни `ruff check`, ни `mypy`, ни `eslint`, ни `tsc`. Их прогоняет pre-commit на `git commit` — один раз, теми же конфигами, что и CI. Нужна проверка — коммить; упавший хук покажет те же ошибки. Коммить дробно, а не одним куском в конце.
- **Тесты — сколько нужно.** `pytest`, `pnpm test` в pre-commit не входят, их результат нельзя получить иначе.

**Не перечитывать то, что уже видел.**

- После Edit/Write файл не перечитывается «чтобы убедиться» — Edit падает с ошибкой, если правка не применилась.
- Если Edit всё же упал на устаревшем снапшоте — читать не файл, а диапазон: `Read` с `offset`/`limit` вокруг цели.
- Большие файлы читать диапазонами; в доках искать через `grep`, а не `cat`.

**Транскрипты сабагентов не читать.** `TaskOutput` запрещён в `.claude/settings.json`: сырой JSONL несёт base64-подписи блоков размышления, дубли диффов и полные `tool_result` — десятки тысяч токенов мусора за один вызов. Результат сабагента приходит сам, в task-notification; для статуса — `TaskList` / `TaskGet`.

**Вывод команд обрезать по месту.** `pytest -q`, `| tail -30` на шумных прогонах, `git diff --stat` перед `git diff`, `gh` с `--json` + нужными полями вместо полного вывода. Жёсткий предел вывода Bash и сабагентов — 20k символов (`BASH_MAX_OUTPUT_LENGTH` / `TASK_MAX_OUTPUT_LENGTH` в `.claude/settings.json`).

**Форму решать до того, как писать.** Переписывание файла целиком по второму и третьему разу — это «проектирую по ходу набора». Решение о форме (роутинг, схема, границы модуля) принимается до первого Write. То же с отладкой: сначала рассуждение о причине, потом эксперимент — иначе тупиковый прогон с таймаутом стоит дороже, чем минута размышления.

**Что из этого форсируется машинно.** [`.claude/hooks/context-guard.py`](.claude/hooks/context-guard.py) — PreToolUse-гард: блокирует `ruff check --fix`, `ruff format`, `prettier --write`, `eslint --fix`, `pre-commit run`, `sed -i`, heredoc-codemod'ы, редиректы в исходники, `git checkout/restore/stash`, Write по существующему файлу ≥60 строк, Read без `limit` по файлу ≥600 строк. Исключения — пути в `/tmp`, `scratchpad`, `.venv`, `node_modules`. Гард fail-open: своя ошибка = разрешить. Если правило реально мешает — правь гард, а не обходи его.

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Reporting information

When reporting information to me, be extremely concise and sacrifice grammar for the sake of concision.
