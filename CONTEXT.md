# MyOldMachine — Project Context

Last updated: 2026-03-17

## Overview

One-command machine takeover: converts any old laptop into a dedicated AI assistant controlled entirely through Telegram. Full OS provisioning, self-installing dependencies, always-on, LLM-agnostic.

**GitHub:** https://github.com/nickathens/MyOldMachine (private)
**Install:** `curl -fsSL https://myoldmachine.com/install | bash`

## Architecture

```
User (Telegram) → bot.py → core/llm.py (provider factory)
                                ↓
                    ┌───────────┼───────────────┐
                    │           │               │
              ClaudeCLI    OpenAI-compat    Gemini
              (native      (OpenRouter,     (native
               tools)       OpenAI,          function
                            Grok, Ollama)    calling)
                    │           │               │
                    └───────────┼───────────────┘
                                ↓
                         core/tools.py
                    ┌────────────────────┐
                    │  Unified Schema    │ ← Single tool definitions,
                    │  Env Hardening     │   transformed per-provider
                    │  Process Registry  │
                    │  Output Streaming  │
                    │  Script Preflight  │
                    └────────────────────┘
                                ↓
                    run_command | read_file
                    write_file | list_directory
                    check_process
```

### Tool-Use Flow (non-Claude providers)
1. User sends message via Telegram
2. `bot.py` builds system prompt + conversation history
3. `llm.py` sends to LLM API with tool definitions (from unified schema)
4. LLM returns structured tool call (e.g., `run_command("ls -la")`)
5. `tools.py` validates safety, sanitizes env, executes on host
6. Process tracked in ProcessRegistry; output streamed in chunks
7. Result appended to conversation, sent back to LLM
8. Loop repeats until LLM returns text (not a tool call)
9. **Fallback:** If text contains code blocks/commands instead of structured calls, parser extracts and executes them
10. Final text sent to user via Telegram

### Claude CLI Provider
Uses Claude's native tool-use — no `tools.py` needed. Claude CLI runs bash, reads/writes files directly.

## File Structure

```
bot.py              — Main bot (Telegram handler, system prompt, message routing)
core/
  llm.py            — LLM provider factory + tool-use loops
  tools.py          — Unified tool schema + execution layer + 5 OpenClaw-inspired subsystems
  config.py         — .env loader
  scheduler.py      — APScheduler (reminders, scheduled tasks)
  session.py        — Conversation session management
  skill_loader.py   — Auto-loads skills from skills/ directory
  updater.py        — Git pull + restart mechanism
  self_install.py   — Runtime dependency installer
  memory.py         — Deep memory system (person models, observations, reflection)
  health.py         — Health check endpoint
install/
  wizard.py         — Interactive setup (provider, API key, Telegram token)
  provisioner.py    — OS-level provisioning (disable sleep, auto-login, etc.)
  ollama_setup.py   — Ollama auto-install + hardware benchmark + model recommendation
  os_detect.py      — Linux/macOS detection
  service.py        — systemd/launchd service registration
  templates/        — Service file templates
  cleanup_lists/    — Per-OS cleanup targets
install.sh          — One-command installer (curl | bash entry point)
skills/             — Modular skills (weather, translate, ocr, etc.)
utils/
  scheduler_cli.py  — CLI for managing scheduled jobs
  send_to_telegram.py — Send files/messages to users
  project_manager.py — Memory system project management
  observe.py        — Observation CLI (save behavioral observations)
  reflect.py        — Nightly reflection script (updates person models)
  cleanup.py        — Data cleanup utilities
  safe_json.py      — Atomic JSON read/write
```

## Tools (5 total)

| Tool | Description |
|------|-------------|
| `run_command` | Execute shell commands (foreground or background). Background returns process_id. |
| `read_file` | Read file contents with truncation at 50K chars |
| `write_file` | Write files with preflight validation (catches wrong-language content) |
| `list_directory` | List directory contents with sizes and types |
| `check_process` | Poll, list, or kill background processes by ID |

## OpenClaw-Inspired Subsystems (all in tools.py)

### 1. Process Management
- `ProcessRegistry` tracks all spawned processes by ID
- Background commands return a `process_id` for polling via `check_process`
- Kill with process group cleanup (gets child processes too)
- Auto-cleanup of old finished processes (max 20 tracked)
- All processes killed on bot shutdown

### 2. Environment Hardening
- API keys, tokens, and secrets stripped from inherited env (TELEGRAM_TOKEN, LLM_API_KEY, etc.)
- Pattern-based blocking: `*_SECRET*`, `*_TOKEN`, `*_PASSWORD`, `*_KEY`, `*_CREDENTIALS*`
- Safe-list of allowed vars (HOME, PATH, LANG, locale, proxy, Python/Node/Homebrew paths)
- Bot's Python venv removed from PATH (commands use system Python)
- `~/.local/bin` prepended for user-installed tools

### 3. Unified Tool Schema
- `TOOL_DEFINITIONS` — single list of tool specs
- `get_tools_openai()` — transforms to OpenAI-compatible format
- `get_tools_gemini()` — transforms to Gemini format (strips unsupported JSON Schema keywords)
- Adding a new tool = one place, both providers get it automatically

### 4. Output Streaming
- Commands stream stdout/stderr line by line into `ManagedProcess.output_chunks`
- Background processes: LLM can poll for "new output since last check" via `check_process`
- Foreground processes: full output collected with streaming (no more blocked `communicate()`)
- Truncation at 50K chars with notification

### 5. Script Preflight Validation
- On `write_file`, content is checked against file extension
- Catches: shell syntax in `.py` files, Python syntax in `.sh` files, shell in `.js` files
- Requires 2+ suspicious patterns to trigger (avoids false positives on string literals)
- File is still written — warning appended to tool result so LLM can self-correct

### 6. Fallback Tool-Call Parser
- When weak models write tool calls as text instead of structured `tool_calls`, the parser extracts and executes them
- Three extraction strategies: JSON-style tool calls, function-call syntax, code blocks with shell commands
- Integrated into both `_openai_tool_loop` (OpenAI/OpenRouter/Grok/Ollama) and Gemini loop
- Max 5 fallback attempts per response to prevent infinite loops
- Results fed back to the model as a user message with "I executed these, now respond"
- System prompt reinforced with explicit examples of WRONG (code blocks) vs RIGHT (tool calls)

## Safety Layer

- **Blocked commands:** `rm -rf /`, `mkfs`, `dd` to disk, fork bombs, `mv /`, `rm -rf /etc`, `curl|sudo bash`, `wget|sudo bash`
- **Write path blocklist:** `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`, `/etc/sudoers.d/`, `/boot/`, `/boot/grub/`, `/etc/crontab`, `/var/spool/cron/`
- **Limits:** 120s foreground timeout, 3600s background timeout, 50K char output cap, 25 tool iterations per request
- **Environment:** Sanitized (no leaked secrets), clean PATH per-OS, no bot venv leakage

## LLM Providers

| Provider | Tool-Use | Default Model | Notes |
|----------|----------|---------------|-------|
| Claude CLI | Native | claude-sonnet-4-6 | Full tool-use built into Claude's runtime |
| Claude API | None | claude-sonnet-4-6 | Text-only, no machine control |
| OpenAI | OpenAI-compat | gpt-5.5 | GPT-5.5 ($5/$30)/5.5 Pro/5.4 family/4.1. Vision + tools |
| DeepSeek | OpenAI-compat | deepseek-v4-flash | V4 Flash ($0.14/$0.28)/Pro ($1.74/$3.48), V3.2 legacy. No vision |
| Grok (xAI) | OpenAI-compat | grok-4-1-fast-non-reasoning | Vision on 4.1 Fast/4-0709. 4.1 Fast = 2M ctx |
| Kimi (Moonshot) | OpenAI-compat | kimi-k2.6 | K2.6 $0.95/$4.00, K2.5 $0.60/$3.00. 256K ctx. Vision on K2.5 |
| MiniMax | OpenAI-compat | MiniMax-M2.7 | Reasoning, 205K ctx. $0.30/$1.20/MTok. Vision on M2.5 |
| Gemini | Native | gemini-3-flash-preview | Free tier on 3 Flash Preview, 3.1 Flash-Lite, 2.5 family. 3.1 Pro paid only |
| Ollama | OpenAI-compat | llama3.1:8b | Local, free, auto-installs with hw benchmark |
| Ollama Cloud | OpenAI-compat | qwen3.5:cloud | Cloud-hosted, free, no local GPU needed |
| OpenRouter | OpenAI-compat | nemotron-3-super-120b:free | ~15 free models w/tool-use, ~200 req/day |

**Total: 11 providers.**

## Boot Persistence

The installer (`install.sh`) registers a system service (systemd on Linux, launchd on macOS) that:
- Starts automatically on boot
- Restarts on crash (5-second delay)
- Runs 24/7 without a terminal
- Survives hard reboots

Running `python bot.py` directly is for **testing only** — it dies when the terminal closes.

## Debug Pass (Mar 9)

14 bugs found and fixed:
1. Negative timeout crash in `_stream_process_output`
2. Deprecated `preexec_fn=os.setsid` — replaced with `start_new_session=True`
3. `VIRTUAL_ENV` and `PYTHONHOME` in safe env vars — removed
4. Blocked pattern bypass via command chaining (`rm -rf /etc && echo done`)
5. Missing `--no-preserve-root` pattern
6. Over-aggressive path blocking (`/home/user/Downloads` matched `/home`)
7. Blocked patterns recompiled on every call — now precompiled
8. No binary file detection or size limit on `read_file`
9. No size limit on `write_file`
10. Silent side effects on `new_output` property — renamed to `consume_new_output()`
11. `assistant_msg["content"] = None` broke OpenRouter models
12. Tool results sent without truncation (could overflow context)
13. Gemini `func_args` could be `None` — crash on `.get()`
14. Stale OpenRouter free model IDs in wizard

## Update Pass (Mar 17)

Full production readiness update — 166 unique users cloning, 0 issues filed:
1. **Model list refresh**: All 8 providers verified against official API docs
   - Added Grok 4.20 models (vision + reasoning/non-reasoning variants)
   - Added Claude Sonnet 4.5 (legacy) to Claude API provider
   - Added Gemini 3.1 Flash-Lite Preview, updated Flash-Lite deprecation notice (Mar 31)
   - Updated OpenRouter free models: +3 new (hunter-alpha, healer-alpha, nemotron-super-120b), +1 (minimax-m2.5), removed 2 non-free Qwen VL models
   - Changed OpenRouter default from `openai/gpt-oss-120b:free` to `openrouter/hunter-alpha` (1M ctx, vision+tools+reasoning)
2. **Bug fix: Grok `_is_reasoning_model()`** — "non-reasoning" contains "reasoning", so all `*-non-reasoning` models were incorrectly classified as reasoning (wrong token param, no temperature). Fixed by checking `"non-reasoning"` first.
3. **Bug fix: Grok `supports_vision`** — new 4.20 models support vision but weren't matched. Added `m.startswith("grok-4.20")`.
4. **install.sh fixes**: trap only handled EXIT (not INT/TERM); `$PYTHON` unquoted in several places; `exec` killed sudo keepalive before wizard runs.
5. **README improvements**: Added prerequisites section, install resume note, better troubleshooting (OpenRouter rate limits, Gemini quotas).
6. **GitHub Actions CI**: Python syntax check, shellcheck lint, provisioner dry-run on Ubuntu 22/24, tool schema validation, provider factory tests with Grok reasoning/vision assertions.

## Model Refresh (Apr 12)

All 11 providers verified against official API docs and pricing pages:
1. **OpenAI**: Added GPT-5.4 Mini ($0.75/$4.50), GPT-5.4 Nano ($0.20/$1.25), o3-mini. Updated GPT-4.1 Mini pricing ($0.40/$1.60). Removed pinned snapshot.
2. **Grok (xAI)**: Updated model IDs from `grok-4.20-beta-0309-*` to `grok-4.20-0309-*` (beta removed). Added Multi-Agent model. Removed retired models (grok-code-fast-1, grok-4-0709, grok-3, grok-3-mini).
3. **Gemini**: Added Gemini 3 Flash Preview and 3.1 Flash-Lite Preview (both have free tier). Updated deprecation timeline (2.5 Flash/Pro shutdown June 17, Flash-Lite Jul-Oct). Noted gemini-2.0-flash is deprecated.
4. **Kimi**: Removed discontinued `kimi-latest` (Jan 28, 2026). Added `kimi-k2-thinking`. Updated platform URL redirect.
5. **Ollama Cloud**: Added GLM 5.1, Qwen3 Coder Next, Gemma 4. Standardized `:cloud` tags. Removed local size tags.
6. **OpenRouter**: Major free model churn. Removed 5 models no longer free (hunter-alpha, healer-alpha, step-3.5-flash, mistral-small-3.1, trinity-mini, qwen3-4b). Added 2 new (Gemma 4 26B/31B with vision+tools). Changed default from `openrouter/hunter-alpha` to `nvidia/nemotron-3-super-120b-a12b:free`.
7. **Claude/DeepSeek**: No changes needed. Model lists already current.
8. **Claude API**: Pinned `claude-sonnet-4-5-20250929` for the legacy entry.
9. **MiniMax**: Added as 11th provider. M2.7 (reasoning, 205K ctx), M2.7-highspeed, M2.5 (vision), M2-her (dialogue). OpenAI-compat at api.minimax.io/v1. $0.30/$1.20/MTok.

## Model Refresh (Apr 24)

All 11 providers re-verified against live docs and pricing pages (12 days after Apr 12 pass):
1. **OpenAI**: GPT-5.5 released as flagship ($5/$30 per MTok, 1M ctx). Added GPT-5.5 Pro ($30/$180). GPT-5.4 demoted to "Also available". Default: `gpt-5.4` → `gpt-5.5`.
2. **DeepSeek**: Major version bump. V4 Flash ($0.14/$0.28, 1M ctx) as new default. V4 Pro added ($1.74/$3.48, 1M ctx). `deepseek-chat` and `deepseek-reasoner` preserved as V3.2 legacy aliases (still work per docs, 128K ctx). Default: `deepseek-chat` → `deepseek-v4-flash`.
3. **Kimi**: K2.6 released ($0.95/$4.00 per MTok). Added as default, K2.5 demoted to "Also available". Vision check left on K2.5 only (K2.6 vision not yet confirmed in docs). Default: `kimi-k2.5` → `kimi-k2.6`.
4. **Gemini**: 3 Flash Preview promoted to default ($0.50/$3, free tier active). 2.5 Flash/Pro/Flash-Lite still have free tiers per ai.google.dev docs. 3.1 Pro Preview remains paid-only. Reordered model list (3-series first). Default: `gemini-2.5-flash` → `gemini-3-flash-preview`.
5. **Grok**: Removed unverifiable `grok-4.20-0309-*` variants (3 entries). Added `grok-code-fast-1`, `grok-4-0709`, `grok-3-mini` back. Kept 4.1 Fast family as default.
6. **MiniMax**: Removed unconfirmed `MiniMax-M2-her`. Kept M2.7/M2.7-highspeed/M2.5.
7. **Ollama Cloud**: Refreshed DeepSeek cloud tag V3.2 → V4 Flash. Added `kimi-k2.6:cloud`. Removed `gemini-3-flash-preview:cloud` (not confirmed on Ollama's roster).
8. **OpenRouter**: Added 3 free models (`tencent/hy3-preview:free`, `inclusionai/ling-2.6-flash:free`, `openrouter/free`). Removed 2 no longer free (`arcee-ai/trinity-large-preview:free`, `google/gemma-3-27b-it:free`). Default unchanged.
9. **Claude**: No change needed. Sonnet 4.6 remains default; Opus 4.7 already listed.
10. Files updated: `install/wizard.py`, `bot.py`, `core/llm.py`, `.env.example`, `README.md`, `tests/prompt_eval.py`, `.github/workflows/ci.yml`, `CONTEXT.md`.

## Known Issues

- Google free tier quota can change without notice, breaking Gemini models
- `gemini-2.5-flash` and `gemini-2.5-pro` shutting down June 17, 2026 — migrate to Gemini 3 series
- `gemini-2.5-flash-lite` shutting down July-Oct 2026
- OpenRouter free model IDs can go stale — verify against their API
- OpenRouter free tier: 200 req/day — tool-use multiplies consumption (5-6 iterations per real request)
- macOS launchd service registration is fragile — `launchctl kickstart -k` is more reliable than unload/load
- Old Macs compile ffmpeg from source (~30-60 min)
- Ollama models below 7B are unreliable for tool-use (hallucinate calls, break JSON format)
- Weak models may still write commands as text; fallback parser catches most cases but can miss unusual formats

## Skills Expansion (Mar 17)

Added 17 new skills ported from the Telegram bot (37 -> 54 total):

| Skill | Category | Dependencies |
|-------|----------|-------------|
| workflow | Automation | pyyaml |
| voice | Audio | openai-whisper, ffmpeg |
| background-removal | Visual | rembg, Pillow |
| impeccable | Knowledge | (none) |
| research | Data | requests, beautifulsoup4, feedparser |
| midi | Music | mido |
| midi-to-audio | Music | fluidsynth, fluid-soundfont-gm, ffmpeg |
| sheet-music | Music | lilypond |
| music-theory | Music | music21 |
| audio-to-midi | Music | basic-pitch |
| algorithmic-composition | Music | mingus, pretty_midi, numpy |
| sound-design | Music | numpy, scipy |
| lighthouse | Web | lighthouse (npm) |
| screenshot-diff | Web | imagemagick, Pillow |
| stems | Audio | demucs, ffmpeg |
| price-monitor | Data | requests, beautifulsoup4 |
| docker-services | DevOps | docker |

All skills have:
- `SKILL.md` with generic paths (no user-specific references)
- `deps.json` for self-install system (apt, brew, pip, npm, check)
- Scripts in `scripts/` directory
- Python syntax validated
- Skill loader verified (all 58 skills load correctly)

## Resource-Aware Dependency Gating (Mar 17)

Skills with heavy dependencies now declare minimum resource requirements in `deps.json`:

```json
{
  "weight": "heavy",
  "min_ram_gb": 4,
  "min_disk_gb": 3,
  "install_note": "Installs PyTorch + Demucs models (~1.5 GB download)"
}
```

**How it works:**
1. `system_probe.py` detects RAM and free disk space on first boot, saves to `data/system_caps.json`
2. `skill_loader.py` reads resource requirements from `deps.json` and annotates the skill list with `[HEAVY]`/`[MEDIUM]` tags and warnings when the machine lacks resources
3. The LLM sees these warnings in its system prompt and asks the user before attempting heavy installs
4. `self_install.py` has a resource gate in `install_missing()` that blocks installation when resources are insufficient

**14 skills annotated** (6 heavy, 8 medium):
- Heavy: stems (4GB/3GB), voice (4GB/3GB), upscale (4GB/3GB), background-removal (2GB/2GB), blender (2GB/2GB), spreadsheet (1GB/2GB)
- Medium: audio-to-midi (2GB/1GB), browser (1GB/1GB), scraper (1GB/1GB), media (1GB/1GB), gimp (1GB/1GB), inkscape (1GB/1GB), audio-analysis (1GB/1GB), music-theory (1GB/1GB)

Light skills (40 remaining) have no resource requirements and install normally.

## Production Hardening (Mar 17)

Ported battle-tested improvements from the private Telegram bot (`claude-telegram-bot`):

**CLI Provider (`core/llm.py`):**
- **No-text timeout** (600s): Kills Claude if tools are running but no user-facing text produced for 10 minutes. Catches infinite tool loops.
- **Buffer overflow handling**: `read_line_with_timeout()` now catches `LimitOverrunError` and drains the buffer instead of crashing.
- **50MB subprocess buffer** (was 10MB): Prevents buffer overflow on large tool outputs.
- **Last-turn text fallback**: On error/timeout, prefers text from the latest assistant turn over the full accumulated partial text. More relevant results.
- **API key stripping**: Added `XAI_API_KEY`, `GROK_API_KEY` to the env vars stripped from Claude CLI subprocess.
- **Approach-timeout warning**: Logs at 80% of idle timeout threshold for diagnostics.

**Append-only message log (`core/message_log.py`):**
- Per-user SQLite database that records every exchange permanently
- Never touched by conversation compaction — provides complete searchable history
- WAL mode for concurrent reads, indexed by timestamp and role

**Bot (`bot.py`):**
- Message log integration: every user/assistant exchange logged in `_save_and_send()`
- Improved queued-message notification text

## First-Contact Orientation & Non-Technical UX (Mar 18)

**Problem:** The bot assumed users were technical. The first boot message was a raw dump of specs, provider info, and version numbers. The system prompt used technical language (file paths, CLI commands, tool names) when talking to users. Users cloning MyOldMachine are often non-technical — they want a helpful assistant, not a terminal.

**Changes:**

1. **Auto-firing orientation prompt** (`build_orientation_prompt()` in `bot.py`):
   - On first boot, instead of a static text message, the LLM generates a personalized introduction
   - Reads system capabilities (OS, RAM, disk, skill readiness) and translates them to plain language
   - Introduces itself, explains what it can do in categories (not raw skill names)
   - Asks the user about themselves (name, interests, immediate needs) to populate the person model
   - Falls back to a simple static message if the LLM call fails
   - Saved to conversation history as `[First boot — assistant introduced itself]` (not the raw prompt)

2. **Communication Style directive** in system prompt:
   - "Assume the user has NO experience with code, terminals, or technical concepts"
   - Concrete examples: "I'll convert the video" not "I'll run ffmpeg to transcode"
   - Adapts upward if the user demonstrates technical knowledge
   - Only shows jargon when the user specifically asks for technical details

3. **User-facing text rewritten**:
   - `/start`: Friendly greeting with skill count, no raw specs
   - `/help`: Reorganized by use case (memory, reminders, organization), not by internal module
   - Recovery message: Plain language ("I was working on something when I got interrupted")
   - Fallback first boot message: Simple and welcoming

**Files modified:** `bot.py` only — 4 sections changed (new function, system prompt, start_command, help_command, post_init).

## Testing

Tested on:
- **macOS Catalina 10.15.7** (Intel, user "mtsikala") — Gemini via OpenRouter
- Issues found and fixed: quota exhaustion, stale model IDs, verbose text dumps, restart race condition
- **tools.py integration tests:** All 5 subsystems verified (process mgmt, env hardening, unified schema, streaming, preflight)
- **Debug pass (Mar 9):** 14 bugs across tools.py, llm.py, wizard.py
- **Update pass (Mar 17):** Model refresh, 2 Grok bugs, 3 install.sh fixes, CI pipeline, README improvements
- **API key guides (Mar 17):** Centralized `API_KEY_GUIDES` dict in wizard.py — step-by-step signup instructions for all 7 API-key providers, shown during install and in fallback flow

## Scheduler Reliability Overhaul (Mar 18)

Ported from the Telegram bot to prevent recurring reminders from being silently lost:

1. **End date support (`--until`)**: Recurring jobs can now have an expiry date. The `end_date` is stored in `job_meta`, passed to APScheduler's `CronTrigger`/`IntervalTrigger`, and expired jobs are automatically cleaned up during the 60-second sync loop.

2. **Safe update (`update` subcommand)**: Changes a reminder's text without touching the trigger. Prevents the delete+recreate failure mode where the "recreate" step could fail, silently losing the reminder.

3. **Biweekly repeat**: Added `IntervalTrigger(weeks=2)` support. CLI accepts `--repeat biweekly`.

4. **LLM protocol rules**: Four mandatory rules in the system prompt:
   - Deadline-related recurring reminders MUST use `--until`
   - To reword, use `update`, NEVER delete+recreate
   - Always verify after creating (run `list`)
   - Never remove a recurring job unless user explicitly asks

**Files modified:** `core/scheduler.py`, `utils/scheduler_cli.py`, `bot.py`

## Bot Self-Protection (Mar 18)

Prevents the LLM from accidentally destroying its own runtime environment:

1. **Venv protection (`core/tools.py`)**: Commands targeting the bot's `.venv` directory are blocked — `rm`, `python -m venv`, `pip install/uninstall`. Catches both resolved and unresolved paths, and `cd-then-modify` patterns.

2. **Core file protection**: `bot.py`, `core/`, `.env`, and `.venv/` are protected from both `write_file` (via `BLOCKED_WRITE_PATHS`) and `run_command` (via `_check_bot_self_modification`). Read-only commands (`cat`, `grep`, `head`, etc.) are still allowed.

3. **System prompt instruction**: Explicit "Bot Self-Protection" section tells the LLM to never modify its own runtime files and to use separate venvs for user tasks.

**Root cause**: The LLM rebuilt its own Python venv via tool-use, deleting SSL cert paths that `httpx` needed. Every subsequent API call failed with `FileNotFoundError`. Protection is at the tool layer (not just the prompt) so it works regardless of which LLM model is running.

**Files modified:** `core/tools.py`, `bot.py`, `CONTEXT.md`
