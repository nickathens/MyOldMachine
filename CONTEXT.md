# MyOldMachine — Project Context

Last updated: 2026-03-07

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
                            Ollama)          calling)
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
9. Final text sent to user via Telegram

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
  health.py         — Health check endpoint
install/
  wizard.py         — Interactive setup (provider, API key, Telegram token)
  provisioner.py    — OS-level provisioning (disable sleep, auto-login, etc.)
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

## Safety Layer

- **Blocked commands:** `rm -rf /`, `mkfs`, `dd` to disk, fork bombs, `mv /`, `rm -rf /etc`, `curl|sudo bash`, `wget|sudo bash`
- **Write path blocklist:** `/etc/passwd`, `/etc/shadow`, `/etc/sudoers`, `/etc/sudoers.d/`, `/boot/`, `/boot/grub/`, `/etc/crontab`, `/var/spool/cron/`
- **Limits:** 120s foreground timeout, 3600s background timeout, 50K char output cap, 25 tool iterations per request
- **Environment:** Sanitized (no leaked secrets), clean PATH per-OS, no bot venv leakage

## LLM Providers

| Provider | Tool-Use | Notes |
|----------|----------|-------|
| Claude CLI | Native | Full tool-use built into Claude's runtime |
| OpenRouter | OpenAI-compat | Free models available. Default: `google/gemini-2.0-flash-001` |
| OpenAI | OpenAI-compat | Requires paid API key |
| Gemini | Native | Free tier has zero quota issues. Use via OpenRouter instead |
| Ollama | OpenAI-compat | Local, free, no API key. Slow on old hardware |

## Known Issues

- Google free tier quota can change without notice, breaking Gemini models
- OpenRouter free model IDs can go stale — verify against their API
- macOS launchd service registration is fragile — `launchctl kickstart -k` is more reliable than unload/load
- Old Macs compile ffmpeg from source (~30-60 min)

## Testing

Tested on:
- **macOS Catalina 10.15.7** (Intel, user "mtsikala") — Gemini via OpenRouter
- Issues found and fixed: quota exhaustion, stale model IDs, verbose text dumps, restart race condition
- **tools.py integration tests:** All 5 subsystems verified (process mgmt, env hardening, unified schema, streaming, preflight)
