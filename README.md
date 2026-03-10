# MyOldMachine

You have an old laptop sitting in a drawer. This turns it into a personal AI assistant you talk to through Telegram. It runs 24/7, does what you tell it, and gets smarter as you add skills.

## The idea

Old machines are all different. A 2012 MacBook Air is not a 2015 ThinkPad is not a Raspberry Pi 4. No installer can predict every configuration. MyOldMachine handles the basics — dependencies, service registration, LLM setup — and then **you and the bot figure out the rest together.**

Once it's running, you talk to it on Telegram. Ask it to install things. Ask it to set up skills. Ask it to fix what's broken. It has full access to the machine and it can run commands, read files, install packages. If something doesn't work on your hardware, tell the bot what happened and work through it. That's the workflow.

This is not a polished consumer product. It's a toolkit. You shape it.

## What it does

- **Always-on** — runs as a system service, survives reboots and crashes
- **Full machine access** — the bot can run commands, read/write files, install software, manage processes
- **LLM-agnostic** — works with Claude, OpenAI, DeepSeek, Grok, Gemini, Ollama (local/free), or OpenRouter. 8 providers, switch anytime from Telegram
- **Self-installing dependencies** — skills auto-install what they need on first use
- **37 skills** — Blender, GIMP, Inkscape, browser automation, audio/video editing, web scraping, cloud sync, and more
- **Reminders & scheduling** — natural language, SQLite-backed, survives reboots
- **Deep memory** — per-user person models that evolve through observations and nightly reflection. The bot learns how each user communicates, what they prefer, and what they're working on
- **Memory** — persistent facts, conversation history, project tracking

## Install modes

The installer gives you three choices:

### Full Workstation (recommended)

Your desktop stays intact. The bot installs creative and productivity apps alongside itself: Blender, GIMP, Inkscape, LibreOffice, ImageMagick, rclone. You can sit at the machine and use it normally while also controlling it through Telegram. This is the mode that makes your old machine most useful — the bot can open apps, edit files, render 3D scenes, process images, build websites.

### Minimal

The bot runs as a background service. Your existing apps and settings stay untouched. Skills self-install their dependencies on first use. Good if you want to keep the machine exactly as it is and let the bot grow into it gradually.

### Headless Server

Strips the desktop environment, disables sleep, turns the machine into a dedicated bot appliance. You interact with it only through Telegram or SSH. Frees up RAM and CPU for the bot. Good for machines you'll never sit in front of again.

## Supported platforms

### Linux (systemd service)
- **Debian / Ubuntu** — apt
- **Fedora / RHEL / CentOS / Amazon Linux** — dnf / yum
- **Arch / Manjaro / EndeavourOS** — pacman
- **openSUSE** — zypper
- **Alpine** — apk

Any Linux distribution with one of these package managers will work. The installer auto-detects your distro and uses the right package manager.

### macOS (launchd service)
- **macOS 10.14+** — Homebrew preferred, with direct-download fallbacks for ffmpeg and Node.js when Homebrew fails or builds from source are too slow

### Other
- **Windows** (planned)

### Software compatibility

Not every machine can run the latest version of every app. The installer handles this automatically:

1. **System package manager first** — tries `apt install`, `dnf install`, `brew install`, etc.
2. **Flatpak fallback (Linux)** — if the system package fails (wrong version, missing from repos, too old), it tries Flatpak. Flatpak apps are sandboxed and version-independent — a 2016 Ubuntu machine can run the latest Blender via Flatpak even if `apt install blender` would give you Blender 2.79.
3. **Direct binary downloads (macOS)** — for ffmpeg and Node.js, downloads pre-built binaries when Homebrew can't build them on old macOS.
4. **Report what failed** — anything that couldn't be installed is reported with actionable guidance. The bot can help troubleshoot after setup.

This means:
- An old Ubuntu 18.04 laptop can still get modern Blender, GIMP, and Inkscape via Flatpak
- A Fedora machine that doesn't have Blender in its repos gets it from Flathub
- A macOS Catalina Mac that can't build ffmpeg via Homebrew gets a static binary instead
- If nothing works for a specific app, you know exactly why and what to try next

### Hardware notes
- Ollama (local AI) requires macOS 12+ or a modern Linux kernel
- Playwright (browser skill) needs enough RAM for Chromium — on non-Debian systems, you may need to install browser dependencies manually
- On first boot, the bot probes the system and reports which skills are ready vs. which need dependencies installed
- Apps installed via Flatpak are detected by the system probe and reported alongside system-installed tools

When something doesn't work, the bot is your first line of support. Tell it what happened. It has the context, the logs, and the ability to fix things on the machine.

## Quick start

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

The installer walks you through:

1. Your name
2. Telegram bot token (with step-by-step instructions)
3. Your Telegram user ID
4. LLM provider — shows which are free, which cost money
5. Model selection — recommended models per provider
6. Bot name and timezone
7. Install mode — workstation, minimal, or headless
8. Sudo password (stored locally, never transmitted)

After setup, the bot messages you on Telegram with machine specs and loaded skills.

## Choosing a provider

The installer shows you all options with clear free/paid labels:

| Provider | Free? | Local? | Notes |
|----------|-------|--------|-------|
| **Claude CLI** | With Pro/Max plan | No | Most capable. Uses your existing subscription. |
| **Claude API** | No | No | Pay-per-token. Text-only (no machine control). |
| **OpenAI** | No | No | GPT-5.4, GPT-4.1. Vision + full machine control. |
| **DeepSeek** | No (very cheap) | No | V3.2. $0.28/$0.42 per MTok. 128K context. Full machine control. |
| **Grok (xAI)** | $25 free credits | No | Grok 4.1 Fast: $0.20/$0.50. Vision + machine control. |
| **Gemini** | Limited free tier | No | 2.5 Flash: 10 RPM / 250 RPD free. Machine control. |
| **Ollama** | Yes | Yes | Run models locally. No API key. Requires macOS 12+ or modern Linux. |
| **OpenRouter** | Yes (200 req/day) | No | 17+ free models with tool-use. |

Switch providers anytime from Telegram — no SSH needed:
```
/provider openai gpt-5.4
/model gpt-5-mini
/apikey sk-abc123...
```

## Skills

Skills are modular packages the bot loads automatically. Each has instructions the LLM reads, optional scripts, and a dependency manifest.

### Core skills (all install modes)

| Skill | What it does |
|-------|-------------|
| browser | Full headless browser — navigate, click, fill forms, screenshot, extract content |
| weather | Current weather and forecasts |
| translate | Text translation |
| ocr | Text extraction from images and PDFs |
| compress | ZIP/TAR archives |
| downloads | Parallel downloads with aria2 |
| summarize | Fetch and summarize URLs |
| pdf | Merge, split, extract text from PDFs |
| image-editing | Resize, crop, rotate, filters, format conversion |
| audio-editing | Cut, merge, fade, convert audio |
| video-editing | Cut, merge, text overlays, format conversion |
| audio-analysis | BPM, key detection, loudness analysis |
| color-palette | Extract and generate color palettes |
| text-to-speech | Generate spoken audio from text |
| font-tools | Font conversion and subsetting |
| git | Version control with Git and GitHub CLI |
| database | SQLite operations |
| api-test | HTTP API testing with curl |
| docs | Convert documents to markdown |
| qrcode | Generate QR codes |
| rss | Parse RSS/Atom feeds |
| regex | Pattern matching and text extraction |

### Workstation skills (pre-installed in workstation mode, self-install in others)

| Skill | What it does |
|-------|-------------|
| blender | 3D modeling, rendering, animation via Blender's Python API |
| gimp | Image editing and manipulation via GIMP's Script-Fu |
| inkscape | Vector graphics creation and manipulation via Inkscape CLI |
| spreadsheet | Create, edit, export Excel/ODS spreadsheets (LibreOffice + openpyxl) |
| scraper | Web scraping with Playwright — handles JavaScript-rendered pages |
| media | Screenshots and video recording of web pages |
| icon-gen | Generate favicons, app icons, and icon sets |
| sprite-gen | Create sprites and sprite sheets for games |
| code-scaffold | Generate project templates and boilerplate |
| charts | Terminal bar and line charts |
| notes | Notes and knowledge management with nb |
| bookmarks | Bookmark management with buku |
| cloud-sync | Sync files to cloud storage (Google Drive, Dropbox, S3) via rclone |
| web-build | Static site generation and web development |
| upscale | AI-powered image upscaling with Real-ESRGAN |

### Adding your own skills

Create a directory under `skills/`:

```
skills/my-skill/
├── SKILL.md          # Instructions for the LLM
├── deps.json         # Dependencies (auto-installed)
└── scripts/
    └── my_script.py  # Your script
```

The bot reads `SKILL.md` to learn how to use the skill. Dependencies install automatically on first use.

## Working with your machine

Every old machine has quirks. Here's how this is meant to work:

**Something doesn't install?** Tell the bot. It can try alternative packages, compile from source, or find workarounds for your specific OS version.

**A skill doesn't work?** Send the error to the bot. It can read logs, check versions, and fix configurations.

**Need something custom?** Ask the bot to write a new skill. It can create the SKILL.md, the script, the deps.json, and test it — all from Telegram.

**Want to add more capabilities?** The skill system is designed for this. Browser automation, image processing, audio editing — tell the bot what you need and work through the setup together.

The bot has `run_command`, `read_file`, `write_file`, `list_directory`, and `check_process` as tools. It can do anything you could do at a terminal.

## Architecture

```
User (Telegram) → bot.py → core/llm.py (provider factory)
                                ↓
                    ┌───────────┼───────────────┐
                    │           │               │
              ClaudeCLI    OpenAI-compat    Gemini
              (native      (OpenRouter,     (native
               tools)       OpenAI, Grok,    function
                            DeepSeek,        calling)
                            Ollama)
                    │           │               │
                    └───────────┼───────────────┘
                                ↓
                         core/tools.py
                    ┌────────────────────┐
                    │  run_command       │
                    │  read_file         │
                    │  write_file        │
                    │  list_directory    │
                    │  check_process     │
                    └────────────────────┘
```

## Custom shortcuts

Define personal aliases for common tasks:

```
/alias set disk Check disk usage and alert if above 80%
/alias set backup Run my backup script at ~/backup.sh
/alias set weather What's the weather in Athens?
```

Then just type `/disk`, `/backup`, or `/weather`. The alias text is sent to the AI as if you typed it — so it works with full tool-use.

Manage shortcuts:
- `/alias` — list all your shortcuts
- `/alias set <name> <text>` — create or update
- `/alias remove <name>` — delete

## Health monitoring

The bot checks system health every 4 hours and alerts you via Telegram if:

- Disk space drops below 5 GB (warning) or 2 GB (critical)
- RAM usage exceeds 90% (warning) or 95% (critical)
- Swap usage exceeds 80%
- CPU load is sustained above 95%
- Internet connectivity is lost

Alerts have a 4-hour cooldown — you won't get spammed. You can also check manually with `/health`, which shows disk, RAM, swap, CPU, network, and uptime.

## Deep memory system

The bot builds an evolving understanding of each user over time. This is not a flat list of facts — it's a structured person model that captures behavioral patterns, communication preferences, current priorities, and relationship dynamics.

### How it works

1. **Observations** — during conversations, the bot notices things worth remembering: corrections, preferences, behavioral patterns, project context. It saves these as append-only entries.

2. **Person model** — a structured profile for each user (~500 words) covering identity, preferences, behavioral patterns, current state, and relationship dynamics.

3. **Nightly reflection** — a scheduled job runs at 3 AM, analyzes recent observations, and updates each user's person model. It deduplicates, synthesizes patterns, and discards noise.

### Tier-aware

The system adapts to the model running it:

- **Strong models** (Claude, GPT-4, DeepSeek, Grok, Gemini, large Ollama 70B+): Full mode — observations, nightly reflection, model updates. The LLM has enough reasoning ability to synthesize behavioral data meaningfully.

- **Weak models** (small Ollama, free OpenRouter, etc.): Lite mode — observations accumulate as raw entries, loaded directly into context. No reflection loop. Still useful — the bot remembers facts and corrections — but without the pattern synthesis.

### Multi-user

Each Telegram user gets their own person model, observations, and memory directory. A family sharing one machine gets individual contexts. Privacy note: all data lives as files on disk, readable by anyone with SSH access.

### What gets remembered

| Observation type | Example |
|------------------|---------|
| `behavioral` | "Prefers short, direct answers" |
| `correction` | "Got the timezone wrong — user is in Athens, not London" |
| `preference` | "Always wants audio files as MP3, not WAV" |
| `state` | "Currently focused on a job application deadline" |
| `project` | "Working on a website redesign for their portfolio" |
| `factual` | "User's timezone is Europe/Athens" |
| `relationship` | "User expressed frustration with verbose responses" |

The bot saves observations automatically during conversations — no user action needed. Users can also use `/remember` for explicit facts.

## Telegram commands

| Command | Description |
|---------|-------------|
| `/start` | Connect and show system info |
| `/help` | List all commands |
| `/status` | Messages, memories, skills, uptime |
| `/health` | Disk, RAM, swap, CPU, network |
| `/system` | Version, OS, provider, tool-use status |
| `/clear` | Reset conversation |
| `/remember <fact>` | Save a persistent memory |
| `/memories` | Show saved memories |
| `/forget <n>` | Delete a memory |
| `/remind <time> <msg>` | Set a reminder |
| `/reminders` | Show active reminders |
| `/cancel <id>` | Cancel a reminder |
| `/alias` | Manage custom command shortcuts |
| `/provider` | Show/switch LLM provider |
| `/model` | Change model |
| `/apikey` | Set API key (auto-deletes message) |
| `/cleanup` | Clean old files, rotate logs |
| `/update` | Pull latest updates |
| `/restart` | Restart the bot |

## Security

- Bot runs as your user (not root)
- API keys and tokens stripped from the execution environment
- Command blocking for destructive patterns (`rm -rf /`, `mkfs`, fork bombs)
- Write path blocklist protects system files
- Telegram access restricted to your user ID
- Sudo password stored with 600 permissions, used only for package installation
- Atomic file writes prevent corruption on crash

## Troubleshooting

The bot is your primary troubleshooting tool. But here are common issues:

**Homebrew slow/broken on old macOS:** Homebrew compiles from source on older systems. This is normal and can be slow. The installer downloads ffmpeg and Node.js directly when Homebrew fails.

**"Ollama is not compatible":** Ollama needs macOS 12+. On older Macs, use OpenRouter (free) or another cloud provider.

**Bot not responding:** Check service status — `sudo systemctl status myoldmachine` (Linux) or `launchctl list | grep myoldmachine` (macOS). Check logs in `data/logs/bot.log`.

**Non-Debian Linux — browser skill not working:** Playwright's `install-deps` only supports apt-based systems. On Fedora, Arch, etc., install Chromium through your package manager (`dnf install chromium`, `pacman -S chromium`), then the browser skill should work.

**Skills showing "not ready":** Run `/update` to re-probe system capabilities. The bot detects which tools and libraries are installed. Missing dependencies install automatically on first skill use, or you can install them manually and re-probe.

**Workstation apps not installed:** If you chose minimal or headless mode and want desktop apps later, tell the bot. It can install Blender, GIMP, Inkscape, etc. on demand — either through the system package manager or via Flatpak.

**Flatpak apps not launching from CLI:** Flatpak apps don't always create wrappers in PATH. Use `flatpak run org.blender.Blender` instead of just `blender`. The bot knows how to handle this.

**Tool-use not working with small models:** Models under 7B parameters are unreliable at structured tool calls. Use OpenRouter's free tier for cloud models if your machine can only run small local ones.

**Something else entirely:** That's the point. Every machine is different. Start the bot, describe the problem, and work through it together.

## License

MIT
