# MyOldMachine

Turn an old laptop into a personal AI assistant you control through Telegram.

One command installs everything. You talk to it on Telegram. It can run commands, edit files, install software, process images, edit audio and video, browse the web, set reminders, and more — all from your phone.

## Quick start

### What you need

- An old laptop or desktop (Linux or macOS 10.14+)
- Internet connection
- A Telegram account (free — [download here](https://telegram.org))

That's it. The installer handles everything else.

### Install

**Option 1: One command**

```bash
curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
```

**Option 2: Clone and run**

```bash
git clone https://github.com/nickathens/MyOldMachine.git
cd MyOldMachine
./install.sh
```

### What the installer does

The setup wizard walks you through 6 steps:

1. Your name
2. Telegram bot token (with instructions — takes 2 minutes via [@BotFather](https://t.me/BotFather))
3. Your Telegram user ID (the wizard tells you how to find it)
4. Which AI provider to use (free options available — you can change later)
5. Bot name and timezone
6. Install mode — workstation, minimal, or headless

After setup, the bot messages you on Telegram. Close the laptop lid if you want — it stays running. If the machine reboots, the bot starts automatically.

### Resuming a failed install

If the install is interrupted (power loss, SSH disconnect, etc.), just run the command again. It resumes from where it left off.

## What can it do?

Talk to it like you would any AI assistant — but this one has full access to a real computer. Some examples:

- "Resize all images in ~/photos to 800px wide"
- "What's the weather in Athens?"
- "Remind me to call the dentist tomorrow at 10am"
- "Download this video: [url]"
- "Make a QR code for my website"
- "Summarize this article: [url]"
- "Convert this PDF to text"
- "Check how much disk space I have"

Send it files and it can process them. Send a photo and ask it to resize or convert it. Send audio and ask for the BPM. Send a document and ask for a summary.

## Choosing an AI provider

You pick your AI provider during setup. You can switch anytime from Telegram — no SSH needed.

| Provider | Free? | Notes |
|----------|-------|-------|
| **Ollama** | Yes | Runs AI locally on your machine. No API key needed. Needs macOS 12+ or modern Linux. |
| **OpenRouter** | Yes (200 req/day) | 19+ free models with tool-use. Easy to start with. |
| **Gemini** | Limited free tier | Google's AI. Flash: 10 RPM / 250 RPD. Pro: 5 RPM / 100 RPD. |
| **Grok** | $25 free credits | xAI's models. Vision on 4.1 Fast and 4.20. |
| **DeepSeek** | No (very cheap) | $0.28 per million tokens input. Great value. |
| **OpenAI** | No | GPT-5.4, GPT-5 Mini/Nano, GPT-4.1. Vision + tools. |
| **Claude CLI** | With Pro/Max plan | Most capable. Uses your existing Anthropic subscription. |
| **Claude API** | No | Pay-per-token. Text-only (no machine control). |

**If you want free:** Start with Ollama (local, unlimited) or OpenRouter (cloud, 200 req/day).

**If you want the best quality:** Claude CLI with a Pro subscription, or OpenAI GPT-5.4.

**If you want cheap and good:** DeepSeek at $0.28/$0.42 per million tokens.

Switch providers anytime:
```
/provider openai gpt-5.4
/model gpt-5-mini
/apikey sk-abc123...
```

## Install modes

The installer gives you three choices:

### Full Workstation (recommended)

Installs creative and productivity apps alongside the bot: Blender, GIMP, Inkscape, LibreOffice, ImageMagick, rclone. Your desktop stays intact — you can still use the machine normally while controlling it through Telegram. This gives you the most capabilities.

### Minimal

The bot runs as a background service. Your existing apps and settings stay untouched. Skills install their own dependencies when you first use them. Good if you want to keep the machine as-is and add capabilities gradually.

### Headless Server

Strips the desktop, disables sleep, turns the machine into a dedicated bot appliance. You interact with it only through Telegram or SSH. Frees up resources. Good for machines you'll never sit in front of again.

## Telegram commands

| Command | What it does |
|---------|-------------|
| `/start` | Connect and show system info |
| `/help` | List all commands |
| `/status` | Messages, memories, skills, uptime |
| `/health` | Disk, RAM, CPU, network report |
| `/system` | Version, OS, provider info |
| `/clear` | Reset conversation |
| `/remember <fact>` | Save something the bot should always know |
| `/memories` | Show saved memories |
| `/forget <n>` | Delete a memory by number |
| `/remind <time> <msg>` | Set a reminder ("tomorrow 9am", "in 30 minutes") |
| `/reminders` | Show active reminders |
| `/cancel <id>` | Cancel a reminder |
| `/schedule <time> \| <task>` | Schedule an AI task to run later |
| `/jobs` | Show all scheduled jobs |
| `/topic <name>` | Switch to a named conversation topic |
| `/topics` | List all topics |
| `/recover` | Show interrupted task |
| `/clear_recovery` | Delete recovery data |
| `/alias` | Manage custom command shortcuts |
| `/provider` | Show or switch AI provider |
| `/model` | Change AI model |
| `/apikey` | Set API key (message auto-deletes) |
| `/cleanup` | Clean old files, rotate logs |
| `/update` | Pull latest updates |
| `/restart` | Restart the bot |

## Custom shortcuts

Define your own commands for things you do often:

```
/alias set disk Check disk usage and alert if above 80%
/alias set weather What's the weather in Athens?
/alias set backup Run my backup script at ~/backup.sh
```

Then just type `/disk`, `/weather`, or `/backup`.

## Troubleshooting

The bot itself is your primary troubleshooting tool. Tell it what went wrong and it can usually fix it.

**Install fails partway through?** Run the install command again. It has a checkpoint system and resumes from where it stopped.

**Bot not responding?** Check the service: `sudo systemctl status myoldmachine` (Linux) or `launchctl list | grep myoldmachine` (macOS). Logs are in `data/logs/bot.log`.

**OpenRouter "rate limit" errors?** Free models have a 200 req/day limit, and tool-use consumes 5-6 requests per user message. Switch to a model with higher limits or add billing.

**Gemini "quota exhausted"?** Google's free tier quotas reset daily at midnight Pacific. Use Flash-Lite (1000 RPD) instead of Pro (100 RPD) for higher limits.

**A skill doesn't work?** Send the error to the bot. It can read logs, check versions, and fix configurations.

**"Ollama is not compatible"?** Ollama needs macOS 12+. Use OpenRouter (free) or another cloud provider instead.

**Homebrew slow on old Mac?** Normal — Homebrew compiles from source on older systems. The installer downloads ffmpeg and Node.js directly when Homebrew can't handle it.

**Something else?** Every machine is different. Start the bot, describe the problem, and work through it together. That's how this project is designed to work.

---

# Advanced

Everything below is for people who want to understand the internals, extend the system, or contribute.

## The idea

Old machines are all different. A 2012 MacBook Air is not a 2015 ThinkPad is not a Raspberry Pi 4. No installer can predict every configuration. MyOldMachine handles the basics — dependencies, service registration, LLM setup — and then you and the bot figure out the rest together.

This is not a polished consumer product. It's a toolkit. You shape it.

## Supported platforms

### Linux (systemd service)
- **Debian / Ubuntu** — apt
- **Fedora / RHEL / CentOS / Amazon Linux** — dnf / yum
- **Arch / Manjaro / EndeavourOS** — pacman
- **openSUSE** — zypper
- **Alpine** — apk

Any Linux distribution with one of these package managers will work. The installer auto-detects your distro.

### macOS (launchd service)
- **macOS 10.14+** — Homebrew preferred, with direct-download fallbacks for ffmpeg and Node.js when Homebrew fails or builds from source are too slow

### Other
- **Windows** (planned)

### Software compatibility

The installer handles version mismatches automatically:

1. **System package manager first** — tries `apt install`, `dnf install`, `brew install`, etc.
2. **Flatpak fallback (Linux)** — if the system package fails, it tries Flatpak. Flatpak apps are sandboxed and version-independent — a 2016 Ubuntu machine can run the latest Blender via Flatpak.
3. **Direct binary downloads (macOS)** — for ffmpeg and Node.js, downloads pre-built binaries when Homebrew can't build them on old macOS.
4. **Report what failed** — anything that couldn't be installed is reported with actionable guidance.

### Hardware notes
- Ollama (local AI) requires macOS 12+ or a modern Linux kernel
- Playwright (browser skill) needs enough RAM for Chromium — on non-Debian systems, you may need to install browser dependencies manually
- On first boot, the bot probes the system and reports which skills are ready vs. which need dependencies installed
- Flatpak apps are detected by the system probe and reported alongside system-installed tools

## Skills (37 total)

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

## Deep memory system

The bot builds an evolving understanding of each user over time. This is not a flat list of facts — it's a structured person model that captures behavioral patterns, communication preferences, current priorities, and relationship dynamics.

### How it works

1. **Observations** — during conversations, the bot notices things worth remembering: corrections, preferences, behavioral patterns, project context. It saves these as append-only entries.

2. **Person model** — a structured profile for each user (~500 words) covering identity, preferences, behavioral patterns, current state, and relationship dynamics.

3. **Nightly reflection** — a scheduled job runs at 3 AM, analyzes recent observations, and updates each user's person model. It deduplicates, synthesizes patterns, and discards noise.

### Tier-aware

The system adapts to the model running it:

- **Strong models** (Claude, GPT-4+, DeepSeek, Grok, Gemini, large Ollama 70B+): Full mode — observations, nightly reflection, model updates. The LLM has enough reasoning ability to synthesize behavioral data meaningfully.

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

The bot saves observations automatically during conversations. Users can also use `/remember` for explicit facts.

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

The bot has 5 tools: `run_command` (execute shell commands, foreground or background), `read_file`, `write_file`, `list_directory`, and `check_process` (poll or kill background processes). Through these tools, it can do anything you could do at a terminal.

## Health monitoring

The bot checks system health every 4 hours and alerts you via Telegram if:

- Disk space drops below 5 GB (warning) or 2 GB (critical)
- RAM usage exceeds 90% (warning) or 95% (critical)
- Swap usage exceeds 80%
- CPU load is sustained above 95%
- Internet connectivity is lost

Alerts have a 4-hour cooldown — you won't get spammed. Check manually anytime with `/health`.

## Security

- Bot runs as your user (not root)
- API keys and tokens stripped from the execution environment
- Command blocking for destructive patterns (`rm -rf /`, `mkfs`, fork bombs)
- Write path blocklist protects system files
- Telegram access restricted to your user ID
- Sudo password stored with 600 permissions, used only for package installation
- Atomic file writes prevent corruption on crash

## License

MIT
