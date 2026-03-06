# MyOldMachine

Turn any old laptop into a dedicated AI assistant. One command. Full takeover.

You have an old machine collecting dust. MyOldMachine converts it into a personal AI assistant you control entirely through Telegram. It strips the bloat, installs what it needs, and runs 24/7. You never touch a terminal again.

## What It Does

- **Full machine takeover** — removes desktop environments, browsers, office suites, and other software you don't need. Installs only what the bot requires.
- **Always-on** — disables sleep, suspend, and lid-close sleep. The machine stays running.
- **Self-installing** — if the bot needs a tool to complete a task, it installs it automatically.
- **Self-maintaining** — health monitoring, disk alerts, automatic security updates.
- **Self-updating** — pull the latest version from Telegram with `/update`.
- **LLM-agnostic** — works with Claude, OpenAI, Gemini, Ollama (free/local), or OpenRouter.
- **Modular skills** — weather, translation, OCR, downloads, file compression, URL summarization, and more. Each skill is a self-contained package.
- **Reminders & scheduling** — set reminders via Telegram. SQLite-backed, survives reboots.
- **Remote access** — VNC/Screen Sharing enabled so you can see what the machine is doing.

## Supported Platforms

- **Ubuntu / Debian** (full support)
- **macOS** (full or soft takeover)
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

The installer will walk you through setup:

1. Your name
2. Telegram bot token (instructions provided)
3. Your Telegram user ID
4. LLM provider and API key
5. Bot name
6. Timezone
7. Takeover level (full or soft)
8. Sudo password (stored locally, never transmitted)

After setup, the bot sends you a message on Telegram. You're done.

## What You Need

- An old laptop or desktop (any age — the bot is lightweight)
- Internet connection
- A Telegram account
- An LLM API key (or use Ollama for free local inference)

## Architecture

```
User (Telegram) → Bot (Python) → LLM (any provider) → System (full root access)
                                                     → Skills (modular capabilities)
                                                     → Scheduler (reminders)
                                                     → Health (monitoring)
```

### Core Components

| Component | Purpose |
|-----------|---------|
| `bot.py` | Telegram handlers, message routing, system prompt |
| `core/llm.py` | LLM provider abstraction (Claude, OpenAI, Gemini, Ollama, OpenRouter) |
| `core/session.py` | Per-user conversation history with smart trimming |
| `core/scheduler.py` | SQLite-backed reminder system |
| `core/skill_loader.py` | Auto-loads skills from `skills/` directory |
| `core/self_install.py` | Runtime dependency installer |
| `core/health.py` | System health monitoring (disk, RAM, CPU, uptime) |
| `core/updater.py` | Self-update via git pull + service restart |

### Install System

| Component | Purpose |
|-----------|---------|
| `install.sh` | Entry point — detects OS, sets up Python, launches wizard |
| `install/wizard.py` | Interactive setup conversation |
| `install/provisioner.py` | OS-level setup: bloat removal, dep installation, system config |
| `install/service.py` | Registers as systemd (Linux) or launchd (macOS) service |

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Connect and show system info |
| `/help` | List all commands |
| `/status` | Bot status (messages, memories, skills) |
| `/health` | System health report (disk, RAM, uptime) |
| `/system` | System info (version, OS, provider) |
| `/clear` | Reset conversation history |
| `/remember <fact>` | Save a persistent memory |
| `/memories` | Show saved memories |
| `/forget <n>` | Delete a memory |
| `/remind <time> <msg>` | Set a reminder |
| `/reminders` | Show active reminders |
| `/cancel <id>` | Cancel a reminder |
| `/update` | Update to latest version |
| `/restart` | Restart the bot service |

## Skills

Skills are modular capability packages. Each skill has:
- `SKILL.md` — instructions for the LLM
- `deps.json` — dependency manifest (auto-installed if missing)
- `scripts/` — executable scripts

### Included Skills

| Skill | Description |
|-------|-------------|
| weather | Current weather and forecasts (Open-Meteo, free) |
| translate | Text translation (Google Translate, free) |
| ocr | Text extraction from images and PDFs (Tesseract) |
| compress | ZIP and TAR archive operations |
| downloads | Parallel downloads with aria2 |
| summarize | URL content fetching and summarization |

More skills will be ported from the reference implementation.

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

The bot will auto-install missing dependencies when the skill is first used.

## Docker (Alternative)

If you prefer Docker over native install:

```bash
cp .env.example .env
# Edit .env with your settings
docker compose up -d
```

Note: Docker mode is a lighter deployment — no machine takeover, no system provisioning. The bot runs in a container alongside your existing OS.

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

### Soft Takeover (macOS only)

- Installs the bot as a background service
- Does not remove any apps or change power settings
- You keep using your Mac normally

## Security

- Bot runs as your user (not root)
- Sudo password stored at `~/.sudo_pass` with 600 permissions
- Firewall: SSH + outbound only (Linux)
- fail2ban on SSH (Linux)
- Automatic security updates (Linux)
- Telegram access restricted to `ALLOWED_USERS`
- `.env` and `data/` are gitignored

## License

Private. Not yet open source.
