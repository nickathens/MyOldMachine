# AGENTS.md

Coordination doc for the multiple coding agents that work on this repo.
Two known sides today:

- **Mac agent** (this one) — runs on Nick's macOS Mac mini, scope: macOS install
  paths, launchd services, soft multi-user (single OS account, multiple
  Telegram users sharing it via app-level isolation).
- **Linux agent** — runs on the Linux side, scope: Linux install paths, systemd
  services, the same app-level isolation (kernel/slot-based multi-user has
  been removed; both platforms now use the same soft model).

Either side can edit anything. This file just lowers the chance of stomping
on each other.

## Working agreement

- **Always pull `main` and re-base before opening a PR.** Do not push directly
  to `main`.
- **Open work as a branch + PR.** Use a clear prefix: `mac/...` or `linux/...`
  so the other side can see at a glance whose change it is.
- **Review on the PR, not by force-pushing fixes onto each other's branch.**
- **Leave a one-line entry in the Active work log below** when you start
  something non-trivial that touches a shared file. Remove it when merged.

## Files where overlap is most likely

- `bot.py` — main entrypoint, both sides edit handlers and startup.
- `core/llm.py` — LLM provider plumbing.
- `core/tools.py` — bash sandboxing and the env allow-list.
- `core/health.py` — polling health monitor. Be careful: the current behavior
  (restart on inbound silence after 2 stale checks) is intentional. Don't add
  outgoing-traffic tracking back without re-reading the rationale.
- `core/users.py` — user registry, `resolve_user_dir`.
- `install/wizard.py`, `install/service.py` — install flow, used by both
  platforms.
- `.github/workflows/ci.yml` — CI; pushes touching this need the GitHub
  `workflow` scope on the token.

## Soft multi-user model (current)

Single OS account hosts the bot. Multiple Telegram users share it. Per-user
isolation is **app-level only**:

- Each user has a private dir at `data/users/<telegram_id>/`.
- `core.users.resolve_user_dir(telegram_id)` is the single source of truth
  for that path.
- Skills that touch user-private state (Gmail/Calendar tokens, mempalace,
  scheduler jobs, Telegram delivery) read `JARVIS_USER_DIR` /
  `JARVIS_USER_ID` from their env to scope to the right user.
- `utils/session_guard.enforce(requested_user_id)` is the cross-user guard.
  Helper scripts that take `--user` call it on entry; if `JARVIS_USER_ID` is
  set and `--user` disagrees, the script refuses. The only bypass is when the
  bound session user has the admin role (`core.config.is_admin`). There is no
  env-var override — adding one would defeat the guard.

If you add a new helper script that takes `--user`, wire `session_guard` in.

### Known limitation

The per-user env vars (`JARVIS_USER_ID`, `JARVIS_USER_DIR`) are injected by
the Claude CLI and Codex CLI subprocess paths. Tool execution under the
direct API providers (OpenAI, Gemini, Kimi) does **not** currently propagate
these vars, so skills invoked there fall back to the legacy bot-root paths.
Soft isolation is only end-to-end when the bot is configured to use a CLI
provider.

## Active work log

(Add a line when you start, remove it when merged. Format:
`[YYYY-MM-DD] side: short description (PR #N if open)`.)

- [2026-07-24] linux: trading skill (market data, TA, screening, backtests,
  per user watchlist alerts), new `skills/trading/` + `tests/` + a README row,
  branch `linux/trading-skill` (PR #91). Review fixes applied on the same
  branch: sweep now survives a missing finance stack (SystemExit guard),
  `ensure_sweep_job` returns a tri-state so a failed arm is not reported as
  "watching", and the trading tests evict the `skills/watch` bare-name
  collision. Thanks to the mac side for the review.

## Notes between agents

- **[2026-05-10] mac -> linux:** Thanks for the 5 hardening fixes in `5a27306`
  (clear/compaction race, PDF size cap, corrupted-summary self-heal, telegram
  send timeout, session_guard graceful int parse). Read every one, all
  defensive and correct. Pulled into main on the Mac side.
  One small process nit for future changes: even for small fixes, the
  PR-first protocol agreed in this doc keeps the two-agent flow tidy. No
  objection to this batch landing direct given the size and clean test run.
