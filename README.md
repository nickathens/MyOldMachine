# MyOldMachine

Turn a laptop you stopped using into a personal AI that you own and control from Telegram.

One command installs it. After that you talk to it on your phone, and because it lives on a real machine it can actually do things: run commands, edit files, install software, generate images and video, edit audio, browse the web, watch a video and tell you what is in it, remember what matters to you, and message you back. No cloud account owns it. No subscription is required. The hardware you already have is enough.

*An old machine, a phone, and nothing standing between them.*

## Highlights

- **Runs on hardware most software has given up on.** A decade old laptop, a machine with 1GB of RAM paired with a small local model, a Raspberry Pi. If it boots, it can host this.
- **14 AI providers, including free and fully local ones.** Switch between them from your phone, no reinstall, no SSH.
- **76 skills**, from image generation and stem separation to Greek legal drafting and DaVinci Resolve. Each installs its own dependencies the first time you use it.
- **A graphical Mini App inside Telegram** for the things a chat box is clumsy at.
- **It remembers you.** A private memory per user that grows into a real model of how you work.
- **Serves a whole household.** Many Telegram users on one machine, each with separate memory, data, and reminders.
- **Connects to any MCP server**, so its toolset has no ceiling.
- **Yours.** MIT licensed, no telemetry, no lock in.

## Quick start

### What you need

- An old laptop or desktop (Linux, or macOS 10.14 and newer)
- An internet connection
- A Telegram account (free, [download here](https://telegram.org))

That is it. The installer handles everything else.

### Install

**Option 1: one command**

```bash
curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
```

**Option 2: clone and run**

```bash
git clone https://github.com/nickathens/MyOldMachine.git
cd MyOldMachine
./install.sh
```

### What the installer does

The setup wizard walks you through these steps:

1. **Your name**
2. **Telegram bot token and your Telegram user ID** (about 2 minutes via [@BotFather](https://t.me/BotFather); the wizard explains how to find both)
3. **Which AI provider to use** (free options available, and you can change it later)
4. **Bot name and timezone**
5. **Queue scope**: universal (one AI call at a time across the whole bot) or per user (each Telegram user runs in parallel)
6. **Local Telegram Bot API server** (optional; lifts the upload cap from 50 MB to roughly 2 GB, at the cost of a 30 to 60 minute build step)
7. **Install mode**: workstation, minimal, or headless
8. **System password** (stored locally with mode 0600 so the bot can install software later without prompting)

When the wizard finishes, it runs `claude auth login` (or `codex login`) for you in a browser if you picked a CLI provider, then registers the system service. The bot messages you on Telegram. Close the laptop lid if you like, it stays running. If the machine reboots, the bot comes back on its own.

### Resuming a failed install

If the install is interrupted (power loss, SSH disconnect, anything), run the command again. It has a checkpoint system and resumes from where it stopped.

## What can it do?

Talk to it the way you would any AI assistant, except this one has full access to a real computer. Some examples:

- "Resize all images in ~/photos to 800px wide"
- "What is the weather in Athens?"
- "Remind me to call the dentist tomorrow at 10am"
- "Download this video: [url]"
- "Generate an image of a cat in a spacesuit"
- "Make a QR code for my website"
- "Summarize this article: [url]"
- "Watch this clip and tell me what happens at the end"
- "Convert this PDF to text"
- "How much disk space do I have left?"

Send it files and it processes them. Send a photo and ask it to resize or convert it. Send audio and ask for the BPM. Send a document and ask for a summary.

## Choosing an AI provider

You pick your provider during setup, and you can switch anytime from Telegram, no SSH needed. Free and local options are listed first.

| Provider | Free? | Notes |
|----------|-------|-------|
| **Ollama** | Yes | Runs AI locally on your machine. No API key. Needs macOS 12+ or a modern Linux kernel. |
| **Ollama Cloud** | Free tier | The same models hosted in the cloud. No local GPU needed. |
| **OpenRouter** | Free models available | Many models behind one key, including free ones with tool use. Easy to start with. |
| **Gemini** | Free tier | Google's models. Roughly 5 to 15 requests per minute on the free tier. Default `gemini-3.5-flash`. |
| **FCC (Free Claude Code)** | Depends on backend | Routes the Claude CLI through a [free-claude-code](https://github.com/Alishahryar1/free-claude-code) proxy, so you get full tool use on a free backend (Gemini, DeepSeek, Groq). |
| **Grok** | $25 free credits | xAI's models, machine control via function calling. Default `grok-4.3`. |
| **DeepSeek** | Paid, very cheap | V4 Flash at $0.14 / $0.28 per MTok, 1M context. The best value on the list. |
| **MiniMax** | Paid, cheap | M3 frontier coding, 1M context, $0.30 / $1.20 per MTok. |
| **Kimi** | Paid | Moonshot K2.7 Code, token efficient agentic coding, 256K context, $0.95 / $4.00 per MTok. |
| **Z.ai GLM** | Paid | GLM-5.2 open weights, long horizon agentic work, 1M context, $1.40 / $4.40 per MTok. |
| **OpenAI** | Paid | GPT-5.6 and the GPT-5 family. Vision and tools via function calling. |
| **Claude CLI** | With a Pro or Max plan | The most capable option. Uses your existing Anthropic subscription, no API key. Full machine control. |
| **Codex CLI** | With a ChatGPT Plus or Pro plan | OpenAI's parallel to the Claude CLI. Same subprocess and JSON stream pattern, full machine control. |
| **Claude API** | Paid | Pay per token. Chat only, no machine control. |

**If you want free:** start with Ollama (local, unlimited), Ollama Cloud (no GPU needed), OpenRouter free models, or FCC on a free backend.

**If you want the best quality:** Claude CLI with a Pro subscription, or OpenAI GPT-5.6.

**If you want cheap and good:** DeepSeek V4 Flash at $0.14 / $0.28 per million tokens.

Switch anytime from Telegram:

```
/provider deepseek           switch provider (uses its recommended model)
/model deepseek-v4-pro        change the model within the current provider
/apikey sk-abc123...          set the API key (the message auto deletes)
```

## Install modes

The installer offers three shapes.

### Full workstation (recommended)

Installs creative and productivity apps alongside the bot: Blender, GIMP, Inkscape, LibreOffice, ImageMagick, rclone. Your desktop stays intact, so you can still use the machine normally while controlling it from Telegram. This unlocks the most capabilities.

### Minimal

The bot runs as a background service. Your existing apps and settings stay untouched. Skills install their own dependencies the first time you use them. Good if you want to keep the machine as it is and add capabilities gradually.

### Headless server

Strips the desktop, disables sleep, and turns the machine into a dedicated bot appliance you reach only through Telegram or SSH. Frees up resources. Good for a machine you will never sit in front of again.

## Multiple users, one machine

One install can serve any number of Telegram users from a single OS account. Each Telegram user gets their own data directory at `data/users/<telegram_id>/`: separate conversations, attachments, scheduled jobs, memories, and profile. The bot routes messages by Telegram ID. Isolation here is at the application level, not kernel enforced.

The first user is the admin. From Telegram:

```
/adduser 123456789 Alice          add a regular user
/adduser 123456789 Alice admin    add another admin
/removeuser 123456789             remove a user (refuses to remove the last admin)
/users                            list registered users
```

If you need kernel enforced isolation between people who should not trust each other, run a separate MyOldMachine install per OS account. See [Trust model](#trust-model).

## The Mini App

Some things are clumsy in a chat box. MyOldMachine ships a Telegram Mini App, a small graphical panel that opens from an App button in the chat, for exactly those. It gives you a data driven picker for switching provider and model, a place to set keys, media generation controls, and, for admins, a view across users. The Mini App runs as its own process next to the bot and edits configuration live, so a change you make there takes effect on the next turn without a restart.

## Telegram commands

| Command | What it does |
|---------|-------------|
| `/start` | Connect and show system info |
| `/help` | List all commands |
| `/status` | Messages, memories, skills, uptime |
| `/health` | Disk, RAM, CPU, and network report |
| `/system` | Version, OS, and provider info |
| `/clear` | Reset the conversation |
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
| `/stop` | Stop your current task and cancel anything you have queued (killing a running task needs a CLI provider) |
| `/recover` | Show an interrupted task |
| `/clear_recovery` | Delete recovery data |
| `/alias` | Manage custom command shortcuts |
| `/provider` | Show or switch AI provider |
| `/model` | Change the AI model |
| `/apikey` | Set an API key (the message auto deletes) |
| `/skillstats` | View skill usage statistics |
| `/maintenance` | Configure nightly backup, updates, reboot, and cleanup (admin) |
| `/cleanup` | Clean old files and rotate logs |
| `/update` | Pull the latest updates |
| `/restart` | Restart the bot |
| `/users` | List registered Telegram users (admin) |
| `/adduser <id> <name> [admin]` | Add a Telegram user (admin) |
| `/removeuser <id>` | Remove a Telegram user (admin, refuses to remove the last admin) |

The everyday commands also appear in Telegram's own "/" menu, so you can tap instead of remembering names.

## Custom shortcuts

Define your own commands for things you do often:

```
/alias set disk Check disk usage and alert if above 80%
/alias set weather What is the weather in Athens?
/alias set backup Run my backup script at ~/backup.sh
```

Then just type `/disk`, `/weather`, or `/backup`.

## Troubleshooting

The bot itself is your first troubleshooting tool. Tell it what went wrong and it can usually fix it.

**Install fails partway through?** Run the install command again. The checkpoint system resumes from where it stopped.

**Bot not responding?** Check the service: `sudo systemctl status myoldmachine` (Linux) or `launchctl list | grep myoldmachine` (macOS). Logs are in `data/logs/bot.log`.

**Bot offline after a reboot or power outage on macOS?** The bot runs as a user level LaunchAgent, so it starts only after you log into your account. Until someone types the login passcode, the bot stays offline. Fix: enable auto login in **System Settings, Users & Groups, "Automatically log in as"**, pick your account, and confirm with your passcode. After that, cold boots bring the bot back in seconds. Sleep, wake, and manual logout still require the passcode, so day to day security is unchanged. Note that FileVault disk encryption must be off for auto login to be available. If you cannot use auto login (a shared space, or FileVault is required), convert the bot to a system level LaunchDaemon instead.

**OpenRouter rate limit errors?** Free models cap at 200 requests per day, and tool use spends 5 or 6 requests per message. Switch to a model with higher limits or add billing.

**Gemini quota exhausted?** Google's free tier quotas reset daily at midnight Pacific. Use Flash Lite for higher limits than Pro.

**A skill does not work?** Send the error to the bot. It can read logs, check versions, and fix configurations.

**"Ollama is not compatible"?** Ollama needs macOS 12+. Use OpenRouter (free) or another cloud provider instead.

**Homebrew slow on an old Mac?** Normal. Homebrew compiles from source on older systems. The installer downloads ffmpeg and Node.js directly when Homebrew cannot handle them.

**Something else?** Every machine is different. Start the bot, describe the problem, and work through it together. That is how this project is meant to be used.

## How it compares

If you have seen [OpenClaw](https://github.com/openclaw/openclaw), you might wonder how MyOldMachine differs. Both turn a machine into an AI assistant you control through messaging, but the design philosophy and target audience diverge.

| | **MyOldMachine** | **OpenClaw** |
|---|---|---|
| **Language** | Python | TypeScript |
| **Install** | `curl ... \| bash`, works on decade old hardware | `npm install -g openclaw`, requires Node 24 |
| **AI providers** | 14: Claude CLI, Codex CLI, OpenAI, Gemini, Grok, Kimi, MiniMax, DeepSeek, Z.ai, OpenRouter, Ollama, Ollama Cloud, Claude API, FCC | Primarily OpenAI, configurable profiles |
| **Free or local AI** | Ollama (unlimited, local), Ollama Cloud (free tier), OpenRouter free tier, Gemini free tier, FCC on free backends | No built in free option |
| **Messaging** | Telegram | 22 channels (WhatsApp, Slack, Discord, Telegram, and more) |
| **Skills** | 76 skills with auto installing dependencies and resource aware hooks | 100+ AgentSkills |
| **Target machine** | Old laptops, desktops, any Linux or macOS, runs on 1GB of RAM with small Ollama models | Modern hardware recommended |
| **Ownership** | Independent, MIT licensed | OpenAI acquired (March 2026) |
| **MCP support** | Client, connects to any MCP server for unlimited tool expansion | Native MCP client support |

**When to use MyOldMachine:** you have an old machine collecting dust, you want a free, provider agnostic AI assistant that works with the hardware you already have, and you do not want vendor lock in.

**When to use OpenClaw:** you need multi platform messaging (WhatsApp, Slack, Discord) and are comfortable with Node.js and OpenAI pricing.

## What it replaces

A dedicated MyOldMachine setup can stand in for several paid services:

| SaaS tool | Monthly cost | MyOldMachine equivalent |
|-----------|-------------|------------------------|
| ChatGPT Plus / Claude Pro | $20 to $25 | Ollama (free, local) or OpenRouter free tier |
| Zapier / Make (automation) | $20 to $70 | `workflow` skill, YAML defined multi step pipelines |
| Notion AI | $10 | `notes` skill plus `database` skill |
| Canva (basic) | $13 | `image-gen`, `image-editing`, `inkscape`, `gimp` skills |
| Descript (audio) | $24 | `audio-editing`, `stems`, `voice` skills |
| Grammarly | $12 | Ask the bot to proofread, it has the full LLM |
| Todoist / Reminders | $5 | Built in scheduler with natural language |
| Cloud storage sync | $3 to $10 | `cloud-sync` skill (rclone: Google Drive, Dropbox, S3) |
| Website monitoring | $10 to $30 | `lighthouse`, `screenshot-diff`, `price-monitor` skills |
| Transcription service | $10 to $25 | `voice` skill (Whisper, local, unlimited) |

That is $127 to $244 per month of subscriptions replaced by one old laptop running free software. The only ongoing cost is electricity and whichever AI provider you choose, which can be $0 with Ollama.

---

# Advanced

Everything below is for people who want to understand the internals, extend the system, or contribute.

## The idea

Old machines are all different. A 2012 MacBook Air is not a 2015 ThinkPad is not a Raspberry Pi 4. No installer can predict every configuration. MyOldMachine handles the basics, dependencies, service registration, and LLM setup, and then you and the bot work out the rest together.

This is not a polished consumer product. It is a toolkit. You shape it.

## Supported platforms

### Linux (systemd service)
- **Debian and Ubuntu**: apt
- **Fedora, RHEL, CentOS, Amazon Linux**: dnf or yum
- **Arch, Manjaro, EndeavourOS**: pacman
- **openSUSE**: zypper
- **Alpine**: apk

Any Linux distribution with one of these package managers works. The installer auto detects your distro.

### macOS (launchd service)
- **macOS 10.14 and newer**: Homebrew preferred, with direct download fallbacks for ffmpeg and Node.js when Homebrew fails or builds from source too slowly

### Other
- **Windows**: planned

### Software compatibility

The installer handles version mismatches automatically:

1. **System package manager first**: it tries `apt install`, `dnf install`, `brew install`, and so on.
2. **Flatpak fallback (Linux)**: if the system package fails, it tries Flatpak. Flatpak apps are sandboxed and version independent, so a 2016 Ubuntu machine can run the latest Blender.
3. **Direct binary downloads (macOS)**: for ffmpeg and Node.js, it downloads prebuilt binaries when Homebrew cannot build them on old macOS.
4. **Report what failed**: anything that could not be installed is reported with actionable guidance.

### Hardware notes
- Ollama (local AI) requires macOS 12+ or a modern Linux kernel.
- Playwright (the browser skill) needs enough RAM for Chromium. On non Debian systems you may need to install browser dependencies manually.
- On first boot the bot probes the system and reports which skills are ready and which need dependencies.
- Flatpak apps are detected by the system probe and reported alongside system installed tools.

## Skills (76)

Skills are modular packages the bot loads automatically. Each has instructions the LLM reads, optional scripts, and a dependency manifest. Dependencies install on first use, so a fresh machine stays lean until a skill is actually needed.

### Core skills (all install modes)

| Skill | What it does |
|-------|-------------|
| browser | Full headless browser: navigate, click, fill forms, screenshot, extract content |
| weather | Current weather and forecasts |
| translate | Text translation |
| ocr | Text extraction from images and PDFs |
| compress | ZIP and TAR archives |
| downloads | Parallel downloads with aria2 |
| summarize | Fetch and summarize URLs |
| pdf | Merge, split, and extract text from PDFs |
| image-editing | Resize, crop, rotate, filter, and convert formats |
| audio-editing | Cut, merge, fade, and convert audio |
| video-editing | Cut, merge, text overlays, and format conversion |
| audio-analysis | BPM, key detection, and loudness analysis |
| color-palette | Extract and generate color palettes |
| text-to-speech | Generate spoken audio from text |
| font-tools | Font conversion and subsetting |
| git | Version control with Git and the GitHub CLI |
| database | SQLite operations |
| api-test | HTTP API testing with curl |
| docs | Convert documents to markdown |
| qrcode | Generate QR codes |
| rss | Parse RSS and Atom feeds |
| regex | Pattern matching and text extraction |
| diagram | Render Mermaid diagrams to PNG, SVG, or PDF |
| clipboard | Read and write the system clipboard, bridging phone and desktop |
| watch | Watch any video: download it, extract keyframes, pull a timestamped transcript, then answer questions about it |
| workflow | Multi step YAML pipelines with retries, conditions, and crash recovery |
| research | Data collection, aggregation, dialectical analysis, and research automation |
| price-monitor | Track prices and get alerts on changes |
| calendar | Google Calendar: view, add, and delete events |
| email | Gmail: send, read, search, and draft |
| docker-services | Spin up databases and services with Docker |
| impeccable | Production grade frontend design knowledge |
| presentations | Cinematic scroll based HTML presentations with GSAP animations and video export |

### Audio and music skills

| Skill | What it does |
|-------|-------------|
| voice | Speech to text transcription with Whisper |
| stems | Separate audio into stems (vocals, drums, bass, other) with Demucs |
| midi | Read, edit, transpose, and merge MIDI files |
| midi-to-audio | Render MIDI to audio with FluidSynth and real instrument soundfonts |
| audio-to-midi | Transcribe audio to MIDI with Spotify's Basic Pitch |
| sheet-music | Convert MIDI to sheet music (PDF or PNG) with LilyPond |
| music-theory | Chord, key, and interval analysis |
| algorithmic-composition | Generate music programmatically |
| sound-design | Programmatic sound synthesis and audio generation |

### Visual and media skills

| Skill | What it does |
|-------|-------------|
| image-gen | Generate images from text prompts (Pollinations.ai, free, no API key) |
| background-removal | AI powered background removal |
| upscale | AI powered image upscaling with Real-ESRGAN |
| algorithmic-art | Generative visual art with p5.js, output as a self contained HTML page renderable to PNG or video |
| logo-animate | Turn a raster logo into a clean SVG, then a choreographed brand animation as standalone HTML, GIF, or MP4 |
| remotion | Programmatic motion graphics: build videos as React components and render to H.264 MP4 |
| davinci-resolve | Drive DaVinci Resolve for editorial work: build timelines, hand off rough cuts, queue renders and grades |
| screenshot-diff | Visual regression testing for websites |
| media | Screenshots and video recording of web pages |

### Workstation skills (preinstalled in workstation mode, self install in others)

| Skill | What it does |
|-------|-------------|
| blender | 3D modeling, rendering, and animation via Blender's Python API |
| blender-video | Video editing, color grading, and compositing via Blender's VSE |
| godot | Game development with the Godot 4 engine |
| gimp | Image editing via GIMP's Script-Fu |
| inkscape | Vector graphics via the Inkscape CLI |
| spreadsheet | Create, edit, and export Excel and ODS spreadsheets (LibreOffice plus openpyxl) |
| scraper | Web scraping with Playwright, handles JavaScript rendered pages |
| lighthouse | Website performance and SEO auditing |
| icon-gen | Generate favicons, app icons, and icon sets |
| sprite-gen | Create sprites and sprite sheets for games |
| code-scaffold | Generate project templates and boilerplate |
| charts | Terminal bar and line charts |
| notes | Notes and knowledge management with nb |
| bookmarks | Bookmark management with buku |
| cloud-sync | Sync files to cloud storage (Google Drive, Dropbox, S3) with rclone |
| torrent | Search torrent indexers via Jackett and download via aria2, VPN gated by default |
| web-build | Static site generation and web development |
| screenplay | Write, version, and export Fountain screenplays to PDF, HTML, or FDX |
| mempalace | Permanent per user memory: semantic search over your conversation history |
| coding | Build and Diagnose methodology protocols with a mandatory report block |
| vpn | ProtonVPN control (Linux and macOS) |

### Professional and developer skills

| Skill | What it does |
|-------|-------------|
| greek-law | Greek law research, drafting, and explanation, grounded in primary Greek sources with strict citation discipline |
| greek-engineer | Technical and regulatory assistant for engineering practice in Greece, with self checking calculation engines |
| mcp-builder | Guide and scaffolding for building Model Context Protocol servers |
| security-audit | Defensive source code security auditing, producing a verified findings report a developer can act on |

### Adding your own skills

Create a directory under `skills/`:

```
skills/my-skill/
├── SKILL.md          Instructions for the LLM
├── deps.json         Dependencies (auto installed)
└── scripts/
    └── my_script.py  Your script
```

The bot reads `SKILL.md` to learn how to use the skill. Dependencies install automatically on first use.

### MCP server support

[Model Context Protocol](https://modelcontextprotocol.io) is an open standard for connecting AI applications to external tools and data sources. MyOldMachine can connect to any MCP server, giving the bot access to hundreds of community built integrations (databases, APIs, cloud services, and more) without writing custom skills.

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

MCP tools appear alongside the built in tools automatically. The bot discovers each server's capabilities on startup and routes tool calls transparently. Browse available servers at [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers).

## Deep memory system

The bot builds an evolving understanding of each user over time. This is not a flat list of facts, it is a structured person model that captures behavioral patterns, communication preferences, current priorities, and relationship dynamics.

### How it works

1. **Observations**: during conversations the bot notices things worth remembering (corrections, preferences, patterns, project context) and saves them as append only entries.
2. **Person model**: a structured profile for each user, roughly 500 words, covering identity, preferences, behavior, current state, and relationship.
3. **Nightly reflection**: a scheduled job runs at 3 AM, reads the recent observations, and updates each user's person model. It deduplicates, synthesizes patterns, and discards noise.

### Tier aware

The system adapts to the model running it.

- **Strong models** (Claude, GPT-5 family, DeepSeek, Grok, Gemini, large Ollama 70B and up): full mode, with observations, nightly reflection, and model updates. The LLM has enough reasoning ability to synthesize behavioral data meaningfully.
- **Weak models** (small Ollama, free OpenRouter): lite mode, where observations accumulate as raw entries loaded directly into context, with no reflection loop. Still useful, the bot remembers facts and corrections, but without the pattern synthesis.

### Per user isolation

Each Telegram user gets their own person model, observations, and memory directory. A family sharing one machine gets individual contexts. This is organizational scoping, not a security boundary: all data lives as files on disk, and any allowlisted user who can run tools (or anyone with SSH access) can read it. See [Trust model](#trust-model).

The Claude CLI provider extends this scoping to the model's own workspace. Each user gets a private `CLAUDE_CONFIG_DIR` under `data/users/<id>/claude`, so the CLI's auto memory pool and session transcripts never cross users. On the first turn after an upgrade the previously shared pool is split once by provenance: each memory file is traced back to the Telegram user it served, unattributable files go to the primary admin, and the old shared pool stays on disk untouched as a frozen backup. Machine wide hooks and permissions (`settings.json`, plugins) and the CLI credential file are shared into every workspace by symlink, so all users ride one login and one token refresh chain. On macOS the credential is exported once from the keychain, which the CLI only consults for its default config dir.

### What gets remembered

| Observation type | Example |
|------------------|---------|
| `behavioral` | "Prefers short, direct answers" |
| `correction` | "Got the timezone wrong, user is in Athens, not London" |
| `preference` | "Always wants audio files as MP3, not WAV" |
| `state` | "Currently focused on a job application deadline" |
| `project` | "Working on a website redesign for their portfolio" |
| `factual` | "User's timezone is Europe/Athens" |
| `relationship` | "User expressed frustration with verbose responses" |

The bot saves observations automatically during conversations. Users can also use `/remember` for explicit facts.

## Architecture

```
Telegram user
  bot.py              routes messages, commands, and sessions
  core/llm.py         provider factory, 14 providers
    ClaudeCLIProvider     native tool use
    CodexCLIProvider      native tool use
    OpenAI compatible     OpenAI, OpenRouter, Grok, DeepSeek, Kimi, MiniMax, Z.ai, Ollama, Ollama Cloud
    GeminiProvider        native function calling
  utils/skill_hooks.py   pre: RAM and disk checks   post: usage tracking   stop: cleanup
  core/tools.py          10 tools (5 machine, 5 skill overlay)
```

The bot exposes 10 tools. Five drive the machine: `run_command` (foreground or background), `read_file`, `write_file`, `list_directory`, and `check_process` (poll or kill background processes). Through those five it can do anything you could at a terminal. The other five let each user tailor their own skills without affecting anyone else: `set_skill_override`, `set_skill_note`, `clear_skill_note`, `fork_skill`, and `unfork_skill`.

## Skill hooks

A middleware layer runs before and after every skill invocation. It prevents resource exhaustion, kills orphaned processes, and tracks usage.

- **Pre execution checks**: block skills that would exceed available RAM or disk. The browser, stems (Demucs), blender, upscale, and voice skills all carry minimum thresholds. If your machine has 2GB free and stems needs 4GB, it tells you why it cannot run instead of letting the OOM killer take the bot.
- **Post execution tracking**: log every skill invocation to SQLite (duration, RAM snapshot, success or failure). Query with `/skillstats` from Telegram or `python utils/skill_usage_cli.py` from the terminal.
- **Session end cleanup**: when a conversation ends, kill orphaned Chromium and Playwright, Blender, GIMP, Inkscape, Godot, Demucs, and aria2c processes. Remove stale temp files. Stop abandoned Docker containers.
- **Startup cleanup**: on restart, sweep for anything orphaned by a previous crash, and clean stale browser state and old temp files.
- **Denial alerts**: if a skill is blocked for low resources, send a Telegram notification so you know it happened.

**Coverage:**

| Hook type | Skills |
|-----------|--------|
| RAM pre check | browser, scraper, media, stems, blender, blender-video, upscale, voice, background-removal |
| Disk pre check | video-editing, downloads |
| Process cleanup | browser, blender, stems, gimp, inkscape, godot, downloads, lighthouse |
| Temp file cleanup | stems, video-editing, image-editing, audio-editing, lighthouse |
| Docker cleanup | docker-services |

Hooks work with all providers, not just Claude. For the Claude CLI they use the native hook system (PreToolUse, PostToolUse, Stop events). For every other provider they are embedded in the tool execution layer. 28 skills carry a per skill `hooks.json`, and the rest are light enough not to need one.

## Health monitoring

The bot checks system health every 4 hours and alerts you on Telegram when:

- Disk space drops below 5 GB (warning) or 2 GB (critical)
- RAM usage exceeds 90% (warning) or 95% (critical)
- Swap usage exceeds 80%
- CPU load stays above 95%
- Internet connectivity is lost

Alerts have a 4 hour cooldown, so you will not get spammed. Check manually anytime with `/health`.

## Nightly maintenance

An admin can turn on a nightly chain from `/maintenance`: backup, reflection, cleanup, system update, a fresh system probe, a health check, and an optional reboot slotted after the whole chain. Each step is opt in, and the reboot refuses to run unless it can confirm the bot will come back on boot (an enabled systemd service on Linux, or a boot service or auto login on macOS), so it never strands the machine at a login screen.

## Prompt evaluation

A built in testing framework verifies system prompt behavior across providers. It catches regressions when you change prompts, runs locally, and sends nothing off the machine.

```bash
# Run the built in suites against all available providers
python tests/prompt_eval.py

# Test specific providers
python tests/prompt_eval.py --providers openai,gemini

# Run one suite
python tests/prompt_eval.py --suite tool_use

# Use custom YAML tests
python tests/prompt_eval.py --config tests/eval_config.yaml

# Verbose, show full responses
python tests/prompt_eval.py -v
```

The built in suites test tool use compliance (structured calls versus code blocks), safety (refusing destructive commands, not leaking secrets), communication style (plain language, no jargon), and self protection (refusing to modify the bot's own runtime). Custom tests use YAML with assertion types `contains`, `not_contains`, `matches`, `not_matches`, `min_length`, `max_length`, `tool_call`, and `no_tool_call`. See `tests/eval_config.yaml.example`.

## Compute pool

Offload heavy jobs (stem separation, upscaling, Blender renders, video encodes, ML training) to other machines you own. The bot's host is the orchestrator, and any machine reachable over SSH with key auth can be a worker. Jobs run asynchronously: the orchestrator pushes a stdlib only runner to the worker, launches it detached, polls a status file, pulls results back, and messages you on Telegram when each job finishes. Route by capability (`--needs gpu --min-ram 16`) or name a worker directly. Pairing a worker grants command execution on it as your SSH user, so pair only machines you control. Managed from the terminal with `python utils/worker_cli.py`. See [docs/compute-pool.md](docs/compute-pool.md).

## Security

### Trust model

Allowlisting a Telegram user grants them the ability to run commands on this machine as the OS user the bot runs as. That includes reading and writing files, installing packages, and using any tool the active provider exposes. Tool capable providers such as the Claude CLI run without interactive permission prompts. This is what makes the bot useful, and it is also the boundary you are trusting.

Roles are not a capability sandbox. The `admin` role gates global bot configuration (switching provider, model, effort) and cross user views in the Mini App. It does **not** restrict which shell commands a non admin allowlisted user can run. Per user data directories (`data/users/<id>/`) provide organizational and privacy scoping, not an enforced boundary. A user who can run tools can reach another user's directory, the `.env` file, and any secret the bot's OS user can read.

Practical guidance:

- Allowlist only people you would trust with a shell on this machine.
- For users who should not trust each other, run a separate install under a separate OS account (see [Multiple users](#multiple-users-one-machine)) so the operating system enforces isolation.
- Keep anything the bot must never expose out of the bot's environment and home directory.

The measures below are hardening, not a containment boundary:

- The bot runs as your user, not root.
- API keys and tokens are stripped from the execution environment.
- Destructive command patterns are blocked (`rm -rf /`, `mkfs`, fork bombs).
- A write path blocklist protects system files and the bot runtime.
- Self protection prevents the LLM from modifying its own code, venv, or config.
- Telegram access is restricted to your allowlisted user IDs.
- The sudo password is stored with 600 permissions and used only for package installation.
- Atomic file writes prevent corruption on crash.
- Skill hooks enforce resource limits before heavy operations run.
- Orphaned processes are cleaned up on session end and on restart.

## License

MIT
