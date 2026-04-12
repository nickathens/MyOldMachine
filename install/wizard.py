#!/usr/bin/env python3
"""
MyOldMachine Setup Wizard — Interactive configuration.

Walks the user through setup: name, Telegram token, LLM provider,
takeover level, sudo password, timezone. Writes .env and user profile.
Then hands off to the provisioner for system-level changes.

Supports checkpoint resume — if the script is interrupted and re-run,
already-completed steps are skipped automatically.
"""

import argparse
import getpass
import json
import os
import platform
import re
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add repo root to path
REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))


# --- Checkpoint system ---

CHECKPOINT_FILE = os.environ.get("MYOLDMACHINE_CHECKPOINT_FILE",
                                  str(Path.home() / ".myoldmachine_install_checkpoints"))


def checkpoint_done(name: str) -> bool:
    try:
        with open(CHECKPOINT_FILE) as f:
            return name in [line.strip() for line in f]
    except FileNotFoundError:
        return False


def checkpoint_set(name: str):
    with open(CHECKPOINT_FILE, "a") as f:
        f.write(name + "\n")


# --- Terminal UI helpers ---

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def info(msg):
    print(f"{BLUE}[INFO]{NC} {msg}")


def ok(msg):
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")
    sys.exit(1)


def ask(prompt, default=None, required=True, secret=False):
    """Ask a question with optional default."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            if secret:
                value = getpass.getpass(f"  {prompt}{suffix}: ")
            else:
                value = input(f"  {prompt}{suffix}: ").strip()
        except EOFError:
            error("Input stream closed. Can't read user input.")
        if not value and default:
            return default
        if not value and required:
            print(f"  {RED}This field is required.{NC}")
            continue
        return value


def ask_choice(prompt, options, default=None):
    """Ask user to pick from numbered options."""
    print(f"  {prompt}")
    for i, (key, desc) in enumerate(options, 1):
        marker = " (default)" if key == default else ""
        print(f"    {i}. {key} — {desc}{marker}")
    while True:
        try:
            raw = input(f"  Choice [{default or ''}]: ").strip()
        except EOFError:
            error("Input stream closed.")
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        for key, _ in options:
            if raw.lower() == key.lower():
                return key
        print(f"  {RED}Invalid choice. Pick 1-{len(options)} or type the name.{NC}")


def detect_timezone():
    """Attempt to detect local timezone."""
    try:
        import tzlocal
        return str(tzlocal.get_localzone())
    except ImportError:
        pass
    try:
        tz = Path("/etc/timezone").read_text().strip()
        if tz:
            return tz
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # macOS — /etc/localtime symlink
    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            target = str(localtime.resolve())
            for marker in ["/zoneinfo/"]:
                if marker in target:
                    tz = target.split(marker, 1)[1]
                    if "/" in tz:
                        return tz
    except Exception:
        pass
    # macOS fallback
    try:
        result = subprocess.run(
            ["systemsetup", "-gettimezone"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            match = re.search(r":\s*(.+)", result.stdout)
            if match:
                return match.group(1).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "UTC"


def detect_machine_specs():
    """Detect basic machine specs."""
    import platform
    specs = {
        "os": platform.system().lower(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "hostname": platform.node(),
    }

    # CPU
    try:
        if specs["os"] == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            specs["cpu"] = result.stdout.strip()
        else:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        specs["cpu"] = line.split(":")[1].strip()
                        break
    except Exception:
        specs["cpu"] = "Unknown"

    # RAM
    try:
        if specs["os"] == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            specs["ram_gb"] = round(int(result.stdout.strip()) / (1024**3))
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        specs["ram_gb"] = round(kb / (1024**2))
                        break
    except Exception:
        specs["ram_gb"] = 0

    # Disk
    try:
        st = os.statvfs("/")
        specs["disk_gb"] = round((st.f_blocks * st.f_frsize) / (1024**3))
        specs["disk_free_gb"] = round((st.f_bavail * st.f_frsize) / (1024**3))
    except Exception:
        specs["disk_gb"] = 0
        specs["disk_free_gb"] = 0

    # GPU
    specs["gpu"] = None
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "VGA" in line or "3D" in line:
                specs["gpu"] = line.split(": ", 1)[-1].strip()
                break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if not specs["gpu"]:
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "Chipset Model" in line:
                    specs["gpu"] = line.split(":")[1].strip()
                    break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return specs


_ALL_LLM_PROVIDERS = [
    ("claude", "Claude Code CLI — uses your Pro/Max plan (no API key needed), full machine control"),
    ("claude-api", "Anthropic Claude API — requires paid API credits ($), chat only, no machine control"),
    ("openai", "OpenAI — requires API key ($), machine control via function calling"),
    ("deepseek", "DeepSeek — extremely cheap ($0.28/$0.42 per MTok), 128K context, machine control"),
    ("grok", "xAI Grok — $25 free credits on signup, machine control via function calling"),
    ("kimi", "Moonshot Kimi — K2.5 multimodal, 256K context, $0.60/$3.00 per MTok, machine control"),
    ("gemini", "Google Gemini — free tier available (5-15 RPM), machine control via function calling"),
    ("ollama", "Ollama — free, runs locally on your machine, machine control via function calling"),
    ("ollama-cloud", "Ollama Cloud — cloud-hosted models (free tier available), no local GPU needed"),
    ("openrouter", "OpenRouter — many models, one API key (free models available), machine control"),
]


def _get_available_providers() -> list:
    """Return LLM providers available on this machine.

    Claude CLI requires Node.js — if not present, it will be installed
    automatically during provisioning. Never hide it from the user.
    """
    return list(_ALL_LLM_PROVIDERS)

DEFAULT_MODELS = {
    "claude": "claude-sonnet-4-6",
    "claude-api": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
    "deepseek": "deepseek-chat",
    "grok": "grok-4-1-fast-non-reasoning",
    "kimi": "kimi-k2.5",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1:8b",
    "ollama-cloud": "qwen3.5:cloud",
    "openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
}

# Model lists per provider — shown as numbered options during setup.
# First entry in each list is the default (recommended).
# Last updated: April 12, 2026 — verified against official API docs/pricing pages
PROVIDER_MODELS = {
    "claude": [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 — fast, strong reasoning (recommended)"),
        ("claude-opus-4-6", "Claude Opus 4.6 — most capable, complex tasks"),
    ],
    "claude-api": [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 — fast, strong reasoning, $3/$15 per MTok (recommended)"),
        ("claude-opus-4-6", "Claude Opus 4.6 — most capable, $5/$25 per MTok"),
        ("claude-haiku-4-5", "Claude Haiku 4.5 — fastest, cheapest, $1/$5 per MTok"),
        ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5 — legacy, $3/$15 per MTok (retires no sooner than Sep 29)"),
    ],
    "openai": [
        ("gpt-5.4", "GPT-5.4 — latest frontier, vision + tools, 1.1M ctx, $2.50/$15 per MTok (recommended)"),
        ("gpt-5.4-mini", "GPT-5.4 Mini — fast, vision + tools, $0.75/$4.50 per MTok"),
        ("gpt-5.4-nano", "GPT-5.4 Nano — cheapest 5.4-class, $0.20/$1.25 per MTok"),
        ("gpt-5-mini", "GPT-5 Mini — fast, 400K ctx, $0.25/$2 per MTok"),
        ("gpt-5-nano", "GPT-5 Nano — fastest, cheapest, 400K ctx, $0.05/$0.40 per MTok"),
        ("gpt-4.1", "GPT-4.1 — strong coding, 1M context, $2/$8 per MTok"),
        ("gpt-4.1-mini", "GPT-4.1 Mini — good balance, 1M ctx, $0.40/$1.60 per MTok"),
        ("gpt-4.1-nano", "GPT-4.1 Nano — lightweight, 1M ctx, $0.10/$0.40 per MTok"),
        ("o4-mini", "o4-mini — reasoning model, 200K ctx, $1.10/$4.40 per MTok"),
        ("o3-mini", "o3-mini — reasoning model, 200K ctx, $0.55/$2.20 per MTok"),
    ],
    "grok": [
        ("grok-4-1-fast-non-reasoning", "Grok 4.1 Fast — cheapest, vision, 2M ctx, $0.20/$0.50 per MTok (recommended)"),
        ("grok-4-1-fast-reasoning", "Grok 4.1 Fast Reasoning — chain-of-thought, vision, 2M ctx, $0.20/$0.50"),
        ("grok-4.20-0309-non-reasoning", "Grok 4.20 — flagship, vision, 2M ctx, $2/$6 per MTok"),
        ("grok-4.20-0309-reasoning", "Grok 4.20 Reasoning — chain-of-thought, vision, 2M ctx, $2/$6 per MTok"),
        ("grok-4.20-multi-agent-0309", "Grok 4.20 Multi-Agent — agentic tasks, vision, 2M ctx, $2/$6 per MTok"),
    ],
    "gemini": [
        ("gemini-2.5-flash", "Gemini 2.5 Flash — fast, free tier available, $0.30/$2.50 per MTok (recommended)"),
        ("gemini-3-flash-preview", "Gemini 3 Flash Preview — frontier performance, free tier, $0.50/$3 per MTok"),
        ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite Preview — cheapest next-gen, free tier, $0.25/$1.50"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro — best reasoning, paid only, $1.25/$10 per MTok"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview — most capable, paid only, $2/$12 per MTok"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — budget, free tier, $0.10/$0.40 per MTok"),
    ],
    "deepseek": [
        ("deepseek-chat", "DeepSeek V3.2 Chat — fast, strong, 128K ctx, $0.28/$0.42 per MTok (recommended)"),
        ("deepseek-reasoner", "DeepSeek V3.2 Reasoner — thinking mode, 128K ctx, $0.28/$0.42 per MTok"),
    ],
    "kimi": [
        ("kimi-k2.5", "Kimi K2.5 — multimodal (vision + tools), 256K ctx, $0.60/$3.00 per MTok (recommended)"),
        ("kimi-k2-thinking", "Kimi K2 Thinking — advanced reasoning, 256K ctx, $0.60/$2.50 per MTok"),
        ("kimi-k2", "Kimi K2 — text + code, 256K ctx, $0.60/$2.50 per MTok"),
    ],
    "ollama-cloud": [
        ("qwen3.5:cloud", "Qwen 3.5 Cloud — largest, strong reasoning (recommended)"),
        ("glm-5.1:cloud", "GLM 5.1 — Zhipu AI, agentic engineering"),
        ("qwen3-coder-next:cloud", "Qwen3 Coder Next — coding-focused"),
        ("deepseek-v3.2:cloud", "DeepSeek V3.2 — 671B MoE, strong reasoning"),
        ("nemotron-3-super:cloud", "Nemotron 3 Super — NVIDIA, strong tool-use"),
        ("devstral-2:cloud", "Devstral 2 — Mistral, coding-focused"),
        ("glm-5:cloud", "GLM-5 — Zhipu AI, general-purpose"),
        ("minimax-m2.7:cloud", "MiniMax M2.7 — fast, general-purpose"),
        ("kimi-k2.5:cloud", "Kimi K2.5 — Moonshot, multimodal"),
        ("gemma4:cloud", "Gemma 4 — Google, open model"),
        ("gemini-3-flash-preview:cloud", "Gemini 3 Flash Preview — Google, fast"),
    ],
}

# Free models available on OpenRouter (no billing required)
# Updated April 12, 2026 — verified against costgoat.com/pricing/openrouter-free-models
# IMPORTANT: Only models with tool-use/function-calling support are listed.
# MyOldMachine needs tool-use to control the machine.
# Rate limits: 20 requests/minute, 200 requests/day.
OPENROUTER_FREE_MODELS = [
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron Super 120B — NVIDIA, tools + reasoning, 262K ctx (recommended)"),
    ("google/gemma-4-31b-it:free", "Gemma 4 31B — Google, vision + tools, 262K ctx"),
    ("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B — Google, vision + tools, 262K ctx"),
    ("qwen/qwen3-coder:free", "Qwen3 Coder — Alibaba, coding + tool-use, 262K ctx"),
    ("qwen/qwen3-next-80b-a3b-instruct:free", "Qwen3 Next 80B — large MoE, tool-use, 262K ctx"),
    ("nvidia/nemotron-3-nano-30b-a3b:free", "Nemotron Nano 30B — NVIDIA, tool-use, 256K ctx"),
    ("minimax/minimax-m2.5:free", "MiniMax M2.5 — tool-use, 197K ctx"),
    ("openai/gpt-oss-120b:free", "GPT-OSS 120B — OpenAI open-source, strong tool-use, 131K ctx"),
    ("openai/gpt-oss-20b:free", "GPT-OSS 20B — OpenAI open-source, fast, tool-use, 131K ctx"),
    ("z-ai/glm-4.5-air:free", "GLM 4.5 Air — Zhipu AI, tool-use, 131K ctx"),
    ("arcee-ai/trinity-large-preview:free", "Arcee Trinity Large — reasoning + tool-use, 131K ctx"),
    ("nvidia/nemotron-nano-12b-v2-vl:free", "Nemotron Nano 12B VL — NVIDIA, vision + tool-use, 128K ctx"),
    ("nvidia/nemotron-nano-9b-v2:free", "Nemotron Nano 9B — NVIDIA, tool-use, 128K ctx"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B — Meta, solid all-rounder, tool-use, 66K ctx"),
    ("google/gemma-3-27b-it:free", "Gemma 3 27B — Google, vision + tool-use, 131K ctx"),
]

# Providers that need an API key
API_KEY_PROVIDERS = {"claude-api", "openai", "deepseek", "grok", "kimi", "gemini", "ollama-cloud", "openrouter"}

# Step-by-step API key guides for each provider.
# Shown during setup when the user picks a provider.
# URLs verified March 17, 2026.
API_KEY_GUIDES = {
    "claude-api": {
        "name": "Anthropic",
        "url": "https://console.anthropic.com/settings/keys",
        "steps": [
            "Go to console.anthropic.com/settings/keys",
            "  (redirects to platform.claude.com/settings/keys)",
            "Sign up with email, Google, or SSO",
            "Add a payment method under Billing (pay-as-you-go, no minimum)",
            "Click 'Create Key', name it, and copy it immediately",
            "  The full key is shown only once — starts with sk-ant-",
        ],
        "notes": [
            "New accounts may receive initial free credits.",
            "You can set spending limits to avoid surprises.",
        ],
    },
    "openai": {
        "name": "OpenAI",
        "url": "https://platform.openai.com/api-keys",
        "steps": [
            "Go to platform.openai.com/api-keys",
            "Sign up or log in (separate from ChatGPT login)",
            "Add a payment method under Billing and buy credits",
            "Click 'Create new secret key', name it, and copy it immediately",
            "  The full key is shown only once — starts with sk-",
        ],
        "notes": [
            "The API platform account is separate from ChatGPT.",
            "You must add credits before API calls will work.",
        ],
    },
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://platform.deepseek.com/api_keys",
        "steps": [
            "Go to platform.deepseek.com/api_keys",
            "Sign up with email, Google, or GitHub",
            "Top up your balance (very cheap — $0.28/$0.42 per MTok)",
            "Click 'Create API Key', name it, and copy it immediately",
            "  The full key is shown only once — starts with sk-",
        ],
        "notes": [
            "Extremely affordable. $2 of credits lasts a long time.",
            "90% discount on cached input tokens.",
        ],
    },
    "grok": {
        "name": "xAI (Grok)",
        "url": "https://console.x.ai",
        "steps": [
            "Go to console.x.ai and create an account",
            "  No credit card required for signup",
            "In the left sidebar, click 'API Keys'",
            "Click 'Create API Key', name it, and copy it immediately",
            "  The full key is shown only once",
        ],
        "notes": [
            "$25 free credits on signup — no payment needed.",
            "Opt into data sharing for $150/month additional free credits.",
        ],
    },
    "kimi": {
        "name": "Moonshot AI (Kimi)",
        "url": "https://platform.moonshot.ai",
        "steps": [
            "Go to platform.moonshot.ai",
            "Sign up with Google or email",
            "In the left sidebar, click 'API Keys'",
            "Click 'Create API Key', name it, and copy it immediately",
            "  The full key is shown only once",
        ],
        "notes": [
            "OpenAI-compatible API — same format, different base URL.",
            "K2.5 supports vision and tool calling. 256K context.",
        ],
    },
    "gemini": {
        "name": "Google AI (Gemini)",
        "url": "https://aistudio.google.com/apikey",
        "steps": [
            "Go to aistudio.google.com/apikey",
            "Sign in with your Google account",
            "Click 'Create API key'",
            "Choose 'Create API key in new project' (easiest)",
            "Copy the key — Google lets you view it again later",
        ],
        "notes": [
            "Free tier available — no credit card required:",
            "  Gemini 2.5 Pro:         5 RPM,  100 req/day",
            "  Gemini 2.5 Flash:      10 RPM,  250 req/day",
            "  Gemini 2.5 Flash-Lite: 15 RPM, 1000 req/day",
        ],
    },
    "ollama-cloud": {
        "name": "Ollama Cloud",
        "url": "https://ollama.com/settings/keys",
        "steps": [
            "Go to ollama.com and create an account (or sign in)",
            "Go to ollama.com/settings/keys",
            "Click 'Create API key', name it, and copy it immediately",
            "  API keys don't expire but can be revoked anytime",
        ],
        "notes": [
            "Free tier available — session limits reset every 5 hours, weekly reset every 7 days.",
            "Pro ($20/mo) and Max ($100/mo) tiers for heavier usage.",
            "Same API as local Ollama — just runs in the cloud.",
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "url": "https://openrouter.ai/settings/keys",
        "steps": [
            "Go to openrouter.ai and click 'Sign up'",
            "  Sign up with email, Google, or GitHub",
            "Go to openrouter.ai/settings/keys",
            "Click 'Create Key', name it, and copy it immediately",
            "  The full key is shown only once — starts with sk-or-",
        ],
        "notes": [
            "Free models available — no billing required.",
            "Free tier: 20 requests/min, 200 requests/day.",
            "Add credits only if you want access to paid models.",
        ],
    },
}


def _print_api_key_guide(provider: str):
    """Print the step-by-step API key guide for a provider."""
    guide = API_KEY_GUIDES.get(provider)
    if not guide:
        return
    print()
    print(f"  {BOLD}How to get your {guide['name']} API key:{NC}")
    print()
    for step in guide["steps"]:
        # Indented sub-steps start with spaces
        if step.startswith("  "):
            print(f"    {YELLOW}{step}{NC}")
        else:
            print(f"    → {step}")
    if guide.get("notes"):
        print()
        for note in guide["notes"]:
            if note.startswith("  "):
                print(f"      {note}")
            else:
                print(f"    {GREEN}{note}{NC}")
    print()


def _select_model_for_provider(config: dict, provider: str):
    """Ask user to select a model for the given provider. Updates config in place."""
    if provider == "openrouter":
        print()
        print(f"  {BOLD}Free models (no billing required):{NC}")
        for i, (model_id, desc) in enumerate(OPENROUTER_FREE_MODELS, 1):
            print(f"    {i}. {desc}")
            print(f"       ID: {model_id}")
        print()
        print(f"  Or enter any OpenRouter model ID (see openrouter.ai/models)")
        print()
        default_model = DEFAULT_MODELS["openrouter"]
        raw = ask(f"Model (number or ID)", default=default_model)
        if raw.isdigit() and 1 <= int(raw) <= len(OPENROUTER_FREE_MODELS):
            config["llm_model"] = OPENROUTER_FREE_MODELS[int(raw) - 1][0]
        else:
            config["llm_model"] = raw
    elif provider in PROVIDER_MODELS:
        models = PROVIDER_MODELS[provider]
        print()
        print(f"  {BOLD}Available models:{NC}")
        for i, (model_id, desc) in enumerate(models, 1):
            print(f"    {i}. {desc}")
            print(f"       ID: {model_id}")
        print()
        print(f"  Or enter any model ID manually.")
        print()
        default_model = DEFAULT_MODELS.get(provider, models[0][0])
        raw = ask(f"Model (number or ID)", default=default_model)
        if raw.isdigit() and 1 <= int(raw) <= len(models):
            config["llm_model"] = models[int(raw) - 1][0]
        else:
            config["llm_model"] = raw
    else:
        default_model = DEFAULT_MODELS.get(provider, "")
        config["llm_model"] = ask(f"Model", default=default_model)


def write_env(repo_dir: Path, config: dict):
    """Write configuration to .env file."""
    lines = [
        f"TELEGRAM_BOT_TOKEN={config['telegram_token']}",
        f"LLM_PROVIDER={config['llm_provider']}",
        f"LLM_MODEL={config['llm_model']}",
        f"LLM_API_KEY={config.get('llm_api_key', '')}",
        f"ALLOWED_USERS={config['telegram_user_id']}",
        f"BOT_NAME={config['bot_name']}",
        f"TIMEZONE={config['timezone']}",
        f"INSTALL_MODE={config.get('takeover', 'workstation')}",
        f"WEBHOOK_PORT=0",
    ]
    if config["llm_provider"] == "ollama":
        lines.append(f"OLLAMA_BASE_URL={config.get('ollama_url', 'http://localhost:11434')}")

    env_file = repo_dir / ".env"
    env_file.write_text("\n".join(lines) + "\n")
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    ok(f"Configuration saved to {env_file}")


def write_user_profile(repo_dir: Path, config: dict, machine_specs: dict):
    """Write initial user profile and memories."""
    data_dir = repo_dir / "data"
    users_dir = data_dir / "users" / str(config["telegram_user_id"])
    users_dir.mkdir(parents=True, exist_ok=True)

    profiles = {
        str(config["telegram_user_id"]): {
            "name": config["user_name"],
            "display_name": config["user_name"],
            "role": "admin",
            "can_install": True,
            "can_restart": True,
            "blocked_skills": [],
        }
    }
    profiles_file = data_dir / "users.json"
    profiles_file.write_text(json.dumps(profiles, indent=2) + "\n")

    memories = [
        {
            "content": f"User's name is {config['user_name']}",
            "timestamp": datetime.now().isoformat(),
        },
        {
            "content": f"Machine: {machine_specs.get('hostname', 'unknown')} / "
                       f"{machine_specs.get('cpu', 'unknown')} / "
                       f"{machine_specs.get('ram_gb', '?')}GB RAM / "
                       f"{machine_specs.get('disk_gb', '?')}GB disk",
            "timestamp": datetime.now().isoformat(),
        },
    ]
    memories_file = users_dir / "memories.json"
    memories_file.write_text(json.dumps(memories, indent=2) + "\n")

    # Memory directory structure
    memory_dir = data_dir / "memory"
    for subdir in ["projects", "topics", "decisions"]:
        (memory_dir / subdir).mkdir(parents=True, exist_ok=True)

    ok(f"User profile created for {config['user_name']}")


def store_sudo_password(password: str):
    """Store sudo password securely."""
    sudo_file = Path.home() / ".sudo_pass"
    sudo_file.write_text(password + "\n")
    sudo_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    ok("Sudo password stored")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, default=str(REPO_DIR))
    parser.add_argument("--os", type=str, choices=["linux", "macos"], default="linux")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)
    detected_os = args.os

    # --- OS Detection ---
    if not checkpoint_done("wizard_os_detect"):
        from install.os_detect import detect as detect_os_info, print_detection_summary
        os_info = detect_os_info()
        print(f"\n{BOLD}Detected System{NC}")
        print_detection_summary(os_info)

        if os_info.blockers:
            print()
            for b in os_info.blockers:
                error(b)

        if os_info.warnings:
            print()
            proceed = input("  Continue despite warnings? (Y/n): ").strip().lower()
            if proceed == "n":
                info("Aborted. Address the warnings above and try again.")
                sys.exit(0)

        checkpoint_set("wizard_os_detect")
        print()
    else:
        ok("OS detection (cached)")

    # Check if configuration already done (resume case)
    env_valid = False
    if (repo_dir / ".env").exists() and checkpoint_done("wizard_config"):
        config = _load_config_from_env(repo_dir)
        # Validate critical fields exist — stale .env from a previous install
        # may have wrong provider, missing token, etc.
        required_fields = ["telegram_token", "telegram_user_id", "llm_provider", "llm_model"]
        missing = [f for f in required_fields if not config.get(f)]
        if missing:
            warn(f"Existing config is missing: {', '.join(missing)}")
            warn("Running wizard again to get correct values...")
        else:
            info(".env already exists, skipping configuration steps")
            env_valid = True

    if env_valid:
        pass  # config already loaded above
    else:
        config = _run_wizard_steps(detected_os)
        # Detect machine specs
        info("Detecting machine specs...")
        machine_specs = detect_machine_specs()
        print(f"  Hostname: {machine_specs.get('hostname', '?')}")
        print(f"  CPU: {machine_specs.get('cpu', '?')}")
        print(f"  RAM: {machine_specs.get('ram_gb', '?')} GB")
        print(f"  Disk: {machine_specs.get('disk_free_gb', '?')} GB free / {machine_specs.get('disk_gb', '?')} GB total")
        if machine_specs.get("gpu"):
            print(f"  GPU: {machine_specs['gpu']}")
        print()

        info("Saving configuration...")
        write_env(repo_dir, config)
        write_user_profile(repo_dir, config, machine_specs)
        store_sudo_password(config["sudo_pass"])
        checkpoint_set("wizard_config")

    # --- Provisioning ---
    # Verify provisioning actually completed — check for key binaries.
    # Old versions of this script set the checkpoint even on failure.
    provisioning_valid = checkpoint_done("provisioning")
    if provisioning_valid:
        import shutil as _shutil
        missing = [b for b in ["ffmpeg", "sox", "jq", "htop", "tmux"]
                   if not _shutil.which(b)]
        if missing:
            warn(f"Provisioning checkpoint set but missing packages: {', '.join(missing)}")
            warn("Re-running provisioning...")
            provisioning_valid = False

    if not provisioning_valid:
        print(f"\n{BOLD}System Provisioning{NC}")
        takeover = config.get("takeover", "workstation")
        print("  The installer will now configure your machine.")
        if takeover == "headless":
            print("  This will remove desktop software and install the bot's dependencies.")
            print("  The machine will become a headless server — no GUI.")
        elif takeover == "workstation":
            print("  This will install the bot's dependencies plus creative/desktop apps.")
            print("  Your desktop stays intact. You can use the machine normally.")
        else:
            print("  This will install the bot's dependencies without removing existing software.")
        print()

        dry_run_first = input("  Preview changes first (dry run)? (Y/n): ").strip().lower()
        if dry_run_first != "n":
            info("Running dry run — no changes will be made...")
            result = subprocess.run(
                [sys.executable, str(repo_dir / "install" / "provisioner.py"),
                 "--repo-dir", str(repo_dir), "--takeover", takeover, "--dry-run"],
            )
            if result.returncode != 0:
                warn("Dry run finished with warnings (see output above)")
            print()
            proceed = input("  Proceed with actual provisioning? (Y/n): ").strip().lower()
        else:
            proceed = input("  Continue? (Y/n): ").strip().lower()

        if proceed == "n":
            ok("Configuration saved. Run the provisioner later with:")
            print(f"  python {repo_dir}/install/provisioner.py --repo-dir {repo_dir} --takeover {takeover}")
            sys.exit(0)

        result = subprocess.run(
            [sys.executable, str(repo_dir / "install" / "provisioner.py"),
             "--repo-dir", str(repo_dir), "--takeover", takeover],
        )
        if result.returncode != 0:
            warn("Provisioning had some issues (see output above). Continuing with service setup.")
            warn("Re-run the installer to retry provisioning.")
        else:
            ok("Provisioning complete")
            checkpoint_set("provisioning")
    else:
        ok("Provisioning (cached)")

    # --- Claude CLI install (if provider is claude) ---
    if config.get("llm_provider") == "claude" and not checkpoint_done("claude_cli"):
        import shutil as _shutil

        # After provisioning, npm/node may be installed but not yet in this
        # process's PATH. Search common locations as a fallback.
        def _find_npm():
            npm = _shutil.which("npm")
            if npm:
                return npm
            for candidate in [
                "/usr/local/bin/npm",
                "/opt/homebrew/bin/npm",
                str(Path.home() / ".nvm/current/bin/npm"),
            ]:
                if Path(candidate).exists():
                    return candidate
            return None

        def _switch_provider_fallback():
            """Claude CLI failed — let user pick a different provider without restarting."""
            print()
            warn("Claude CLI requires Node.js and npm, which could not be installed.")
            print()
            print(f"  {GREEN}You can switch to a different provider now.{NC}")
            print(f"  {GREEN}Tip: OpenRouter has free models and doesn't need Node.js.{NC}")
            print()
            providers_without_cli = [p for p in _ALL_LLM_PROVIDERS if p[0] != "claude"]
            new_provider = ask_choice(
                "Pick a different provider:", providers_without_cli, default="openrouter",
            )
            config["llm_provider"] = new_provider
            _select_model_for_provider(config, new_provider)

            # Handle API key for the new provider
            if new_provider in API_KEY_PROVIDERS:
                _print_api_key_guide(new_provider)
                guide = API_KEY_GUIDES.get(new_provider, {})
                key_label = f"{guide.get('name', new_provider)} API key"
                config["llm_api_key"] = ask(key_label, secret=True)
            else:
                config["llm_api_key"] = ""

            # Rewrite .env with new provider
            write_env(repo_dir, config)
            ok(f"Switched to {new_provider} ({config['llm_model']})")
            checkpoint_set("claude_cli")  # Mark as done — no CLI needed anymore

        if _shutil.which("claude"):
            ok("Claude Code CLI already installed")
            print(f"  {YELLOW}Run 'claude login' to authenticate with your Anthropic plan.{NC}")
            checkpoint_set("claude_cli")
        else:
            npm_path = _find_npm()
            if not npm_path:
                _switch_provider_fallback()
            else:
                info("Installing Claude Code CLI...")
                try:
                    result = subprocess.run(
                        [npm_path, "install", "-g", "@anthropic-ai/claude-code"],
                        timeout=180,
                    )
                except subprocess.TimeoutExpired:
                    result = None
                if result and result.returncode == 0:
                    ok("Claude Code CLI installed")
                    print()
                    print(f"  {BOLD}IMPORTANT: You need to authenticate before the bot can work.{NC}")
                    print(f"  {YELLOW}Run this command now:{NC}")
                    print(f"    claude login")
                    print(f"  {YELLOW}This opens your browser to sign in with your Anthropic account.{NC}")
                    print(f"  {YELLOW}Your Pro/Max plan covers usage — no API credits needed.{NC}")
                    print()
                    checkpoint_set("claude_cli")
                else:
                    _switch_provider_fallback()

    # --- Ollama install (if provider is ollama) ---
    if config.get("llm_provider") == "ollama" and not checkpoint_done("ollama_setup"):
        from install.ollama_setup import (
            install_ollama, ensure_ollama_running, pull_model as ollama_pull_model,
            verify_model, is_ollama_installed,
        )

        model = config.get("llm_model", DEFAULT_MODELS.get("ollama", "llama3.1:8b"))
        print(f"\n{BOLD}Ollama Setup{NC}")
        info(f"Target model: {model}")
        print()

        # Step 1: Install Ollama if needed
        if not is_ollama_installed():
            info("Installing Ollama...")
            if not install_ollama():
                warn("Automatic Ollama installation failed.")
                warn("Install manually:")
                if platform.system() == "Darwin":
                    warn("  brew install ollama")
                    warn("  -- or --")
                    warn("  Download from https://ollama.com/download/mac")
                else:
                    warn("  curl -fsSL https://ollama.com/install.sh | sh")
                warn(f"Then run: ollama pull {model}")
                warn("Then re-run the installer.")
                sys.exit(1)
        else:
            ok("Ollama is already installed")

        # Step 2: Ensure Ollama server is running
        info("Starting Ollama server...")
        if not ensure_ollama_running():
            warn("Could not start Ollama automatically. Trying manual start...")
            # Last resort: start serve directly and wait
            try:
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                import time as _time
                _time.sleep(5)
                # Verify
                check = subprocess.run(
                    ["ollama", "list"],
                    capture_output=True, text=True, timeout=10
                )
                if check.returncode != 0:
                    warn("Ollama server won't start. You may need to start it manually:")
                    warn("  ollama serve")
                    warn("Then re-run the installer.")
                    sys.exit(1)
            except Exception as e:
                warn(f"Failed to start Ollama: {e}")
                warn("Start it manually: ollama serve")
                warn("Then re-run the installer.")
                sys.exit(1)
        ok("Ollama server is running")

        # Step 3: Pull the model
        info(f"Pulling model: {model}")
        info("This may take a while depending on model size and connection speed...")
        if not ollama_pull_model(model):
            warn(f"Failed to pull model {model}.")
            warn(f"Try manually: ollama pull {model}")
            warn("Then re-run the installer.")
            sys.exit(1)

        # Step 4: Verify model responds
        verify_model(model)

        ok(f"Ollama ready with model: {model}")
        checkpoint_set("ollama_setup")

        # Update .env with the confirmed model
        env_file = repo_dir / ".env"
        if env_file.exists():
            env_content = env_file.read_text()
            lines = env_content.splitlines()
            new_lines = []
            for line in lines:
                if line.startswith("LLM_MODEL="):
                    new_lines.append(f"LLM_MODEL={model}")
                else:
                    new_lines.append(line)
            env_file.write_text("\n".join(new_lines) + "\n")
            env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # Preserve 600
        print()

    # --- Service setup ---
    if not checkpoint_done("service"):
        result = subprocess.run(
            [sys.executable, str(repo_dir / "install" / "service.py"),
             "--repo-dir", str(repo_dir)],
        )
        if result.returncode != 0:
            warn("Service setup had issues (see output above).")
            warn("You can start the bot manually: cd " + str(repo_dir) + " && .venv/bin/python bot.py")
        else:
            ok("Service registered")
            checkpoint_set("service")
    else:
        ok("Service setup (cached)")

    # --- Done — clean up checkpoints only if everything succeeded ---
    if checkpoint_done("provisioning") and checkpoint_done("service"):
        checkpoint_file = Path(CHECKPOINT_FILE)
        if checkpoint_file.exists():
            checkpoint_file.unlink()

    print()
    print(f"{BOLD}╔══════════════════════════════════════╗{NC}")
    print(f"{BOLD}║         Setup Complete!              ║{NC}")
    print(f"{BOLD}╚══════════════════════════════════════╝{NC}")
    print()
    print(f"  Your bot ({config.get('bot_name', 'MyOldMachine')}) is now running.")
    print(f"  Open Telegram and send /start to your bot.")
    print()
    print(f"  {GREEN}The bot is registered as a system service.{NC}")
    print(f"  It will start automatically on boot and restart on crash.")
    print(f"  You can close this terminal — the bot keeps running.")
    print()
    print(f"  Useful commands:")
    print(f"    /status  — Check bot status")
    print(f"    /health  — System health report")
    print(f"    /update  — Update to latest version")
    print(f"    /help    — See all commands")
    print()
    if detected_os == "linux":
        print(f"  Service management:")
        print(f"    sudo systemctl status myoldmachine")
        print(f"    sudo systemctl restart myoldmachine")
        print(f"    journalctl -u myoldmachine -f")
    else:
        print(f"  Service management:")
        print(f"    launchctl list | grep myoldmachine")
        print(f"    tail -f {repo_dir}/data/logs/bot.log")
    print()
    print(f"  {YELLOW}If this worked for you, consider giving it a star:{NC}")
    print(f"  {BOLD}https://github.com/nickathens/MyOldMachine{NC}")
    print()


def _run_wizard_steps(detected_os: str) -> dict:
    """Run the interactive wizard steps and return config dict."""
    config = {}

    # Step 1: User identity
    print(f"\n{BOLD}Step 1: About You{NC}")
    config["user_name"] = ask("What's your name?")

    # Step 2: Telegram
    print(f"\n{BOLD}Step 2: Telegram Bot{NC}")
    print("  You need a Telegram bot token. Here's how to get one:")
    print("    1. Open Telegram and search for @BotFather")
    print("    2. Send /newbot and follow the prompts")
    print("    3. Copy the token it gives you")
    print()
    config["telegram_token"] = ask("Paste your bot token")

    print()
    print("  Now you need your Telegram user ID:")
    print("    1. Search for @userinfobot on Telegram")
    print("    2. Send /start — it will reply with your ID")
    print()
    raw_id = ask("Your Telegram user ID")
    if not raw_id.isdigit():
        error("Telegram user ID must be a number.")
    config["telegram_user_id"] = raw_id

    # Step 3: LLM Provider
    print(f"\n{BOLD}Step 3: AI Provider{NC}")
    print("  Choose which AI model will power your assistant.")
    print()
    print(f"  {GREEN}FREE options:{NC}")
    print(f"    - Claude Code CLI — uses your existing Anthropic Pro/Max subscription")
    print(f"    - Ollama — runs a local model on this machine (no internet needed)")
    print(f"    - Ollama Cloud — cloud-hosted models, free tier (no local GPU needed)")
    print(f"    - OpenRouter — has free models (20 RPM, 200 req/day)")
    print(f"    - Gemini — free tier with real quota (5-15 RPM, 100-1000 RPD)")
    print(f"    - Grok — $25 free credits on signup")
    print()
    print(f"  {YELLOW}PAID options:{NC}")
    print(f"    - Claude API — requires Anthropic API credits (separate from Pro/Max plan)")
    print(f"    - OpenAI — requires OpenAI API credits")
    print(f"    - Kimi — Moonshot AI, multimodal, 256K context ($0.60/$3.00 per MTok)")
    print()
    available_providers = _get_available_providers()
    # Default to first available provider (claude if present, otherwise claude-api)
    default_provider = available_providers[0][0] if available_providers else "openrouter"
    config["llm_provider"] = ask_choice(
        "Pick your provider:", available_providers, default=default_provider,
    )

    if config["llm_provider"] == "claude":
        _select_model_for_provider(config, "claude")
    elif config["llm_provider"] == "openrouter":
        _select_model_for_provider(config, "openrouter")
    elif config["llm_provider"] == "ollama":
        # Check compatibility first — Ollama requires macOS 12+ (Monterey)
        from install.ollama_setup import check_ollama_compatibility
        compatible, reason = check_ollama_compatibility()
        if not compatible:
            print()
            warn("Ollama cannot run on this machine:")
            for line in reason.split("\n"):
                warn(f"  {line.strip()}")
            print()
            print(f"  {GREEN}Tip: OpenRouter has free models that don't require billing.{NC}")
            print()
            # Remove ollama from the list and let user pick again
            providers_without_ollama = [p for p in available_providers if p[0] != "ollama"]
            config["llm_provider"] = ask_choice(
                "Pick a different provider:", providers_without_ollama, default="openrouter",
            )
            # Handle model selection for the newly chosen provider
            _select_model_for_provider(config, config["llm_provider"])
        else:
            # Auto-detect hardware and pick the best model — no user input needed
            print()
            info("Detecting hardware to pick the best local model...")
            try:
                benchmark_result = subprocess.run(
                    [sys.executable, str(REPO_DIR / "install" / "ollama_setup.py"),
                     "--json"],
                    capture_output=True, text=True, timeout=30,
                )
                if benchmark_result.returncode == 0 and benchmark_result.stdout.strip():
                    bench_data = json.loads(benchmark_result.stdout.strip())
                    specs = bench_data.get("specs", {})
                    recommended = bench_data.get("recommended_model")
                    explanation = bench_data.get("explanation", "")

                    print(f"  CPU:  {specs.get('cpu_name', '?')} ({specs.get('cpu_cores', '?')} cores)")
                    print(f"  RAM:  {specs.get('ram_gb', '?')} GB")
                    print(f"  Disk: {specs.get('disk_free_gb', '?')} GB free")
                    gpu = specs.get("gpu", {})
                    if gpu.get("name"):
                        print(f"  GPU:  {gpu['name']} [{gpu['type']}]")
                    print()

                    if recommended:
                        config["llm_model"] = recommended
                        ok(f"Selected model: {recommended}")
                        # Strip ANSI for clean display
                        clean_exp = re.sub(r'\033\[[0-9;]*m', '', explanation)
                        print(f"  {clean_exp}")
                    else:
                        warn("Hardware doesn't meet minimum requirements for local models.")
                        warn("Falling back to smallest available model.")
                        config["llm_model"] = "qwen2.5:0.5b"
                else:
                    warn("Benchmark returned no data. Using default model.")
                    config["llm_model"] = DEFAULT_MODELS.get("ollama", "llama3.1:8b")
            except (subprocess.TimeoutExpired, Exception) as e:
                warn(f"Hardware detection failed ({e}). Using default model.")
                config["llm_model"] = DEFAULT_MODELS.get("ollama", "llama3.1:8b")
            print()
    else:
        _select_model_for_provider(config, config["llm_provider"])

    if config["llm_provider"] == "claude":
        # Claude CLI — authenticates via 'claude login' using existing Pro/Max plan.
        # No API key. Node.js is installed during provisioning if missing.
        config["llm_api_key"] = ""
        import shutil as _shutil
        print()
        print(f"  {GREEN}Claude Code CLI uses your existing Anthropic Pro or Max plan.{NC}")
        print(f"  {GREEN}No API key or credits needed — it authenticates via your browser.{NC}")
        if not _shutil.which("claude"):
            if not _shutil.which("npm") and not _shutil.which("node"):
                print(f"  {YELLOW}Node.js will be installed automatically during system provisioning.{NC}")
            print(f"  {YELLOW}Claude Code CLI will be installed automatically after provisioning.{NC}")
        print(f"  {YELLOW}After install, run: claude login{NC}")
        print(f"  {YELLOW}This opens your browser to authenticate — no key to copy-paste.{NC}")
    elif config["llm_provider"] == "ollama":
        config["llm_api_key"] = ""
        config["ollama_url"] = "http://localhost:11434"
        import shutil as _shutil
        if _shutil.which("ollama"):
            # Verify the installed binary actually works on this OS
            from install.ollama_setup import check_ollama_compatibility
            compat, compat_reason = check_ollama_compatibility()
            if compat:
                ok("Ollama is already installed")
            else:
                warn(f"Ollama is installed but incompatible: {compat_reason.splitlines()[0]}")
                print(f"  {GREEN}Will attempt reinstall during setup.{NC}")
                config["ollama_auto_install"] = True
        else:
            print(f"  {GREEN}Ollama will be installed automatically.{NC}")
            config["ollama_auto_install"] = True
    elif config["llm_provider"] in API_KEY_PROVIDERS:
        # OpenRouter: tell user if their selected model is free
        if config["llm_provider"] == "openrouter":
            free_model_ids = {m[0] for m in OPENROUTER_FREE_MODELS}
            is_free = config["llm_model"] in free_model_ids or config["llm_model"].endswith(":free")
            if is_free:
                print(f"\n  {GREEN}Free model selected — no billing required.{NC}")
        _print_api_key_guide(config["llm_provider"])
        guide = API_KEY_GUIDES.get(config["llm_provider"], {})
        key_label = f"{guide.get('name', config['llm_provider'])} API key"
        config["llm_api_key"] = ask(key_label, secret=True)

    # Step 4: Bot name and timezone
    print(f"\n{BOLD}Step 4: Personalization{NC}")
    config["bot_name"] = ask("What should your bot call itself?", default="MyOldMachine")
    detected_tz = detect_timezone()
    config["timezone"] = ask("Timezone", default=detected_tz)

    # Step 5: Install mode
    print(f"\n{BOLD}Step 5: Install Mode{NC}")
    print(f"  All modes register the bot as a system service that:")
    print(f"    - Starts automatically on boot")
    print(f"    - Restarts automatically on crash")
    print(f"    - Runs 24/7 without you touching a terminal")
    print()
    config["takeover"] = ask_choice(
        "Choose your install mode:",
        [
            ("workstation", "Full workstation — keeps your desktop, installs creative apps "
             "(Blender, GIMP, Inkscape, LibreOffice), all skills enabled (recommended)"),
            ("minimal", "Minimal — bot runs as background service, "
             "your apps and settings stay untouched, skills self-install on first use"),
            ("headless", "Headless server — strips the desktop environment, "
             "disables sleep, turns the machine into a dedicated bot appliance"),
        ],
        default="workstation",
    )

    # Step 6: Sudo password
    print(f"\n{BOLD}Step 6: System Access{NC}")
    print("  The bot needs your password stored locally so it can install software on its own.")
    print("  Stored at ~/.sudo_pass (readable only by you, never sent anywhere).")

    sudo_cached = False
    try:
        sudo_cached = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, timeout=5
        ).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if sudo_cached:
        ok("Administrator access already active (from installer)")
        print("  Enter your password below so the bot can use it later.")

    sudo_pass = ask("Sudo/admin password", secret=True)

    info("Verifying password...")
    try:
        verify = subprocess.run(
            ["sudo", "-S", "echo", "ok"],
            input=sudo_pass + "\n",
            capture_output=True, text=True, timeout=10
        )
        if verify.returncode != 0:
            error("Password verification failed. Check your password and try again.")
    except subprocess.TimeoutExpired:
        error("Password verification timed out.")
    ok("Password verified")

    config["sudo_pass"] = sudo_pass
    return config


def _load_config_from_env(repo_dir: Path) -> dict:
    """Load config from existing .env file for resume."""
    config = {}
    env_file = repo_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key == "TELEGRAM_BOT_TOKEN":
                    config["telegram_token"] = value
                elif key == "LLM_PROVIDER":
                    config["llm_provider"] = value
                elif key == "LLM_MODEL":
                    config["llm_model"] = value
                elif key == "LLM_API_KEY":
                    config["llm_api_key"] = value
                elif key == "ALLOWED_USERS":
                    config["telegram_user_id"] = value
                elif key == "BOT_NAME":
                    config["bot_name"] = value
                elif key == "TIMEZONE":
                    config["timezone"] = value
                elif key == "INSTALL_MODE":
                    config["takeover"] = value
    config.setdefault("takeover", "workstation")
    config.setdefault("bot_name", "MyOldMachine")
    return config


if __name__ == "__main__":
    main()
