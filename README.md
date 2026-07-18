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

The setup wizard walks you through these steps:

1. **Your name**
2. **Telegram bot token + your Telegram user ID** (takes 2 minutes via [@BotFather](https://t.me/BotFather); the wizard explains how to find both)
3. **Which AI provider to use** (free options available — you can change later)
4. **Bot name and timezone**
5. **Queue scope** — universal (one LLM call at a time across the whole bot) or per-user (each Telegram user runs in parallel)
6. **Local Telegram Bot API server** (optional — lifts upload caps from 50 MB to ~2 GB; adds a 30-60 min build step)
7. **Install mode** — workstation, minimal, or headless
8. **System password** — stored locally with mode 0600 so the bot can install software without prompting later

After the wizard finishes, it runs `claude auth login` (or `codex login`) for you in a browser if you picked a CLI provider, then registers the system service. The bot messages you on Telegram. Close the laptop lid if you want — it stays running. If the machine reboots, the bot starts automatically.

### Resuming a failed install

If the install is interrupted (power loss, SSH disconnect, etc.), just run the command again. It resumes from where it left off.

## What can it do?

Talk to it like you would any AI assistant — but this one has full access to a real computer. Some examples:

- "Resize all images in ~/photos to 800px wide"
- "What's the weather in Athens?"
- "Remind me to call the dentist tomorrow at 10am"
- "Download this video: [url]"
- "Generate an image of a cat in a spacesuit"
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
| **Ollama Cloud** | Free tier available | Same models as local Ollama, hosted in the cloud. No GPU needed. Session limits reset every 5 hours. |
| **OpenRouter** | Yes (~200 req/day) | ~16 free models with tool-use. Easy to start with. |
| **Gemini** | Limited free tier | Google's AI. Flash Preview: 10 RPM / 250 RPD. Pro: 5 RPM / 100 RPD. |
| **Grok** | $25 free credits | xAI's models. Vision on 4.1 Fast and 4-0709. 4.1 Fast has 2M context. |
| **Kimi** | No | Moonshot AI. K2.6 long-horizon coding, 256K context. $0.95/$4.00 per MTok. |
| **MiniMax** | No (very cheap) | M2.7 reasoning, 205K context. $0.30/$1.20 per MTok. |
| **DeepSeek** | No (very cheap) | V4 Flash at $0.14/$0.28 per million tokens, 1M context. Great value. |
| **OpenAI** | No | GPT-5.5, GPT-5.5 Pro, GPT-5.4 family, GPT-4.1. Vision + tools. |
| **Claude CLI** | With Pro/Max plan | Most capable. Uses your existing Anthropic subscription. |
| **Codex CLI** | With ChatGPT Plus/Pro plan | OpenAI's parallel to Claude CLI. Same subprocess + JSON-stream pattern, full machine control. |
| **Claude API** | No | Pay-per-token. Text-only (no machine control). |
| **FCC** | Depends on backend | Routes Claude CLI through a [free-claude-code](https://github.com/Alishahryar1/free-claude-code) proxy. Use any backend (Gemini free, DeepSeek, Groq, etc.) with full tool-use. |

**If you want free:** Start with Ollama (local, unlimited), Ollama Cloud (no GPU needed), OpenRouter (~200 req/day), or FCC with a free backend (Gemini, Groq).

**If you want the best quality:** Claude CLI with a Pro subscription, or OpenAI GPT-5.5.

**If you want cheap and good:** DeepSeek V4 Flash at $0.14/$0.28 per million tokens.

Switch providers anytime:
```
/provider openai gpt-5.5
/model gpt-5.4-mini
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

## Sharing the bot with multiple Telegram users

One install can serve any number of Telegram users from a single OS user. Each Telegram user gets their own data directory at `data/users/<telegram_id>/` — separate conversations, attachments, scheduled jobs, memories, and per-user profile. The bot routes messages by Telegram ID; isolation is at the application level, not kernel-enforced.

The first user is the admin. From Telegram:

```
/adduser 123456789 Alice          # add a regular user
/adduser 123456789 Alice admin    # add another admin
/removeuser 123456789             # remove a user (refuses to remove the last admin)
/users                            # list registered users
```

If you need kernel-enforced isolation between multiple humans on one machine, run a separate MyOldMachine install per OS user account.

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
| `/stop` | Kill the current task (Claude CLI only) |
| `/recover` | Show interrupted task |
| `/clear_recovery` | Delete recovery data |
| `/alias` | Manage custom command shortcuts |
| `/provider` | Show or switch AI provider |
| `/model` | Change AI model |
| `/apikey` | Set API key (message auto-deletes) |
| `/skillstats` | View skill usage statistics |
| `/maintenance` | Configure nightly backup, updates, and cleanup (admin) |
| `/cleanup` | Clean old files, rotate logs |
| `/update` | Pull latest updates |
| `/restart` | Restart the bot |
| `/users` | List registered Telegram users (admin) |
| `/adduser <id> <name> [admin]` | Add a Telegram user (admin) |
| `/removeuser <id>` | Remove a Telegram user (admin; refuses to remove last admin) |

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

**Bot offline after a reboot or power outage on macOS?** The bot runs as a user-level LaunchAgent, so it only starts after you log into your account. Until someone types the login passcode, the bot stays offline. Fix: enable auto-login in **System Settings → Users & Groups → "Automatically log in as"**, pick your account, confirm with your passcode. After this, cold boots bring the bot back in seconds. Sleep/wake and manual logout still require the passcode, so day-to-day security is unchanged. Note: FileVault disk encryption must be off for auto-login to be available. If you can't use auto-login (shared space, FileVault required), convert the bot to a system-level LaunchDaemon instead.

**OpenRouter "rate limit" errors?** Free models have a 200 req/day limit, and tool-use consumes 5-6 requests per user message. Switch to a model with higher limits or add billing.

**Gemini "quota exhausted"?** Google's free tier quotas reset daily at midnight Pacific. Use Flash-Lite (1000 RPD) instead of Pro (100 RPD) for higher limits.

**A skill doesn't work?** Send the error to the bot. It can read logs, check versions, and fix configurations.

**"Ollama is not compatible"?** Ollama needs macOS 12+. Use OpenRouter (free) or another cloud provider instead.

**Homebrew slow on old Mac?** Normal — Homebrew compiles from source on older systems. The installer downloads ffmpeg and Node.js directly when Homebrew can't handle it.

**Something else?** Every machine is different. Start the bot, describe the problem, and work through it together. That's how this project is designed to work.

## How it compares

If you've seen [OpenClaw](https://github.com/openclaw/openclaw), you might wonder how MyOldMachine is different. Both turn a machine into an AI assistant you control through messaging — but the design philosophy and target audience diverge significantly.

| | **MyOldMachine** | **OpenClaw** |
|---|---|---|
| **Language** | Python | TypeScript |
| **Install** | `curl ... \| bash` — works on decade-old hardware | `npm install -g openclaw` — requires Node 24 |
| **AI providers** | 13 — Claude CLI, Codex CLI, OpenAI, Gemini, Grok, Kimi, MiniMax, DeepSeek, OpenRouter, Ollama, Ollama Cloud, Claude API, FCC (free-claude-code proxy) | Primarily OpenAI, configurable profiles |
| **Free/local AI** | Ollama (unlimited, local), Ollama Cloud (free tier), OpenRouter free tier (200 req/day), Gemini free tier, FCC with free backends | No built-in free option |
| **Messaging** | Telegram | 22 channels (WhatsApp, Slack, Discord, Telegram, etc.) |
| **Skills** | 75 skills with auto-installing dependencies, resource-aware hooks | 100+ AgentSkills |
| **Target machine** | Old laptops, desktops, any Linux/macOS — runs on 1GB RAM with Ollama small models | Modern hardware recommended |
| **Ownership** | Independent, MIT licensed | OpenAI-acquired (March 2026) |
| **MCP support** | Client — connects to any MCP server for unlimited tool expansion | Native MCP client support |

**When to use MyOldMachine:** You have an old machine collecting dust. You want a free, provider-agnostic AI assistant that works with whatever hardware you have. You don't want vendor lock-in.

**When to use OpenClaw:** You need multi-platform messaging (WhatsApp, Slack, Discord) and are comfortable with Node.js and OpenAI pricing.

## What it replaces

A dedicated MyOldMachine setup can replace several paid services:

| SaaS tool | Monthly cost | MyOldMachine equivalent |
|-----------|-------------|------------------------|
| ChatGPT Plus / Claude Pro | $20–25 | Use Ollama (free, local) or OpenRouter free tier |
| Zapier / Make (automation) | $20–70 | `workflow` skill — YAML-defined multi-step pipelines |
| Notion AI | $10 | `notes` skill + `database` skill |
| Canva (basic) | $13 | `image-gen` + `image-editing` + `inkscape` + `gimp` skills |
| Descript (audio) | $24 | `audio-editing` + `stems` + `voice` skills |
| Grammarly | $12 | Ask the bot to proofread — it has the full LLM |
| Todoist / Reminders | $5 | Built-in scheduler with natural language |
| Cloud storage sync | $3–10 | `cloud-sync` skill (rclone — Google Drive, Dropbox, S3) |
| Website monitoring | $10–30 | `lighthouse` + `screenshot-diff` + `price-monitor` skills |
| Transcription service | $10–25 | `voice` skill (Whisper, local, unlimited) |

That's $127–244/month in SaaS subscriptions replaced by one old laptop running free software. The only ongoing cost is electricity and whichever AI provider you choose — which can be $0 with Ollama.

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

## Skills (63)

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
| workflow | Multi-step YAML pipelines with retry, conditions, crash recovery |
| research | Data collection, aggregation, dialectical analysis, and research automation |
| price-monitor | Track prices and get alerts for changes |
| calendar | Google Calendar integration — view, add, delete events |
| email | Gmail integration — send, read, search, draft emails |
| docker-services | Spin up databases and services with Docker |
| impeccable | Production-grade frontend design knowledge |
| presentations | Cinematic scroll-based HTML presentations with GSAP animations, video export |

### Audio and music skills

| Skill | What it does |
|-------|-------------|
| voice | Speech-to-text transcription using Whisper |
| stems | Separate audio into stems (vocals, drums, bass, other) using Demucs |
| midi | Read, edit, transpose, and merge MIDI files |
| midi-to-audio | Render MIDI to audio with FluidSynth and real instrument soundfonts |
| audio-to-midi | Transcribe audio to MIDI using Spotify's Basic Pitch |
| sheet-music | Convert MIDI to sheet music (PDF/PNG) via LilyPond |
| music-theory | Chord, key, and interval analysis |
| algorithmic-composition | Generate music programmatically |
| sound-design | Programmatic sound synthesis and audio generation |

### Visual and media skills

| Skill | What it does |
|-------|-------------|
| image-gen | Generate images from text prompts (Pollinations.ai, free, no API key) |
| background-removal | AI-powered background removal from images |
| upscale | AI-powered image upscaling with Real-ESRGAN |
| screenshot-diff | Visual regression testing for websites |
| media | Screenshots and video recording of web pages |

### Workstation skills (pre-installed in workstation mode, self-install in others)

| Skill | What it does |
|-------|-------------|
| blender | 3D modeling, rendering, animation via Blender's Python API |
| blender-video | Video editing, color grading, compositing via Blender's VSE |
| godot | Game development with Godot 4 engine |
| gimp | Image editing and manipulation via GIMP's Script-Fu |
| inkscape | Vector graphics creation and manipulation via Inkscape CLI |
| spreadsheet | Create, edit, export Excel/ODS spreadsheets (LibreOffice + openpyxl) |
| scraper | Web scraping with Playwright — handles JavaScript-rendered pages |
| lighthouse | Website performance and SEO auditing |
| icon-gen | Generate favicons, app icons, and icon sets |
| sprite-gen | Create sprites and sprite sheets for games |
| code-scaffold | Generate project templates and boilerplate |
| charts | Terminal bar and line charts |
| notes | Notes and knowledge management with nb |
| bookmarks | Bookmark management with buku |
| cloud-sync | Sync files to cloud storage (Google Drive, Dropbox, S3) via rclone |
| torrent | Search torrent indexers via Jackett and download via aria2. VPN-gated by default. |
| web-build | Static site generation and web development |
| screenplay | Write, version, and export Fountain screenplays to PDF/HTML/FDX |
| mempalace | Per-user permanent memory: semantic search over your conversation history |
| coding | Build/Diagnose methodology protocols with mandatory report block |
| vpn | ProtonVPN control (Linux + macOS) |

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

### MCP server support

[Model Context Protocol](https://modelcontextprotocol.io) (MCP) is an open standard for connecting AI applications to external tools and data sources. MyOldMachine can connect to any MCP server, giving the bot access to hundreds of community-built integrations — databases, APIs, cloud services, and more — without writing custom skills.

**Setup:**

1. Install the MCP SDK: `pip install "mcp[cli]"`
2. Copy `mcp_servers.json.example` to `mcp_servers.json`
3. Add your MCP servers
4. Restart the bot

**Example `mcp_servers.json`:**

```json
{
  "servers": [
    {
      "name": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..." }
    },
    {
      "name": "sqlite",
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "/home/user/data.db"]
    }
  ]
}
```

MCP tools appear alongside built-in tools automatically. The bot discovers each server's capabilities on startup and routes tool calls transparently. Browse available servers at [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers).

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

Each Telegram user gets their own person model, observations, and memory directory. A family sharing one machine gets individual contexts. Privacy note: this is organizational scoping, not a security boundary. All data lives as files on disk, and any allowlisted user who can run tools (or anyone with SSH access) can read it. See [Trust model](#trust-model).

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
                            DeepSeek, Kimi,  calling)
                            MiniMax, Ollama,
                            Ollama Cloud)
                    │           │               │
                    └───────────┼───────────────┘
                                ↓
                    utils/skill_hooks.py
                    (pre-check RAM/disk,
                     post-track usage,
                     stop: cleanup)
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

## Skill hooks

A middleware layer that runs before and after every skill invocation. Prevents resource exhaustion, kills orphaned processes, and tracks usage.

**What it does:**

- **Pre-execution checks** — blocks skills that would exceed available RAM or disk space. The browser, stems (Demucs), blender, upscale, and voice skills all have minimum resource thresholds. If your machine has 2GB free and stems needs 4GB, it tells you why it can't run instead of OOM-killing the bot.
- **Post-execution tracking** — logs every skill invocation to SQLite (duration, RAM snapshot, success/failure). Query stats with `/skillstats` from Telegram or `python utils/skill_usage_cli.py` from the terminal.
- **Session-end cleanup** — when a conversation ends, kills orphaned Chromium/Playwright, Blender, GIMP, Inkscape, Godot, Demucs, and aria2c processes. Removes stale temp files. Stops abandoned Docker containers.
- **Startup cleanup** — on bot restart, sweeps for any processes orphaned by a previous crash. Cleans stale browser state files and old temp files.
- **Denial alerts** — if a skill is blocked due to low resources, sends you a Telegram notification so you know it happened.

**Coverage:**

| Hook type | Skills |
|-----------|--------|
| RAM pre-check | browser, scraper, media, stems, blender, blender-video, upscale, voice, background-removal |
| Disk pre-check | video-editing, downloads |
| Process cleanup | browser, blender, stems, gimp, inkscape, godot, downloads, lighthouse |
| Temp file cleanup | stems, video-editing, image-editing, audio-editing, lighthouse |
| Docker cleanup | docker-services |

Hooks work with all LLM providers, not just Claude. For Claude CLI, they use the native hook system (PreToolUse/PostToolUse/Stop events). For all other providers, they're embedded in the tool execution layer.

18 skills have per-skill `hooks.json` configs. The remaining skills are lightweight enough to not need them.

## Health monitoring

The bot checks system health every 4 hours and alerts you via Telegram if:

- Disk space drops below 5 GB (warning) or 2 GB (critical)
- RAM usage exceeds 90% (warning) or 95% (critical)
- Swap usage exceeds 80%
- CPU load is sustained above 95%
- Internet connectivity is lost

Alerts have a 4-hour cooldown — you won't get spammed. Check manually anytime with `/health`.

## Prompt evaluation

A built-in testing framework for verifying system prompt behavior across providers. Catches regressions when you modify prompts — runs locally, no data leaves your machine.

```bash
# Run built-in test suites against all available providers
python tests/prompt_eval.py

# Test specific providers
python tests/prompt_eval.py --providers openai,gemini

# Run a specific suite
python tests/prompt_eval.py --suite tool_use

# Use custom YAML tests
python tests/prompt_eval.py --config tests/eval_config.yaml

# Verbose — show full responses
python tests/prompt_eval.py -v
```

Built-in suites test: tool-use compliance (structured calls vs code blocks), safety (refuses destructive commands, doesn't leak secrets), communication style (plain language, no jargon), and bot self-protection (refuses to modify its own runtime).

Custom tests use YAML with assertion types: `contains`, `not_contains`, `matches`, `not_matches`, `min_length`, `max_length`, `tool_call`, `no_tool_call`. See `tests/eval_config.yaml.example`.

## Compute pool

Offload heavy jobs — stem separation, upscaling, Blender renders, video encodes, ML training — to other machines you own. The bot's host is the orchestrator; any machine reachable over SSH with key auth can be a worker. Jobs run asynchronously: the orchestrator pushes a stdlib-only runner to the worker, launches it detached, polls a status file, pulls results back, and messages you on Telegram when each job finishes. Route by capability (`--needs gpu --min-ram 16`) or name a worker directly. Pairing a worker grants command execution on it as your SSH user — only pair machines you control. Managed from the terminal via `python utils/worker_cli.py`; see [docs/compute-pool.md](docs/compute-pool.md).

## Security

### Trust model

Allowlisting a Telegram user grants them the ability to run commands on this machine as the OS user the bot runs as. That includes reading and writing files, installing packages, and using any tool the active provider exposes. Tool-capable providers such as the Claude CLI run without interactive permission prompts. This is what makes the bot useful, and it is also the boundary you are trusting.

Roles are not a capability sandbox. The `admin` role gates global bot configuration (switching provider, model, effort) and cross-user views in the Mini App. It does **not** restrict which shell commands a non-admin allowlisted user can run. Per-user data directories (`data/users/<id>/`) provide organizational and privacy scoping, not an enforced boundary. A user who can run tools can reach another user's directory, the `.env` file, and any secret readable by the bot's OS user.

Practical guidance:

- Only allowlist people you would trust with a shell on this machine.
- For users who should not trust each other, run a separate install under a separate OS user account (see [Multi-user](#multi-user)) so the operating system enforces isolation.
- Keep anything the bot must never expose out of the bot's environment and home directory.

The measures below are hardening, not a containment boundary:

- Bot runs as your user (not root)
- API keys and tokens stripped from the execution environment
- Command blocking for destructive patterns (`rm -rf /`, `mkfs`, fork bombs)
- Write path blocklist protects system files and bot runtime
- Bot self-protection prevents the LLM from modifying its own code, venv, or config
- Telegram access restricted to your user ID
- Sudo password stored with 600 permissions, used only for package installation
- Atomic file writes prevent corruption on crash
- Skill hooks enforce resource limits before heavy operations run
- Orphaned process cleanup on session end and bot restart

## License

MIT
