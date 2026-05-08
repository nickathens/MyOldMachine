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
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            return name in [line.strip() for line in f]
    except FileNotFoundError:
        return False


def checkpoint_set(name: str):
    with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
        f.write(name + "\n")


from utils.env_io import atomic_env_write as _atomic_env_write  # noqa: E402


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
        tz = Path("/etc/timezone").read_text(encoding="utf-8").strip()
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


def _quick_ram_gb() -> float:
    """Best-effort RAM detection that doesn't depend on the full specs probe.

    Returns total RAM in GB as a float, or 0.0 if it can't tell.
    Used by the multi-user wizard step to recommend the request queue
    before the heavier machine_specs probe runs.
    """
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / (1024 ** 3)
        else:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return 0.0


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
            with open("/proc/cpuinfo", encoding="utf-8") as f:
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
            with open("/proc/meminfo", encoding="utf-8") as f:
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
    ("codex", "OpenAI Codex CLI — uses your ChatGPT Plus/Pro plan (no API key needed), full machine control"),
    ("claude-api", "Anthropic Claude API — requires paid API credits ($), chat only, no machine control"),
    ("openai", "OpenAI — requires API key ($), machine control via function calling"),
    ("deepseek", "DeepSeek — extremely cheap ($0.14/$0.28 per MTok), 1M context, machine control"),
    ("grok", "xAI Grok — $25 free credits on signup, machine control via function calling"),
    ("kimi", "Moonshot Kimi — K2.6 long-horizon coding, 256K context, $0.95/$4.00 per MTok, machine control"),
    ("minimax", "MiniMax — M2.7 reasoning, 205K context, $0.30/$1.20 per MTok, machine control"),
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
    "codex": "gpt-5.5",
    "openai": "gpt-5.5",
    "deepseek": "deepseek-v4-flash",
    "grok": "grok-4-1-fast-non-reasoning",
    "kimi": "kimi-k2.6",
    "minimax": "MiniMax-M2.7",
    "gemini": "gemini-3-flash-preview",
    "ollama": "llama3.1:8b",
    "ollama-cloud": "qwen3.5:cloud",
    "openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
}

# Model lists per provider — shown as numbered options during setup.
# First entry in each list is the default (recommended).
# Last updated: April 28, 2026 — verified against official API docs/pricing pages
PROVIDER_MODELS = {
    "claude": [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 — fast, strong reasoning, 1M ctx (recommended)"),
        ("claude-opus-4-7", "Claude Opus 4.7 — most capable, best agentic coding, 1M ctx"),
        ("claude-opus-4-6", "Claude Opus 4.6 — legacy flagship, 1M ctx"),
    ],
    "codex": [
        ("gpt-5.5", "GPT-5.5 — Codex CLI default, vision + tools, 1M ctx (recommended)"),
        ("gpt-5.5-pro", "GPT-5.5 Pro — max intelligence, slower, 1M ctx"),
        ("gpt-5.4", "GPT-5.4 — prior frontier, vision + tools, 1.1M ctx"),
        ("gpt-5.4-mini", "GPT-5.4 Mini — fast, vision + tools"),
        ("gpt-5-codex", "GPT-5 Codex — coding-tuned variant"),
    ],
    "claude-api": [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 — fast, 1M ctx, $3/$15 per MTok (recommended)"),
        ("claude-opus-4-7", "Claude Opus 4.7 — most capable, best agentic coding, 1M ctx, $5/$25 per MTok"),
        ("claude-opus-4-6", "Claude Opus 4.6 — legacy flagship, 1M ctx, $5/$25 per MTok"),
        ("claude-haiku-4-5", "Claude Haiku 4.5 — fastest, cheapest, 200K ctx, $1/$5 per MTok"),
        ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5 — legacy, 200K ctx, $3/$15 per MTok"),
    ],
    "openai": [
        ("gpt-5.5", "GPT-5.5 — latest frontier, vision + tools, 1M ctx, $5/$30 per MTok (recommended)"),
        ("gpt-5.5-pro", "GPT-5.5 Pro — max intelligence, 1M ctx, $30/$180 per MTok"),
        ("gpt-5.4", "GPT-5.4 — prior frontier, vision + tools, 1.1M ctx, $2.50/$15 per MTok"),
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
        ("grok-4-1-fast-non-reasoning", "Grok 4.1 Fast — cheapest, 2M ctx, $0.20/$0.50 per MTok (recommended)"),
        ("grok-4-1-fast-reasoning", "Grok 4.1 Fast Reasoning — chain-of-thought, 2M ctx, $0.20/$0.50"),
        ("grok-4.20-0309-non-reasoning", "Grok 4.20 — newest flagship, 2M ctx, vision + tools, $2/$6 per MTok"),
        ("grok-4.20-0309-reasoning", "Grok 4.20 Reasoning — newest flagship w/ reasoning, 2M ctx, $2/$6 per MTok"),
        ("grok-4.20-multi-agent-0309", "Grok 4.20 Multi-Agent — orchestrated agents, 2M ctx"),
        ("grok-code-fast-1", "Grok Code Fast 1 — coding-tuned, 256K ctx, $0.20/$1.50 per MTok"),
        ("grok-4-0709", "Grok 4 — flagship, 256K ctx, $3/$15 per MTok"),
        ("grok-3-mini", "Grok 3 Mini — budget, 131K ctx, $0.30/$0.50 per MTok"),
    ],
    "gemini": [
        ("gemini-3-flash-preview", "Gemini 3 Flash Preview — frontier, free tier, $0.50/$3 per MTok (recommended)"),
        ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite Preview — cheapest next-gen, free tier, $0.25/$1.50"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview — most capable, paid only, $2/$12 per MTok"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash — legacy, paid only as of Apr 1 2026, $0.30/$2.50 per MTok"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro — legacy, paid only, $1.25/$10 per MTok"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — legacy budget, paid only, $0.10/$0.40 per MTok"),
    ],
    "deepseek": [
        ("deepseek-v4-flash", "DeepSeek V4 Flash — 1M ctx, 384K output, $0.14/$0.28 per MTok (recommended)"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro — flagship, 1M ctx, 384K output, $1.74/$3.48 per MTok"),
        ("deepseek-chat", "DeepSeek V3.2 Chat — legacy alias, 128K ctx, $0.28/$0.42 per MTok"),
        ("deepseek-reasoner", "DeepSeek V3.2 Reasoner — legacy thinking alias, 128K ctx, $0.28/$0.42 per MTok"),
    ],
    "kimi": [
        ("kimi-k2.6", "Kimi K2.6 — latest, long-horizon coding, $0.95/$4.00 per MTok (recommended)"),
        ("kimi-k2.5", "Kimi K2.5 — multimodal (vision + tools), 256K ctx, $0.60/$3.00 per MTok"),
        ("kimi-k2-thinking", "Kimi K2 Thinking — advanced reasoning, 256K ctx, $0.60/$2.50 per MTok"),
        ("kimi-k2", "Kimi K2 — text + code, 256K ctx, $0.60/$2.50 per MTok"),
    ],
    "minimax": [
        ("MiniMax-M2.7", "MiniMax M2.7 — self-evolving reasoning, 205K ctx, $0.30/$1.20 per MTok (recommended)"),
        ("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed — faster variant, $0.60/$2.40 per MTok"),
        ("MiniMax-M2.5", "MiniMax M2.5 — multimodal (vision + tools), 205K ctx, $0.30/$1.20 per MTok"),
    ],
    "ollama-cloud": [
        ("qwen3.5:cloud", "Qwen 3.5 Cloud — multimodal, strong reasoning (recommended)"),
        ("glm-5.1:cloud", "GLM 5.1 — Zhipu AI, SOTA on SWE-Bench Pro, agentic engineering"),
        ("kimi-k2.6:cloud", "Kimi K2.6 — Moonshot, long-horizon coding agent"),
        ("deepseek-v4-flash:cloud", "DeepSeek V4 Flash — 284B MoE, 1M ctx, efficient reasoning"),
        ("qwen3-coder-next:cloud", "Qwen3 Coder Next — coding-focused"),
        ("nemotron-3-super:cloud", "Nemotron 3 Super — NVIDIA, 120B MoE, strong tool-use"),
        ("devstral-2:cloud", "Devstral 2 — Mistral, software engineering agent"),
        ("glm-5:cloud", "GLM-5 — Zhipu AI, general-purpose"),
        ("minimax-m2.7:cloud", "MiniMax M2.7 — fast, general-purpose"),
        ("kimi-k2.5:cloud", "Kimi K2.5 — Moonshot, multimodal"),
        ("gemma4:cloud", "Gemma 4 — Google, reasoning + agentic"),
    ],
}

# Free models available on OpenRouter (no billing required)
# Updated April 28, 2026 — verified against openrouter.ai and provider docs.
# IMPORTANT: Only models with tool-use/function-calling support are listed.
# MyOldMachine needs tool-use to control the machine.
# Rate limits: 20 requests/minute, 200 requests/day.
OPENROUTER_FREE_MODELS = [
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron Super 120B — NVIDIA, tools + reasoning, 262K ctx (recommended)"),
    ("inclusionai/ling-2.6-1t:free", "Ling 2.6 1T — InclusionAI flagship, SWE-bench SOTA, tools, 262K ctx"),
    ("tencent/hy3-preview:free", "Tencent Hunyuan 3 Preview — tools, 262K ctx"),
    ("inclusionai/ling-2.6-flash:free", "Ling 2.6 Flash — InclusionAI, tools, 262K ctx"),
    ("google/gemma-4-31b-it:free", "Gemma 4 31B — Google, vision + tools, 262K ctx"),
    ("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B — Google, vision + tools, 262K ctx"),
    ("qwen/qwen3-coder:free", "Qwen3 Coder — Alibaba, coding + tool-use, 262K ctx"),
    ("qwen/qwen3-next-80b-a3b-instruct:free", "Qwen3 Next 80B — large MoE, tool-use, 262K ctx"),
    ("nvidia/nemotron-3-nano-30b-a3b:free", "Nemotron Nano 30B — NVIDIA, tool-use, 256K ctx"),
    ("openrouter/free", "OpenRouter Free Router — auto-routes free models, vision + tools, 200K ctx"),
    ("minimax/minimax-m2.5:free", "MiniMax M2.5 — tool-use, 197K ctx"),
    ("openai/gpt-oss-120b:free", "GPT-OSS 120B — OpenAI open-source, strong tool-use, 131K ctx"),
    ("openai/gpt-oss-20b:free", "GPT-OSS 20B — OpenAI open-source, fast, tool-use, 131K ctx"),
    ("z-ai/glm-4.5-air:free", "GLM 4.5 Air — Zhipu AI, tool-use, 131K ctx"),
    ("nvidia/nemotron-nano-12b-v2-vl:free", "Nemotron Nano 12B VL — NVIDIA, vision + tool-use, 128K ctx"),
    ("nvidia/nemotron-nano-9b-v2:free", "Nemotron Nano 9B — NVIDIA, tool-use, 128K ctx"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B — Meta, solid all-rounder, tool-use, 66K ctx"),
]

# Providers that need an API key
API_KEY_PROVIDERS = {"claude-api", "openai", "deepseek", "grok", "kimi", "minimax", "gemini", "ollama-cloud", "openrouter"}

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
    "codex": {
        "name": "OpenAI Codex CLI",
        "url": "https://chatgpt.com",
        "steps": [
            "Codex CLI is auto-installed during setup — no key needed if you have a ChatGPT plan.",
            "After install, run: codex login",
            "  Sign in with your ChatGPT Plus / Pro / Business / Edu / Enterprise account.",
            "  Your subscription covers usage — no API credits required.",
            "OR (headless / no ChatGPT plan):",
            "  Set OPENAI_API_KEY in .env. Buy credits at platform.openai.com.",
        ],
        "notes": [
            "Recommended path: ChatGPT plan + `codex login`. Free with subscription.",
            "API key path is for CI / non-interactive setups only.",
        ],
    },
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://platform.deepseek.com/api_keys",
        "steps": [
            "Go to platform.deepseek.com/api_keys",
            "Sign up with email, Google, or GitHub",
            "Top up your balance (very cheap — V4 Flash at $0.14/$0.28 per MTok)",
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
    "minimax": {
        "name": "MiniMax",
        "url": "https://platform.minimax.io",
        "steps": [
            "Go to platform.minimax.io and create an account",
            "In the left sidebar, click 'API Keys'",
            "Click 'Create new secret key', name it, and copy it immediately",
            "  You may need to complete identity verification first",
            "  Top up your balance (pay-as-you-go, no minimum)",
        ],
        "notes": [
            "OpenAI-compatible API. Very competitive pricing.",
            "M2.7: strong reasoning, 205K context, $0.30/$1.20 per MTok.",
            "M2.5: multimodal (vision + tools).",
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
        print("  Or enter any OpenRouter model ID (see openrouter.ai/models)")
        print()
        default_model = DEFAULT_MODELS["openrouter"]
        raw = ask("Model (number or ID)", default=default_model)
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
        print("  Or enter any model ID manually.")
        print()
        default_model = DEFAULT_MODELS.get(provider, models[0][0])
        raw = ask("Model (number or ID)", default=default_model)
        if raw.isdigit() and 1 <= int(raw) <= len(models):
            config["llm_model"] = models[int(raw) - 1][0]
        else:
            config["llm_model"] = raw
    else:
        default_model = DEFAULT_MODELS.get(provider, "")
        config["llm_model"] = ask("Model", default=default_model)


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
        "WEBHOOK_PORT=0",
    ]
    if config["llm_provider"] == "ollama":
        lines.append(f"OLLAMA_BASE_URL={config.get('ollama_url', 'http://localhost:11434')}")

    # Multi-user mode (slot-based isolation via system users + sudoers)
    if config.get("multiuser_enabled"):
        lines.append("MULTIUSER_ENABLED=1")
        lines.append(f"MULTIUSER_NUM_SLOTS={config['multiuser_num_slots']}")
        lines.append("MULTIUSER_ORCHESTRATOR_USER=mom_orchestrator")
        if config.get("multiuser_queue_enabled"):
            lines.append("CONCURRENT_REQUESTS=1")
        else:
            lines.append("CONCURRENT_REQUESTS=0")
    else:
        lines.append("MULTIUSER_ENABLED=0")

    env_file = repo_dir / ".env"
    _atomic_env_write(env_file, "\n".join(lines) + "\n")
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
    profiles_file.write_text(json.dumps(profiles, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

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
    memories_file.write_text(json.dumps(memories, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Memory directory structure
    memory_dir = data_dir / "memory"
    for subdir in ["projects", "topics", "decisions"]:
        (memory_dir / subdir).mkdir(parents=True, exist_ok=True)

    ok(f"User profile created for {config['user_name']}")


def store_sudo_password(password: str):
    """Store sudo password securely."""
    sudo_file = Path.home() / ".sudo_pass"
    sudo_file.write_text(password + "\n", encoding="utf-8")
    sudo_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    ok("Sudo password stored")


def _run_multiuser_step(config: dict):
    """Step 5 multi-user setup. Asks how many users will share this machine.

    Updates config with:
    - multiuser_enabled (bool)
    - multiuser_num_slots (int, 1..4)
    - multiuser_queue_enabled (bool)
    """
    print(f"\n{BOLD}Step 5: Multi-User Setup{NC}")

    if platform.system() not in ("Linux", "Darwin"):
        warn(f"Multi-user mode is not supported on {platform.system()}.")
        config["multiuser_num_slots"] = 1
        config["multiuser_enabled"] = False
        config["multiuser_queue_enabled"] = False
        return

    print("  How many people will use this machine?")
    print(f"    {GREEN}1{NC}     Just you. Nothing extra is set up.")
    print(f"    {GREEN}2-4{NC}   Multiple people share this machine.")
    print("            Each person gets a private data directory.")
    print("            The OS kernel enforces the privacy boundary;")
    print("            no one can read anyone else's files.")
    print()

    while True:
        raw = ask("Number of users (1-4)", default="1")
        try:
            num_users = int(raw)
        except ValueError:
            print(f"  {RED}Must be a number.{NC}")
            continue
        if 1 <= num_users <= 4:
            break
        print(f"  {RED}Must be between 1 and 4.{NC}")

    config["multiuser_num_slots"] = num_users
    config["multiuser_enabled"] = num_users > 1

    if num_users == 1:
        config["multiuser_queue_enabled"] = False
        ok("Single-user mode. No isolation needed.")
        return

    print()
    print(f"  {GREEN}Multi-user mode enabled ({num_users} slots).{NC}")
    print()
    admin_name = config.get("user_name", "you")
    print(f"  {BOLD}You ({admin_name}) become the admin (slot 1).{NC}")
    print("  Admin role:")
    print("    • Add/remove other users via Telegram (/adduser, /removeuser, /users)")
    print("    • View system health (/health)")
    print("    • Restart the bot if needed")
    print(f"  {YELLOW}You CANNOT read other users' data.{NC} Privacy is enforced by the kernel,")
    print("  not by the bot's code. If something breaks and you need to read")
    print("  another user's files, log in to the machine and use sudo directly.")
    print()
    if num_users >= 2:
        print(f"  Slots 2-{num_users} are reserved but unbound. After install, add users via:")
        print(f"    {BOLD}/adduser <telegram_id> <name>{NC}")
        print()

    # Queue recommendation based on RAM
    ram_gb = _quick_ram_gb()
    if ram_gb > 0:
        per_user = ram_gb / num_users
        print(f"  RAM check: {ram_gb:.1f} GB total / {num_users} users = "
              f"{per_user:.1f} GB per user")
        if per_user < 4:
            print(f"  {YELLOW}Tight memory budget. Strongly recommend the request queue:{NC}")
            queue_default = "y"
        elif per_user < 8:
            print(f"  {YELLOW}Moderate memory budget. Queue is a good safety net.{NC}")
            queue_default = "y"
        else:
            print(f"  {GREEN}Comfortable memory budget. Queue is optional.{NC}")
            queue_default = "n"
    else:
        print(f"  {YELLOW}Could not detect RAM. Recommending queue by default.{NC}")
        queue_default = "y"

    print("  The queue runs one user's request at a time. Others get a")
    print("  'next in line' message and wait. Prevents OOM on small machines.")
    print()
    answer = ask("Enable request queue?", default=queue_default).strip().lower()
    config["multiuser_queue_enabled"] = answer in ("y", "yes")
    if config["multiuser_queue_enabled"]:
        ok("Request queue will be enabled (concurrent_requests=1)")
    else:
        ok("Request queue disabled. Concurrent requests allowed.")


def _provision_multiuser(repo_dir: Path, config: dict) -> tuple[bool, str]:
    """Provision the OS-level multi-user state.

    Creates orchestrator + slot system users, sets up directory ownership
    and permissions, installs the sudoers fragment, and writes the initial
    data/orchestrator/users.json with the admin slot binding.

    Returns (success, message). Caller should error out on failure.
    Idempotent. Safe to re-run after a partial install.
    """
    from install.multiuser import (
        ORCHESTRATOR_USER, SUDOERS_FRAGMENT_PATH,
        create_system_user, find_cli_binary, grant_sudo,
        mkdir_as_root, set_owner, set_perms, slot_user,
    )

    sudo_pass = config.get("sudo_pass")
    num_slots = config["multiuser_num_slots"]

    data_root = repo_dir / "data"
    orchestrator_home = data_root / "orchestrator"
    users_root = data_root / "users"
    shared_dir = data_root / "shared"

    # 1. Create orchestrator user
    info(f"Creating orchestrator system user: {ORCHESTRATOR_USER}")
    if not create_system_user(ORCHESTRATOR_USER, password=sudo_pass,
                               home_dir=orchestrator_home):
        return False, f"failed to create {ORCHESTRATOR_USER}"

    # 2. Create slot users
    slot_users: list[str] = []
    for slot in range(1, num_slots + 1):
        name = slot_user(slot)
        info(f"Creating slot user: {name}")
        slot_home = users_root / f"user{slot}"
        if not create_system_user(name, password=sudo_pass, home_dir=slot_home):
            return False, f"failed to create {name}"
        slot_users.append(name)

    # 3. Create directories with correct ownership and perms
    info("Setting up data directories with isolation...")
    orchestrator_home.mkdir(parents=True, exist_ok=True)
    if not set_owner(orchestrator_home, ORCHESTRATOR_USER, ORCHESTRATOR_USER,
                     password=sudo_pass, recursive=True):
        return False, f"failed to chown {orchestrator_home}"
    if not set_perms(orchestrator_home, 0o700, password=sudo_pass):
        return False, f"failed to chmod {orchestrator_home}"

    # users_root is parent of every slot dir AND the location where the
    # orchestrator creates _archived/ on /removeuser. Owner = orchestrator
    # so it can write here. Mode 0755 lets slot users traverse via "other"
    # to reach their own slot dir; privacy is enforced at slot level (02770).
    users_root.mkdir(parents=True, exist_ok=True)
    if not set_owner(users_root, ORCHESTRATOR_USER, ORCHESTRATOR_USER,
                     password=sudo_pass):
        return False, f"failed to chown {users_root}"
    if not set_perms(users_root, 0o755, password=sudo_pass):
        return False, f"failed to chmod {users_root}"

    for slot in range(1, num_slots + 1):
        slot_dir = users_root / f"user{slot}"
        # users_root was just chowned to mom_orchestrator with mode 0755, so
        # the install user (who is "other" relative to that owner) no longer
        # has write permission here. A plain Path.mkdir would raise EACCES.
        # Create via sudo and let the chown/chmod below fix ownership + mode.
        if not mkdir_as_root(slot_dir, password=sudo_pass):
            return False, f"failed to create {slot_dir}"
        # Owner: mom_userN (own slot data, written by their CLI subprocess)
        # Group: mom_orchestrator (bot writes session state, pending messages here)
        # Mode 2770: setgid bit (2) ensures subdirectories created by mom_userN
        # inherit the orchestrator group, so the bot can read/clean up session
        # state without sudo. Plus rwx for owner+group, nothing for other.
        # The kernel blocks mom_userY (Y!=N) from accessing this directory.
        if not set_owner(slot_dir, slot_user(slot), ORCHESTRATOR_USER,
                         password=sudo_pass, recursive=True):
            return False, f"failed to chown {slot_dir}"
        if not set_perms(slot_dir, 0o2770, password=sudo_pass):
            return False, f"failed to chmod {slot_dir}"

    # Shared dir: read-only for slots (via "other"), writable by orchestrator only
    shared_dir.mkdir(parents=True, exist_ok=True)
    if not set_owner(shared_dir, ORCHESTRATOR_USER, ORCHESTRATOR_USER,
                     password=sudo_pass):
        return False, f"failed to chown {shared_dir}"
    if not set_perms(shared_dir, 0o775, password=sudo_pass):
        return False, f"failed to chmod {shared_dir}"

    # Cross-user bot-state dirs that the orchestrator writes to at runtime.
    # The wizard's earlier steps may have created these as the install user
    # (config.py's import-time mkdirs, write_user_profile, etc.). Re-chown
    # them so the orchestrator owns the contents. Otherwise APScheduler,
    # the memory system, and systemd's bot.log redirection all fail with
    # EACCES once the service starts as mom_orchestrator.
    for sub in ("memory", "scheduler", "identities", "logs"):
        d = data_root / sub
        d.mkdir(parents=True, exist_ok=True)
        if not set_owner(d, ORCHESTRATOR_USER, ORCHESTRATOR_USER,
                         password=sudo_pass, recursive=True):
            return False, f"failed to chown {d}"
        if not set_perms(d, 0o700, password=sudo_pass):
            return False, f"failed to chmod {d}"

    # data/users.json (legacy single-user profile file) and the data/ root
    # itself need to be readable by the orchestrator. The orchestrator does
    # not write data/, so leave install-user as owner; just guarantee perms.
    legacy_profile = data_root / "users.json"
    if legacy_profile.exists():
        # World-readable so the orchestrator can read it; install user owns.
        set_perms(legacy_profile, 0o644, password=sudo_pass)

    # 4. Resolve CLI binaries that the orchestrator is allowed to spawn
    binaries: list[Path] = []
    for cli in ("claude", "codex"):
        path = find_cli_binary(cli)
        if path:
            info(f"  Resolved {cli} -> {path}")
            binaries.append(path)
    if not binaries:
        return False, (
            "no CLI binaries (claude, codex) found in PATH. Install the CLI for "
            "your provider before running multi-user setup."
        )

    # 5. Build, validate, and install sudoers fragment
    info(f"Installing sudoers fragment at {SUDOERS_FRAGMENT_PATH}")
    success, msg = grant_sudo(
        ORCHESTRATOR_USER, slot_users, binaries, password=sudo_pass,
    )
    if not success:
        return False, f"sudoers install failed: {msg}"
    ok(msg)

    # 6. Make .env readable by the orchestrator (group=orchestrator, mode 0640)
    env_file = repo_dir / ".env"
    if env_file.exists():
        install_user = getpass.getuser()
        if not set_owner(env_file, install_user, ORCHESTRATOR_USER, password=sudo_pass):
            warn("Could not chown .env to orchestrator group. Bot may fail to read .env.")
        else:
            set_perms(env_file, 0o640, password=sudo_pass)

    # 7. Write the orchestrator's users.json with the admin slot binding.
    # Idempotency: if users.json already exists with a valid version, leave
    # it alone. Re-running the wizard must not clobber slots that the admin
    # has bound via /adduser since the original install.
    target = orchestrator_home / "users.json"
    existing_admin: dict | None = None
    try:
        cat = subprocess.run(
            ["sudo", "-S", "--", "cat", str(target)],
            input=(sudo_pass or "") + "\n",
            capture_output=True, text=True, timeout=10,
        )
        if cat.returncode == 0:
            try:
                parsed = json.loads(cat.stdout)
                if int(parsed.get("version", 0)) >= 1 and parsed.get("slots"):
                    existing_admin = parsed
            except (ValueError, TypeError, json.JSONDecodeError):
                existing_admin = None
    except (subprocess.TimeoutExpired, OSError):
        existing_admin = None

    if existing_admin:
        info(f"Existing users.json found at {target}. Preserving slot bindings.")
        return True, (
            f"multi-user system provisioned: {ORCHESTRATOR_USER} + "
            f"{num_slots} slot(s); existing slot bindings preserved"
        )

    info("Writing orchestrator slot table...")
    users_json = {
        "version": 1,
        "num_slots": num_slots,
        "max_slots": 4,
        "orchestrator_user": ORCHESTRATOR_USER,
        "queue_enabled": bool(config.get("multiuser_queue_enabled", False)),
        "concurrent_requests": 1 if config.get("multiuser_queue_enabled") else 0,
        "slots": {
            str(s): None for s in range(1, num_slots + 1)
        },
    }
    users_json["slots"]["1"] = {
        "telegram_id": str(config["telegram_user_id"]),
        "name": config.get("user_name") or "admin",
        "is_admin": True,
        "added": datetime.now().isoformat(timespec="seconds"),
    }

    # Write via temp file + sudo cp so the final file is owned by the orchestrator
    import tempfile
    fd, tmp_path = tempfile.mkstemp(prefix="mom_users_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(users_json, f, indent=2, sort_keys=True)
            f.write("\n")

        cp_cmd = ["sudo", "-S", "--", "cp", tmp_path, str(target)]
        result = subprocess.run(
            cp_cmd, input=(sudo_pass or "") + "\n",
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False, f"failed to write users.json: {result.stderr.strip()[:300]}"

        if not set_owner(target, ORCHESTRATOR_USER, ORCHESTRATOR_USER, password=sudo_pass):
            return False, "failed to chown users.json"
        if not set_perms(target, 0o600, password=sudo_pass):
            return False, "failed to chmod users.json"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return True, (
        f"multi-user system provisioned: {ORCHESTRATOR_USER} + "
        f"{num_slots} slot(s); admin bound to slot 1"
    )


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

        # Refuse install on hosts where the binary is known to crash on load
        # (macOS < 13, glibc < 2.31, musl) before npm even runs. Without this
        # the install succeeds but every claude invocation aborts with dyld
        # "Symbol not found" or "GLIBC_X.Y not found".
        from install.compat_check import cli_compat
        from install.os_detect import detect as _detect_os
        _claude_compat_ok, _claude_compat_reason = cli_compat("claude", _detect_os())

        if not _claude_compat_ok:
            warn(_claude_compat_reason)
            _switch_provider_fallback()
        elif _shutil.which("claude"):
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
                    print("    claude login")
                    print(f"  {YELLOW}This opens your browser to sign in with your Anthropic account.{NC}")
                    print(f"  {YELLOW}Your Pro/Max plan covers usage — no API credits needed.{NC}")
                    print()
                    checkpoint_set("claude_cli")
                else:
                    _switch_provider_fallback()

    # --- Codex CLI install (if provider is codex) ---
    if config.get("llm_provider") == "codex" and not checkpoint_done("codex_cli"):
        import shutil as _shutil

        def _find_npm_codex():
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

        def _switch_codex_fallback():
            """Codex CLI failed — let user pick a different provider without restarting."""
            print()
            warn("Codex CLI requires Node.js and npm, which could not be installed.")
            print()
            print(f"  {GREEN}You can switch to a different provider now.{NC}")
            print(f"  {GREEN}Tip: OpenRouter has free models and doesn't need Node.js.{NC}")
            print()
            providers_without_codex = [p for p in _ALL_LLM_PROVIDERS if p[0] != "codex"]
            new_provider = ask_choice(
                "Pick a different provider:", providers_without_codex, default="openrouter",
            )
            config["llm_provider"] = new_provider
            _select_model_for_provider(config, new_provider)

            if new_provider in API_KEY_PROVIDERS:
                _print_api_key_guide(new_provider)
                guide = API_KEY_GUIDES.get(new_provider, {})
                key_label = f"{guide.get('name', new_provider)} API key"
                config["llm_api_key"] = ask(key_label, secret=True)
            else:
                config["llm_api_key"] = ""

            write_env(repo_dir, config)
            ok(f"Switched to {new_provider} ({config['llm_model']})")
            checkpoint_set("codex_cli")

        # Refuse install on hosts where the binary is known to crash on load
        # (macOS < 13, glibc < 2.31, musl) before npm even runs.
        from install.compat_check import cli_compat
        from install.os_detect import detect as _detect_os
        _codex_compat_ok, _codex_compat_reason = cli_compat("codex", _detect_os())

        if not _codex_compat_ok:
            warn(_codex_compat_reason)
            _switch_codex_fallback()
        elif _shutil.which("codex"):
            ok("OpenAI Codex CLI already installed")
            print(f"  {YELLOW}Run 'codex login' to authenticate with your ChatGPT plan.{NC}")
            checkpoint_set("codex_cli")
        else:
            npm_path = _find_npm_codex()
            if not npm_path:
                _switch_codex_fallback()
            else:
                info("Installing OpenAI Codex CLI...")
                try:
                    result = subprocess.run(
                        [npm_path, "install", "-g", "@openai/codex"],
                        timeout=180,
                    )
                except subprocess.TimeoutExpired:
                    result = None
                if result and result.returncode == 0:
                    ok("OpenAI Codex CLI installed")
                    print()
                    print(f"  {BOLD}IMPORTANT: You need to authenticate before the bot can work.{NC}")
                    print(f"  {YELLOW}Run this command now:{NC}")
                    print("    codex login")
                    print(f"  {YELLOW}This opens your browser to sign in with your ChatGPT account.{NC}")
                    print(f"  {YELLOW}Your Plus/Pro plan covers usage — no API credits needed.{NC}")
                    print(f"  {YELLOW}(For headless setups, set OPENAI_API_KEY in .env instead.){NC}")
                    print()
                    checkpoint_set("codex_cli")
                else:
                    _switch_codex_fallback()

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
            env_content = env_file.read_text(encoding="utf-8")
            lines = env_content.splitlines()
            new_lines = []
            for line in lines:
                if line.startswith("LLM_MODEL="):
                    new_lines.append(f"LLM_MODEL={model}")
                else:
                    new_lines.append(line)
            _atomic_env_write(env_file, "\n".join(new_lines) + "\n")
        print()

    # --- Multi-user provisioning ---
    # Runs after CLI installs so the sudoers fragment can pin absolute paths.
    if config.get("multiuser_enabled") and not checkpoint_done("multiuser"):
        print(f"\n{BOLD}Multi-User Provisioning{NC}")
        info("Setting up slot-based isolation. This creates system users,")
        info("data directories with kernel-enforced permissions, and a")
        info("sudoers fragment so the bot can spawn CLI subprocesses as each slot.")
        print()
        success, msg = _provision_multiuser(repo_dir, config)
        if not success:
            error(f"Multi-user provisioning failed: {msg}")
            error("Aborting install. Re-run after fixing the cause above.")
            error("The checkpoint is NOT advanced, so re-running will retry this step.")
            sys.exit(1)
        ok(msg)
        checkpoint_set("multiuser")
    elif config.get("multiuser_enabled"):
        ok("Multi-user provisioning (cached)")

    # --- Service setup ---
    if not checkpoint_done("service"):
        service_cmd = [sys.executable, str(repo_dir / "install" / "service.py"),
                       "--repo-dir", str(repo_dir)]
        if config.get("multiuser_enabled"):
            service_cmd += ["--orchestrator-user", "mom_orchestrator"]
        result = subprocess.run(service_cmd)
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
    print("  Open Telegram and send /start to your bot.")
    print()
    print(f"  {GREEN}The bot is registered as a system service.{NC}")
    print("  It will start automatically on boot and restart on crash.")
    print("  You can close this terminal — the bot keeps running.")
    print()
    print("  Useful commands:")
    print("    /status  — Check bot status")
    print("    /health  — System health report")
    print("    /update  — Update to latest version")
    print("    /help    — See all commands")
    print()
    if detected_os == "linux":
        print("  Service management:")
        print("    sudo systemctl status myoldmachine")
        print("    sudo systemctl restart myoldmachine")
        print("    journalctl -u myoldmachine -f")
    elif config.get("multiuser_enabled"):
        print("  Service management (LaunchDaemon, requires sudo):")
        print("    sudo launchctl list | grep myoldmachine")
        print("    sudo launchctl unload /Library/LaunchDaemons/com.myoldmachine.bot.plist")
        print("    sudo launchctl load -w /Library/LaunchDaemons/com.myoldmachine.bot.plist")
        print(f"    tail -f {repo_dir}/data/logs/bot.log")
    else:
        print("  Service management:")
        print("    launchctl list | grep myoldmachine")
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
    print("    - Claude Code CLI — uses your existing Anthropic Pro/Max subscription")
    print("    - Ollama — runs a local model on this machine (no internet needed)")
    print("    - Ollama Cloud — cloud-hosted models, free tier (no local GPU needed)")
    print("    - OpenRouter — has free models (20 RPM, 200 req/day)")
    print("    - Gemini — free tier with real quota (5-15 RPM, 100-1000 RPD)")
    print("    - Grok — $25 free credits on signup")
    print()
    print(f"  {YELLOW}PAID options:{NC}")
    print("    - Claude API — requires Anthropic API credits (separate from Pro/Max plan)")
    print("    - OpenAI — requires OpenAI API credits")
    print("    - Kimi — Moonshot AI, multimodal, 256K context ($0.60/$3.00 per MTok)")
    print("    - MiniMax — M2.7 reasoning, 205K context ($0.30/$1.20 per MTok)")
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
    elif config["llm_provider"] == "codex":
        # Codex CLI — authenticates via 'codex login' using existing ChatGPT Plus/Pro plan.
        # No API key. Node.js is installed during provisioning if missing.
        config["llm_api_key"] = ""
        import shutil as _shutil
        print()
        print(f"  {GREEN}OpenAI Codex CLI uses your existing ChatGPT Plus/Pro/Business plan.{NC}")
        print(f"  {GREEN}No API key or credits needed — it authenticates via your browser.{NC}")
        if not _shutil.which("codex"):
            if not _shutil.which("npm") and not _shutil.which("node"):
                print(f"  {YELLOW}Node.js will be installed automatically during system provisioning.{NC}")
            print(f"  {YELLOW}Codex CLI will be installed automatically after provisioning.{NC}")
        print(f"  {YELLOW}After install, run: codex login{NC}")
        print(f"  {YELLOW}If you don't have a ChatGPT plan, set OPENAI_API_KEY in .env instead.{NC}")
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

    # Step 5: Multi-user setup
    _run_multiuser_step(config)

    # Step 6: Install mode
    print(f"\n{BOLD}Step 6: Install Mode{NC}")
    print("  All modes register the bot as a system service that:")
    print("    - Starts automatically on boot")
    print("    - Restarts automatically on crash")
    print("    - Runs 24/7 without you touching a terminal")
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

    # Step 7: Sudo password
    print(f"\n{BOLD}Step 7: System Access{NC}")
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
        for line in env_file.read_text(encoding="utf-8").splitlines():
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
                elif key == "MULTIUSER_ENABLED":
                    config["multiuser_enabled"] = value == "1"
                elif key == "MULTIUSER_NUM_SLOTS":
                    try:
                        config["multiuser_num_slots"] = int(value)
                    except ValueError:
                        config["multiuser_num_slots"] = 1
                elif key == "CONCURRENT_REQUESTS":
                    config["multiuser_queue_enabled"] = value not in ("", "0")
    config.setdefault("takeover", "workstation")
    config.setdefault("bot_name", "MyOldMachine")
    config.setdefault("multiuser_enabled", False)
    config.setdefault("multiuser_num_slots", 1)
    config.setdefault("multiuser_queue_enabled", False)

    # The sudo password lives outside .env (in ~/.sudo_pass, mode 0600)
    # because .env is later chowned to the orchestrator group. Resume cases
    # need the password back in `config` so multi-user provisioning can run
    # `sudo -S` against machines that don't have passwordless sudo.
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        try:
            config["sudo_pass"] = sudo_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return config


if __name__ == "__main__":
    main()
