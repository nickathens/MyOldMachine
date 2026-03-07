# MyOldMachine

Turn any old laptop into a dedicated AI assistant. One command. Full takeover.

You have an old machine collecting dust. MyOldMachine converts it into a personal AI assistant you control entirely through Telegram. It strips the bloat, installs what it needs, and runs 24/7. You never touch a terminal again.

## What It Does

- **Full machine takeover** — removes desktop environments, browsers, office suites, and other software you don't need. Installs only what the bot requires.
- **Always-on** — disables sleep, suspend, and lid-close sleep. The machine stays running.
- **Tool-use for every provider** — every LLM can run commands, read/write files, and manage processes on the machine. Not just Claude. OpenAI, Gemini, Grok, Ollama, OpenRouter — all of them execute real commands through a built-in tool layer.
- **Self-installing** — if the bot needs a tool to complete a task, it installs it automatically.
- **Self-maintaining** — health monitoring, disk alerts, automatic security updates, log rotation, attachment cleanup.
- **Self-updating** — pull the latest version from Telegram with `/update`.
- **Crash recovery** — pending messages survive crashes and are retried on restart. Task progress saved every 30 seconds.
- **LLM-agnostic** — works with Claude CLI, Claude API, OpenAI, Grok (xAI), Gemini, Ollama (free/local), or OpenRouter. 7 providers.
- **Modular skills** — weather, translation, OCR, downloads, file compression, URL summarization, and more. Each skill is a self-contained package with auto-installing dependencies.
- **Reminders & scheduling** — set reminders via natural language. SQLite-backed, survives reboots.
- **Memory system** — persistent memories, conversation compaction, project tracking. Sophisticated long-term context.
- **Remote access** — VNC/Screen Sharing enabled so you can see what the machine is doing.

## Supported Platforms

- **Ubuntu / Debian** (full support — systemd service)
- **macOS 10.14+** (full or soft takeover — launchd service)
- **Windows** (planned)

## Quick Start

### Option 1: One command

```bash
curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
```

### Option 2: Clone and run

```bash
git clone https://github.com/nickathens/MyOldMachine.git
cd MyOldMachine
./install.sh
```

The installer walks you through setup:

1. Your name
2. Telegram bot token (instructions provided)
3. Your Telegram user ID
4. LLM provider and API key
5. Bot name
6. Timezone
7. Takeover level (full or soft)
8. Sudo password (stored locally, never transmitted)

If you pick **Ollama**, the installer runs a hardware benchmark, recommends the best model your machine can handle, installs Ollama, and pulls the model automatically.

After setup, the bot sends you a welcome message on Telegram with machine specs and loaded skills. You're done.

### Running Manually vs. Installed Service

The installer registers the bot as a **system service** — this is what makes it always-on:

| | Manual (`python bot.py`) | Installed (via `install.sh`) |
|---|---|---|
| Survives reboot | No | Yes |
| Restarts on crash | No | Yes |
| Runs after closing terminal | No | Yes |
| Starts on boot | No | Yes |

**If you want the bot to run at all times, use the full installer.** Running `python bot.py` directly is only useful for testing and debugging. The installer sets up:
- **Linux:** systemd service (`myoldmachine.service`) — starts on boot, restarts on crash
- **macOS:** launchd agent (`com.myoldmachine.bot.plist`) — starts on login, restarts on crash

## What You Need

- An old laptop or desktop (any age — the bot is lightweight)
- Internet connection
- A Telegram account
- An LLM API key (or use Ollama for free local inference)

## Architecture

```
User (Telegram) → bot.py → core/llm.py (provider factory)
                                ↓
                    ┌───────────┼───────────────┐
                    │           │               │
              ClaudeCLI    OpenAI-compat    Gemini
              (native      (OpenRouter,     (native
               tools)       OpenAI, Grok,    function
                            Ollama)          calling)
                    │           │               │
                    └───────────┼───────────────┘
                                ↓
                         core/tools.py
                    ┌────────────────────┐
                    │  Process Registry  │
                    │  Env Hardening     │
                    │  Output Streaming  │
                    │  Script Preflight  │
                    │  Unified Schema    │
                    └────────────────────┘
                                ↓
                    run_command | read_file
                    write_file | list_directory
                    check_process
```

**Claude CLI** uses its own native tool-use (bash, files, web search). Every other provider goes through `core/tools.py` — a tool execution layer that intercepts structured tool calls from the LLM, executes them on the machine, and feeds results back in a loop until the model responds with text.

### Core Components

| Component | Purpose |
|-----------|---------|
| `bot.py` | Telegram handlers, message routing, system prompt, conversation management |
| `core/llm.py` | LLM provider abstraction — 7 providers, tool-use loops, streaming |
| `core/tools.py` | Tool execution layer — 5 tools, process registry, env hardening, safety |
| `core/session.py` | Per-user conversation history with smart trimming and compaction |
| `core/scheduler.py` | APScheduler + SQLite job store — reminders, commands, agent tasks |
| `core/skill_loader.py` | Auto-discovery and context injection for skills |
| `core/self_install.py` | Runtime dependency installer (apt/brew/pip/npm) |
| `core/health.py` | System health monitoring — disk, RAM, CPU, network, uptime |
| `core/updater.py` | Self-update via git pull + pip install (safe — no mid-response restart) |
| `core/config.py` | Environment-based configuration |

### Utilities

| Component | Purpose |
|-----------|---------|
| `utils/project_manager.py` | Create and track projects with state files |
| `utils/cleanup.py` | Automated cleanup — old attachments, large logs, temp files |
| `utils/scheduler_cli.py` | CLI for managing scheduled jobs outside the bot |
| `utils/safe_json.py` | Atomic JSON read/write (temp + fsync + rename) |
| `utils/send_to_telegram.py` | Send files to Telegram from scripts |

### Install System

| Component | Purpose |
|-----------|---------|
| `install.sh` | Entry point — detects OS, sets up Python, launches wizard |
| `install/wizard.py` | Interactive setup — creates .env, data dirs, memory structure |
| `install/provisioner.py` | OS-level setup — bloat removal, deps, system config |
| `install/ollama_setup.py` | Ollama auto-install, hardware benchmark, model recommendation |
| `install/os_detect.py` | Version-aware OS detection (macOS 10.14–15.x, Ubuntu/Debian) |
| `install/service.py` | Registers as systemd (Linux) or launchd (macOS) service |

## Tool-Use Execution Layer

Every LLM provider (except text-only Claude API) can execute commands on the machine through structured tool calls. The LLM never runs commands directly — it sends a structured request (e.g., `run_command("apt install ffmpeg")`), the tool layer validates it, executes it, and returns the result.

### Available Tools

| Tool | Description |
|------|-------------|
| `run_command` | Execute shell commands. Supports foreground (blocking) and background (returns process ID for polling). |
| `read_file` | Read file contents. Binary detection, 5MB size limit, 50K char truncation. |
| `write_file` | Write files. 1MB size limit. Preflight validation catches wrong-language content (e.g., shell syntax in a `.py` file). |
| `list_directory` | List directory contents with file sizes and types. |
| `check_process` | Poll background processes for new output, check status, list all, or kill by ID. |

### How It Works

1. User sends message via Telegram
2. `bot.py` builds system prompt + conversation history
3. `llm.py` sends request to LLM API with tool definitions
4. LLM returns a structured tool call (e.g., `run_command("ls -la")`)
5. `tools.py` validates safety, sanitizes environment, executes on host
6. Process tracked in registry; output streamed in chunks
7. Result appended to conversation, sent back to LLM
8. Loop repeats until the LLM returns text (not a tool call)
9. Final text response sent to user via Telegram

### Fallback Tool-Call Parser

Some models (especially free/small ones via OpenRouter or Ollama) don't emit structured tool calls — they output them as markdown code blocks or plain text instead. The tool layer includes a fallback parser (`extract_tool_calls_from_text()`) with three strategies:

1. **JSON-style** — detects `{"name": "run_command", "arguments": {...}}`
2. **Function-call syntax** — detects `run_command(command="ls")`
3. **Code block detection** — extracts tool calls from fenced code blocks

This means tool-use works even with models that don't natively support function calling — as long as the model is smart enough to follow the instructions in the system prompt.

### Process Management

- `ProcessRegistry` tracks every spawned process by UUID
- Background commands return a `process_id` immediately — the LLM can start a long `apt install`, yield, and poll for output later
- Process group kill (gets child processes too)
- Auto-cleanup of old finished processes (cap: 20)
- All processes killed on bot shutdown

### Environment Hardening

- API keys, tokens, and secrets stripped from the execution environment (`TELEGRAM_TOKEN`, `LLM_API_KEY`, `OPENAI_API_KEY`, etc.)
- Pattern-based blocking: `*_SECRET*`, `*_TOKEN`, `*_PASSWORD`, `*_KEY`, `*_CREDENTIALS*`
- Safe-list of allowed variables (HOME, PATH, LANG, locale, Python/Node paths)
- Bot's Python venv removed from PATH so commands use system Python
- `~/.local/bin` prepended for user-installed tools

## LLM Providers

| Provider | Tool Use | Local | Free | Notes |
|----------|----------|-------|------|-------|
| **claude** | Full (native) | No | No | Claude Code CLI — runs as subprocess. Most capable. Requires `claude login`. |
| **claude-api** | No | No | No | Direct Anthropic API. Text-only — conversation assistant, no machine control. |
| **openai** | Yes (via tool layer) | No | No | GPT-4o, GPT-4, etc. Full machine control. |
| **grok** | Yes (via tool layer) | No | Yes* | xAI. $25 free credits on signup, +$150/mo with data sharing. `api.x.ai/v1`. |
| **gemini** | Yes (via tool layer) | No | No | Google's models. Free tier often has zero quota — use via OpenRouter instead. |
| **ollama** | Yes (via tool layer) | Yes | Yes | Run any model locally. No API key. Auto-installs with hardware benchmark. |
| **openrouter** | Yes (via tool layer) | No | Yes* | 100+ models through one API. Free models available (50 req/day without billing). |

Set your provider in `.env`:
```
LLM_PROVIDER=claude       # or: claude-api, openai, grok, gemini, ollama, openrouter
LLM_MODEL=claude-sonnet-4-20250514
LLM_API_KEY=              # Not needed for claude (CLI) or ollama
```

### Free Options (no credit card required)

**Ollama** — runs locally, completely free, no API key. The installer benchmarks your hardware and recommends the best model:
```
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
```

Hardware recommendations:
| RAM | Recommended Model | Notes |
|-----|-------------------|-------|
| < 4GB | Not recommended | Models too small for reliable tool-use. Use OpenRouter free instead. |
| 4–7GB | Qwen2.5 3B / Gemma 2 2B | Basic tool-use, sometimes unreliable |
| 8–15GB | Llama 3.1 8B | Reliable tool-use. This is the practical floor. |
| 16GB+ | Llama 3.3 70B Q4 | Full capability |

**Grok (xAI)** — $25 free credits on signup, $150/month free with data sharing opt-in:
```
LLM_PROVIDER=grok
LLM_MODEL=grok-2-latest
LLM_API_KEY=xai-...
```

**OpenRouter free models** — cloud-hosted, free API key from [openrouter.ai](https://openrouter.ai):
```
LLM_PROVIDER=openrouter
LLM_MODEL=google/gemini-2.0-flash-001
LLM_API_KEY=sk-or-v1-...
```

Note: OpenRouter's free tier is 50 requests/day. With tool-use, a single conversation can consume 5-10 requests (one per tool call iteration). Heavy use will hit the limit.

### Changing Provider After Install

The easiest way is directly from Telegram — no SSH, no file editing, no restart:

```
/provider                                    — show current state + all options
/provider grok                               — switch provider (uses default model)
/provider openrouter google/gemini-2.0-flash-001  — switch provider + model
/model gpt-4o-mini                           — change model only
/apikey sk-abc123...                         — set API key (message auto-deleted)
```

All three commands update `.env` on disk and reload the provider in memory instantly. Admin-only.

Alternatively, edit `~/MyOldMachine/.env` directly and restart:

```bash
# Linux
sudo systemctl restart myoldmachine

# macOS
launchctl stop com.myoldmachine.bot && launchctl start com.myoldmachine.bot
```

Or send `/restart` from Telegram.

## Session Management

Each user gets isolated conversation state:

- **Smart trimming** — conversations are trimmed to stay within token limits, preserving recent context
- **Compaction** — when conversations exceed a threshold, older messages are summarized into a compact summary that's injected into the system prompt
- **Persistent memories** — `/remember` saves facts that persist across conversation resets and are always included in context
- **Daily reset** — conversations reset daily to prevent unbounded growth, with summary preserved
- **Topic sessions** — conversations can be scoped to specific topics

## Memory System

The bot maintains long-term memory under `data/memory/`:

```
data/memory/
├── projects/          # Project state files (state.json per project)
│   └── my-project/
│       └── state.json
├── topics/            # Domain knowledge (markdown files)
└── decisions/         # Decision logs with rationale
```

Manage projects via CLI:
```bash
python utils/project_manager.py create "My Project" "Summary" "/path/to/project"
python utils/project_manager.py list
python utils/project_manager.py status my-project
python utils/project_manager.py update my-project --status active --next "Do this next"
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Connect and show system info |
| `/help` | List all commands |
| `/status` | Bot status — messages, memories, skills, uptime |
| `/health` | System health — disk, RAM, CPU, network, uptime |
| `/system` | System info — version, OS, provider, tool-use status, branch |
| `/clear` | Reset conversation history |
| `/remember <fact>` | Save a persistent memory |
| `/memories` | Show saved memories |
| `/forget <n>` | Delete a memory by number |
| `/remind <time> <msg>` | Set a reminder (natural language time) |
| `/reminders` | Show active reminders |
| `/cancel <id>` | Cancel a reminder |
| `/provider` | Show current provider or switch: `/provider grok`, `/provider openrouter google/gemini-2.0-flash-001` |
| `/model` | Change model without switching provider: `/model gpt-4o-mini` |
| `/apikey` | Set API key from chat (auto-deletes your message): `/apikey sk-abc123...` |
| `/cleanup` | Run maintenance — clean old files, rotate logs |
| `/update` | Check for and pull updates |
| `/restart` | Restart the bot service |

## Skills

Skills are modular capability packages. Each skill has:
- `SKILL.md` — instructions the LLM reads to understand how to use the skill
- `deps.json` — dependency manifest (auto-installed at runtime if missing)
- `scripts/` — executable scripts the LLM can run

### Included Skills

| Skill | Description |
|-------|-------------|
| weather | Current weather and forecasts (Open-Meteo, free) |
| translate | Text translation (Google Translate, free) |
| ocr | Text extraction from images and PDFs (Tesseract) |
| compress | ZIP and TAR archive operations |
| downloads | Parallel downloads with aria2 |
| summarize | URL content fetching and summarization |

### Adding Custom Skills

Create a directory under `skills/` with:

```
skills/my-skill/
├── SKILL.md          # Instructions for the LLM
├── deps.json         # Dependencies (auto-installed)
└── scripts/
    └── my_script.py  # Your script
```

`deps.json` format:
```json
{
  "apt": ["package-name"],
  "brew": ["package-name"],
  "pip": ["package>=1.0"],
  "npm": ["package"],
  "check": {
    "binary": "binary --version"
  }
}
```

The bot auto-installs missing dependencies when the skill is first loaded.

## Docker (Alternative)

If you prefer Docker over native install:

```bash
cp .env.example .env
# Edit .env with your settings
docker compose up -d
```

Docker mode is a lighter deployment — no machine takeover, no system provisioning. The bot runs in a container alongside your existing OS. Add `restart: always` to your `docker-compose.yml` for boot persistence.

## How It Works

### Full Takeover (Linux)

1. Removes desktop environment (GNOME, KDE, etc.)
2. Removes browsers, office suites, games, media players, snap
3. Installs Python, ffmpeg, sox, Node.js, SSH, firewall
4. Configures UFW (SSH + outbound only), fail2ban, unattended upgrades
5. Disables sleep, suspend, lid-close sleep
6. Enables SSH server and VNC
7. Registers as systemd service (starts on boot, restarts on crash)
8. Stores sudo password for runtime package installation

### Full Takeover (macOS)

1. Removes non-essential apps (GarageBand, iMovie, etc.)
2. Installs dependencies via Homebrew
3. Disables sleep, screen saver
4. Enables Screen Sharing (VNC)
5. Registers as LaunchAgent (starts on login, restarts on crash)
6. Sources `.env` via bash wrapper for launchd compatibility

### Soft Takeover (macOS only)

- Installs the bot as a background service (starts on boot, restarts on crash)
- Does not remove any apps or change power settings
- You keep using your Mac normally
- The bot runs 24/7 alongside your regular workflow

## Security

- Bot runs as your user (not root)
- Sudo password stored at `~/.sudo_pass` with 600 permissions — used only for runtime package installation
- **Command safety** — blocked patterns prevent destructive commands (`rm -rf /`, `mkfs`, `dd` to disk, fork bombs, `curl | sudo bash`). Write path blocklist protects system files (`/etc/passwd`, `/etc/shadow`, `/etc/sudoers`, `/boot/`)
- **Environment hardening** — API keys, tokens, and secrets are stripped from the execution environment. The LLM never sees your credentials.
- **Limits** — 120s foreground timeout, 3600s background timeout, 50K char output cap, 25 tool iterations per request, 5MB read limit, 1MB write limit
- **Script preflight** — `write_file` validates content against file extension (catches shell syntax in `.py` files, etc.)
- Firewall: SSH + outbound only (Linux)
- fail2ban on SSH (Linux)
- Automatic security updates via unattended-upgrades (Linux)
- Telegram access restricted to `ALLOWED_USERS` — unauthorized messages are silently dropped
- `.env` and `data/` are gitignored
- Atomic file writes prevent data corruption on crash
- No credentials are transmitted — all API keys stay on the machine

## Troubleshooting

### "Quota exceeded" or "Resource has been exhausted" error with Gemini

Google's free Gemini tier frequently has zero quota. This is a Google-side issue, not a MyOldMachine bug. Options:

1. **Switch to OpenRouter with a free model** (recommended) — edit `.env`:
   ```
   LLM_PROVIDER=openrouter
   LLM_MODEL=google/gemini-2.0-flash-001
   LLM_API_KEY=sk-or-v1-YOUR_KEY
   ```
   Get a free API key at [openrouter.ai](https://openrouter.ai) (no billing required).
2. **Enable billing** on your Google AI project at [ai.google.dev](https://ai.google.dev)
3. **Use Ollama** for free local inference (no API key needed, but slower on old hardware)

### "Provider returned error" with OpenRouter

The model ID is wrong or no longer available. OpenRouter model IDs change — check [openrouter.ai/collections/free-models](https://openrouter.ai/collections/free-models) for current free models. Edit `.env` and set `LLM_MODEL` to a valid model ID from that page.

### Install hangs during Homebrew (macOS)

On older Macs (Catalina and earlier), Homebrew compiles packages from source instead of using pre-built bottles. ffmpeg in particular can take 30-60 minutes. As long as the terminal shows output, it's working. If you need to restart the install, just run `./install.sh` again — it resumes from where it left off.

### "Post-install step did not complete successfully" (Homebrew)

This is a known Homebrew issue on older macOS versions. The install script handles this automatically — it runs `brew link --overwrite` to fix the symlinks. If you see this warning, let the script continue.

### Bot not responding on Telegram

1. Check the bot is running: `sudo systemctl status myoldmachine` (Linux) or `launchctl list | grep myoldmachine` (macOS)
2. Check logs: `journalctl -u myoldmachine -n 50` (Linux) or `cat ~/MyOldMachine/data/logs/bot.log` (macOS)
3. Verify your Telegram user ID matches `ALLOWED_USERS` in `.env` (empty means anyone can use it)

### Tool-use not working with Ollama

Models below 7B parameters are unreliable at structured tool calls. They'll misformat JSON, hallucinate tool names, or forget the tools exist. If your machine only supports small models (< 4GB RAM), consider using OpenRouter's free tier instead — cloud models are larger and more reliable.

## License

MIT
