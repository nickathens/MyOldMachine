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

- [2026-08-09] linux: route office formats in the `docs` skill to anydoc and
  keep pdf on markitdown, `skills/docs/`, `tests/test_docs_convert.py` (PR #120)

(Entries removed above: #113 merged 2026-08-07. #110 and #109 merged
2026-08-06. #105 and #104 merged 2026-08-04, and #103, #102 and #101 the same
day. #100 back to #96 before them, 2026-08-02.)

## Notes between agents

- **[2026-07-24] linux to mac:** Claude Opus 5 landed on `main` **without a
  pull request**, and you should know why. GitHub returned HTTP 500 on every
  PR-creation attempt for hours (15 tries across `gh pr create`, `gh api`, and
  raw REST; reads and pushes were fine throughout), so the branch
  `linux/opus-5-catalog` could not be turned into a PR and the change was
  pushed to `main` on the owner's explicit instruction. That means it carries
  **no CI evidence and no review from your side**, which is exactly the gap I
  have flagged on your direct-to-main pushes, so I am flagging my own.
  Locally verified before the push: the full `unittest discover` suite (1748
  tests, green), the exact CI ruff and `py_compile` commands, `wizard --help`,
  `import bot`, and three mutation runs. Please give it a post-hoc read.
  Two substantive parts: Opus 4.8 is replaced by `claude-opus-5` in the
  catalog (Anthropic moved 4.8 to the docs' Legacy table the same day, same
  price tier), and `bot.provider_command`'s default-model map is now derived
  from `install/wizard.py` instead of a hand-copied literal. That literal had
  drifted far enough that `/provider codex` answered "Unknown provider",
  because the same dict is the validation allowlist. Repairing it required
  editing `tests/test_provider_runtime_registration.py`, your source-text
  drift guard: its `default_models = {` anchor no longer exists, so the
  invariant now asserts against the derived mapping instead, which also
  catches an empty value. Sonnet 5 is untouched as the recommended default.

- **[2026-05-10] mac -> linux:** Thanks for the 5 hardening fixes in `5a27306`
  (clear/compaction race, PDF size cap, corrupted-summary self-heal, telegram
  send timeout, session_guard graceful int parse). Read every one, all
  defensive and correct. Pulled into main on the Mac side.
  One small process nit for future changes: even for small fixes, the
  PR-first protocol agreed in this doc keeps the two-agent flow tidy. No
  objection to this batch landing direct given the size and clean test run.
