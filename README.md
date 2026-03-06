# MyOldMachine

Turn any old laptop into a dedicated AI assistant. One command. Full takeover.

You have an old machine collecting dust. MyOldMachine converts it into a personal AI assistant you control entirely through Telegram. It strips the bloat, installs what it needs, and runs 24/7. You never touch a terminal again.

## What It Does

- **Full machine takeover** — removes desktop environments, browsers, office suites, and other software you don't need. Installs only what the bot requires.
- **Always-on** — disables sleep, suspend, and lid-close sleep. The machine stays running.
- **Self-installing** — if the bot needs a tool to complete a task, it installs it automatically.
- **Self-maintaining** — health monitoring, disk alerts, automatic security updates, log rotation, attachment cleanup.
- **Self-updating** — pull the latest version from Telegram with `/update`.
- **Crash recovery** — pending messages survive crashes and are retried on restart. Task progress saved every 30 seconds.
- **LLM-agnostic** — works with Claude CLI (full tool-use), Claude API, OpenAI, Gemini, Ollama (free/local), or OpenRouter.
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

After setup, the bot sends you a welcome message on Telegram with machine specs and loaded skills. You're done.

## What You Need

- An old laptop or desktop (any age — the bot is lightweight)
- Internet connection
- A Telegram account
- An LLM API key (or use Ollama for free local inference)

## Architecture

```
User (Telegram) → Bot (Python) → LLM (any provider) → System (full access)
                                                     → Skills (modular capabilities)
                                                     → Scheduler (reminders, jobs)
                                                     → Health (monitoring, alerts)
                                                     → Memory (projects, decisions)
                                                     → Cleanup (auto-maintenance)
```

### Core Components

| Component | Purpose |
|-----------|---------|
| `bot.py` | Telegram handlers, message routing, system prompt, conversation management |
| `core/llm.py` | LLM provider abstraction — 6 providers, streaming support |
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
| `install/os_detect.py` | Version-aware OS detection (macOS 10.14–15.x, Ubuntu/Debian) |
| `install/service.py` | Registers as systemd (Linux) or launchd (macOS) service |

## LLM Providers

| Provider | Tool Use | Local | Free | Notes |
|----------|----------|-------|------|-------|
| **claude** | Full (bash, files, web) | No | No | Claude Code CLI — runs `claude` subprocess. Most capable. Requires `claude login`. |
| **claude-api** | No | No | No | Direct Anthropic API. Text-only, fast, reliable. |
| **openai** | No | No | No | GPT-4o, GPT-4, etc. |
| **gemini** | No | No | No | Google's models. Free tier often has zero quota — consider OpenRouter or Ollama instead. |
| **ollama** | No | Yes | Yes | Run any model locally. No API key needed. |
| **openrouter** | No | No | Yes* | Access 100+ models through one API. ~25 free models available (no billing). |

Set your provider in `.env`:
```
LLM_PROVIDER=claude       # or: claude-api, openai, gemini, ollama, openrouter
LLM_MODEL=claude-sonnet-4-20250514
LLM_API_KEY=              # Not needed for claude (CLI) or ollama
```

### Free Options (no billing required)

**Ollama** — runs locally, completely free, no API key. Good for machines with 8GB+ RAM:
```
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
```

**OpenRouter free models** — cloud-hosted, free API key from [openrouter.ai](https://openrouter.ai):
```
LLM_PROVIDER=openrouter
LLM_MODEL=google/gemini-2.0-flash-001
LLM_API_KEY=sk-or-v1-...
```

Free models on OpenRouter (no billing required, verified March 2026):
| Model | Notes |
|-------|-------|
| `google/gemini-2.0-flash-001` | Fast, capable, free (recommended) |
| `google/gemini-2.5-flash-lite` | Latest Google, lightweight |
| `openai/gpt-4o-mini` | OpenAI's free compact model |
| `deepseek/deepseek-v3.2-20251201` | Strong reasoning, free |
| `qwen/qwen3-235b-a22b-thinking-2507` | Large, good for complex tasks |

Full list: [openrouter.ai/collections/free-models](https://openrouter.ai/collections/free-models)

### Changing Provider After Install

Edit `~/MyOldMachine/.env` on the machine and change the `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY` values. Then restart the bot:

```bash
# Linux
sudo systemctl restart myoldmachine

# macOS
launchctl unload ~/Library/LaunchAgents/com.myoldmachine.bot.plist
launchctl load ~/Library/LaunchAgents/com.myoldmachine.bot.plist
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
| `/system` | System info — version, OS, provider, branch |
| `/clear` | Reset conversation history |
| `/remember <fact>` | Save a persistent memory |
| `/memories` | Show saved memories |
| `/forget <n>` | Delete a memory by number |
| `/remind <time> <msg>` | Set a reminder (natural language time) |
| `/reminders` | Show active reminders |
| `/cancel <id>` | Cancel a reminder |
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

Docker mode is a lighter deployment — no machine takeover, no system provisioning. The bot runs in a container alongside your existing OS.

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

- Installs the bot as a background service
- Does not remove any apps or change power settings
- You keep using your Mac normally

## Security

- Bot runs as your user (not root)
- Sudo password stored at `~/.sudo_pass` with 600 permissions — used only for runtime package installation
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

## License

MIT
