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
from utils.env_io import atomic_secret_write as _atomic_secret_write  # noqa: E402


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


def _quick_cpu_info() -> tuple[int, str]:
    """Fast CPU probe: (logical_cores, model_string).

    Returns (0, "") on failure. Used alongside _quick_ram_gb() by the
    multi-user wizard step to size the request queue recommendation.
    Skips lspci/system_profiler so it stays well under 1s on every host.
    """
    cores = 0
    try:
        cores = os.cpu_count() or 0
    except Exception:
        cores = 0

    model = ""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                model = result.stdout.strip()
        else:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
    except Exception:
        pass
    return cores, model


def _recommend_queue(ram_gb: float, cores: int, num_users: int) -> tuple[str, str]:
    """Decide the queue default + verdict line from a hardware probe.

    Returns (queue_default, verdict_line). queue_default is "y" or "n"
    suitable for the ask() default. verdict_line is a coloured message
    summarising the per-user budget for printing to the user.

    Decision rules (per-user):
      < 4 GB RAM OR < 1 logical core   → queue strongly recommended
      < 8 GB RAM OR < 2 logical cores  → queue is a good safety net
      otherwise                        → queue optional

    RAM is the dominant signal. The cores check exists so a 32 GB box
    with only 2 cores and 4 users still gets the queue (CPU-bound LLM
    subprocesses thrash without it). When detection fails we default
    to enabling the queue, failing safe.
    """
    if ram_gb <= 0:
        # RAM is the dominant OOM signal. Without it we have no reliable
        # picture, so fail safe and default to enabling the queue.
        return "y", f"  {YELLOW}Could not detect RAM. Recommending queue by default.{NC}"

    if num_users <= 0:
        num_users = 1

    per_ram = ram_gb / num_users
    per_cores = cores / num_users if cores > 0 else None

    tight = per_ram < 4 or (per_cores is not None and per_cores < 1)
    moderate = per_ram < 8 or (per_cores is not None and per_cores < 2)

    if tight:
        return "y", f"  {YELLOW}Tight budget. Strongly recommend the request queue.{NC}"
    if moderate:
        return "y", f"  {YELLOW}Moderate budget. Queue is a good safety net.{NC}"
    return "n", f"  {GREEN}Comfortable budget. Queue is optional.{NC}"


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
    ("kimi", "Moonshot Kimi — K2.7-Code token-efficient agentic coding, 256K context, $0.95/$4.00 per MTok, machine control"),
    ("minimax", "MiniMax — M3 frontier coding, 1M context, $0.30/$1.20 per MTok, machine control"),
    ("zai", "Z.ai GLM — GLM-5.2 open-weights, long-horizon coding/agentic, 1M context, $1.40/$4.40 per MTok, machine control"),
    ("gemini", "Google Gemini — free tier available (5-15 RPM), machine control via function calling"),
    ("ollama", "Ollama — free, runs locally on your machine, machine control via function calling"),
    ("ollama-cloud", "Ollama Cloud — cloud-hosted models (free tier available), no local GPU needed"),
    ("openrouter", "OpenRouter — many models, one API key (free models available), machine control"),
    ("fcc", "Free Claude Code — proxy to free backends (Gemini, DeepSeek, Groq, etc.), full machine control"),
]


def _get_available_providers() -> list:
    """Return LLM providers available on this machine.

    Claude CLI requires Node.js — if not present, it will be installed
    automatically during provisioning. Never hide it from the user.
    """
    return list(_ALL_LLM_PROVIDERS)

DEFAULT_MODELS = {
    "claude": "claude-sonnet-5",
    "claude-api": "claude-sonnet-5",
    "codex": "gpt-5.5",
    "openai": "gpt-5.6",
    "deepseek": "deepseek-v4-flash",
    "grok": "grok-4.3",
    "kimi": "kimi-k2.7-code",
    "minimax": "MiniMax-M3",
    "zai": "glm-5.2",
    "gemini": "gemini-3.5-flash",
    "ollama": "llama3.1:8b",
    "ollama-cloud": "qwen3.5:cloud",
    "openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
    "fcc": "claude-sonnet-5",
}

# Model lists per provider — shown as numbered options during setup.
# First entry in each list is the default (recommended).
# Last updated: July 18, 2026 — Anthropic verified live against the
# platform.claude.com models overview (Sonnet 5 GA June 30 supersedes Sonnet 4.6;
# do NOT verify Anthropic against cached references, they lag launches);
# Gemini + OpenRouter queried live against their /models endpoints; OpenAI, xAI,
# DeepSeek, Kimi, MiniMax, Z.ai and Ollama Cloud verified against current docs.
PROVIDER_MODELS = {
    "claude": [
        ("claude-sonnet-5", "Claude Sonnet 5 — newest Sonnet, near-Opus performance, 1M ctx (recommended)"),
        ("claude-fable-5", "Claude Fable 5 — next-gen flagship, hardest coding + agentic work, uses plan quota fast"),
        ("claude-opus-4-8", "Claude Opus 4.8 — most capable, best agentic coding, 1M ctx"),
    ],
    "codex": [
        ("gpt-5.5", "GPT-5.5 — Codex CLI default, vision + tools, 1M ctx (recommended)"),
        ("gpt-5.6", "GPT-5.6 — newest frontier, three tiers via aliases (Sol/Terra/Luna), vision + tools"),
        ("gpt-5.4", "GPT-5.4 — flagship for Codex, native computer use, vision + tools, 1.1M ctx"),
        ("gpt-5.4-mini", "GPT-5.4 Mini — fast, lower-cost, good for lighter tasks and subagents"),
        ("gpt-5.3-codex", "GPT-5.3 Codex — specialist coding model, maximum coding depth"),
    ],
    "claude-api": [
        ("claude-sonnet-5", "Claude Sonnet 5 — newest Sonnet, 1M ctx, $3/$15 per MTok, intro $2/$10 through Aug 31 (recommended)"),
        ("claude-fable-5", "Claude Fable 5 — next-gen flagship, hardest coding + agentic work, $10/$50 per MTok"),
        ("claude-opus-4-8", "Claude Opus 4.8 — most capable, best agentic coding, 1M ctx, $5/$25 per MTok"),
        ("claude-haiku-4-5", "Claude Haiku 4.5 — fastest, cheapest, 200K ctx, $1/$5 per MTok"),
    ],
    "openai": [
        ("gpt-5.6", "GPT-5.6 — newest frontier, three tiers via aliases (Sol/Terra/Luna), vision + tools, 1M ctx, $5/$30 per MTok (recommended)"),
        ("gpt-5.5", "GPT-5.5 — prior frontier, vision + tools, 1M ctx, $5/$30 per MTok"),
        ("gpt-5.5-pro", "GPT-5.5 Pro — max intelligence, 1M ctx, $30/$180 per MTok"),
        ("gpt-5.4", "GPT-5.4 — fast frontier-class, vision + tools, 1.1M ctx, $2.50/$15 per MTok"),
        ("gpt-5.4-mini", "GPT-5.4 Mini — fast, vision + tools, $0.75/$4.50 per MTok"),
        ("gpt-5.4-nano", "GPT-5.4 Nano — cheapest 5.4-class, $0.20/$1.25 per MTok"),
        ("gpt-4.1", "GPT-4.1 — smartest non-reasoning model, 1M context, $2/$8 per MTok"),
        ("gpt-4.1-mini", "GPT-4.1 Mini — good balance, 1M ctx, $0.40/$1.60 per MTok"),
    ],
    "grok": [
        ("grok-4.3", "Grok 4.3 — flagship, 1M ctx, $1.25/$2.50 per MTok (recommended)"),
        ("grok-4.5", "Grok 4.5 — newest flagship, native video, 500K ctx, $2/$6 per MTok"),
        ("grok-build-0.1", "Grok Build 0.1 — dedicated software-engineering model, 256K ctx, $1/$2 per MTok"),
        ("grok-4-1-fast-non-reasoning", "Grok 4.1 Fast — cheapest, 2M ctx, $0.20/$0.50 per MTok"),
        ("grok-4-1-fast-reasoning", "Grok 4.1 Fast Reasoning — chain-of-thought, 2M ctx, $0.20/$0.50"),
        ("grok-4.20-0309-non-reasoning", "Grok 4.20 — prior flagship, 1M ctx, vision + tools, $1.25/$2.50 per MTok"),
        ("grok-4.20-0309-reasoning", "Grok 4.20 Reasoning — prior flagship w/ reasoning, 1M ctx, $1.25/$2.50 per MTok"),
        ("grok-4.20-multi-agent-0309", "Grok 4.20 Multi-Agent — orchestrated agents, 1M ctx, $1.25/$2.50 per MTok"),
    ],
    "gemini": [
        ("gemini-3.5-flash", "Gemini 3.5 Flash — new frontier (May 2026), free tier, $1.50/$9 per MTok (recommended)"),
        ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite — GA, cheapest next-gen, free tier, $0.25/$1.50"),
        ("gemini-3-flash-preview", "Gemini 3 Flash Preview — prior frontier, free tier, $0.50/$3 per MTok"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview — most capable, paid only, $2/$12 per MTok (≤200K)"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash — legacy, free tier, $0.30/$2.50 per MTok"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro — legacy, free tier, $1.25/$10 per MTok"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite — legacy budget, free tier, $0.10/$0.40 per MTok"),
    ],
    "deepseek": [
        ("deepseek-v4-flash", "DeepSeek V4 Flash — 1M ctx, 384K output, $0.14/$0.28 per MTok (recommended)"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro — flagship, 1M ctx, 384K output, $1.74/$3.48 per MTok"),
    ],
    "kimi": [
        ("kimi-k2.7-code", "Kimi K2.7-Code — token-efficient agentic coding, 256K ctx, $0.95/$4.00 per MTok (recommended)"),
        ("kimi-k3", "Kimi K3 — newest flagship, native vision, 1M ctx"),
        ("kimi-k2.6", "Kimi K2.6 — general-purpose, vision + text, 256K ctx, $0.95/$4.00 per MTok"),
    ],
    "minimax": [
        ("MiniMax-M3", "MiniMax M3 — frontier coding + native multimodal, 1M ctx, $0.30/$1.20 per MTok (recommended)"),
        ("MiniMax-M2.7", "MiniMax M2.7 — prior flagship, 205K ctx, $0.30/$1.20 per MTok"),
        ("MiniMax-M2.7-highspeed", "MiniMax M2.7 Highspeed — faster variant, $0.60/$2.40 per MTok"),
    ],
    "zai": [
        ("glm-5.2", "GLM-5.2 — Z.ai flagship, open-weights, long-horizon coding/agentic, 1M ctx, $1.40/$4.40 per MTok (recommended)"),
    ],
    "ollama-cloud": [
        ("qwen3.5:cloud", "Qwen 3.5 Cloud — multimodal, strong reasoning (recommended)"),
        ("minimax-m3:cloud", "MiniMax M3 — frontier coding, 1M ctx, native multimodal"),
        ("nemotron-3-ultra:cloud", "Nemotron 3 Ultra — NVIDIA, high-throughput reasoning, long-running agents"),
        ("glm-5.2:cloud", "GLM 5.2 — Zhipu AI, open-weights flagship, usable 1M ctx, long-horizon coding"),
        ("glm-5.1:cloud", "GLM 5.1 — Zhipu AI, SOTA on SWE-Bench Pro, agentic engineering"),
        ("kimi-k2.7-code:cloud", "Kimi K2.7-Code — Moonshot, token-efficient agentic coding"),
        ("kimi-k2.6:cloud", "Kimi K2.6 — Moonshot, long-horizon coding agent"),
        ("deepseek-v4-flash:cloud", "DeepSeek V4 Flash — 284B MoE, 1M ctx, efficient reasoning"),
        ("deepseek-v4-pro:cloud", "DeepSeek V4 Pro — frontier MoE, 1M ctx, three reasoning modes"),
        ("mistral-large-3:cloud", "Mistral Large 3 — frontier open model, reasoning + agentic"),
        ("nemotron-3-super:cloud", "Nemotron 3 Super — NVIDIA, 120B MoE, strong tool-use"),
        ("minimax-m2.7:cloud", "MiniMax M2.7 — fast, general-purpose"),
        ("gemma4:cloud", "Gemma 4 — Google, reasoning + agentic"),
    ],
}

# Free models available on OpenRouter (no billing required)
# Updated July 18, 2026: verified live against openrouter.ai/api/v1/models
# (filtered: prompt+completion price 0 AND "tools" in supported_parameters).
# IMPORTANT: Only models with tool-use/function-calling support are listed.
# MyOldMachine needs tool-use to control the machine.
# Rate limits: 20 requests/minute, 200 requests/day (1000/day with 10+ paid credits on file).
OPENROUTER_FREE_MODELS = [
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B — NVIDIA, tools + reasoning, 1M ctx (recommended)"),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra 550B — NVIDIA largest, tools + reasoning, 1M ctx"),
    ("qwen/qwen3-coder:free", "Qwen3 Coder 480B — Alibaba, coding + tool-use, 1M ctx"),
    ("poolside/laguna-m.1:free", "Poolside Laguna M.1 — coding agents, tools, 262K ctx"),
    ("poolside/laguna-xs-2.1:free", "Poolside Laguna XS.2.1 — small fast coding model, tools, 262K ctx"),
    ("google/gemma-4-31b-it:free", "Gemma 4 31B — Google, vision + tools, 262K ctx"),
    ("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B — Google, vision + tools, 262K ctx"),
    ("qwen/qwen3-next-80b-a3b-instruct:free", "Qwen3 Next 80B — large MoE, tool-use, 262K ctx"),
    ("nvidia/nemotron-3-nano-30b-a3b:free", "Nemotron 3 Nano 30B — NVIDIA, tool-use, 256K ctx"),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "Nemotron 3 Nano Omni — multimodal reasoning, tools, 256K ctx"),
    ("cohere/north-mini-code:free", "Cohere North Mini Code — coding + tool-use"),
    ("tencent/hy3:free", "Tencent Hunyuan 3 — tool-use, multilingual"),
    ("openrouter/free", "OpenRouter Free Router — auto-routes free models, vision + tools, 200K ctx"),
    ("openai/gpt-oss-20b:free", "GPT-OSS 20B — OpenAI open-source, fast, tool-use, 131K ctx"),
    ("nvidia/nemotron-nano-12b-v2-vl:free", "Nemotron Nano 12B VL — NVIDIA, vision + tool-use, 128K ctx"),
    ("nvidia/nemotron-nano-9b-v2:free", "Nemotron Nano 9B — NVIDIA, tool-use, 128K ctx"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B — Meta, solid all-rounder, tool-use, 131K ctx"),
]

# Providers that need an API key
API_KEY_PROVIDERS = {"claude-api", "openai", "deepseek", "grok", "kimi", "minimax", "gemini", "ollama-cloud", "openrouter", "zai"}

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
    "zai": {
        "name": "Z.ai (GLM)",
        "url": "https://z.ai/manage-apikey/apikey-list",
        "steps": [
            "Go to z.ai/model-api and sign up or log in",
            "Open z.ai/manage-apikey/apikey-list",
            "Click 'Create an API Key', name it, and copy it immediately",
            "Add funds on z.ai/manage-apikey/billing if your balance is empty",
        ],
        "notes": [
            "OpenAI-compatible API — same format, different base URL.",
            "GLM-5.2: open-weights flagship, 1M context, $1.40/$4.40 per MTok ($0.26 cached).",
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

    # Queue scope (universal = one LLM call at a time across all users,
    # per_user = each Telegram user has their own queue). Wizard wrote the
    # chosen mode to config["queue_mode"]; default to per_user.
    queue = config.get("queue_mode") or "per_user"
    if queue not in ("universal", "per_user"):
        queue = "per_user"
    lines.append(f"QUEUE_MODE={queue}")
    lines.append("CONCURRENT_REQUESTS=1" if queue == "universal" else "CONCURRENT_REQUESTS=0")

    # Local Telegram Bot API server (optional, for >50MB uploads)
    if config.get("telegram_local_api_enabled"):
        lines.append("TELEGRAM_API_BASE=http://localhost:8081")
        lines.append(f"TELEGRAM_API_ID={config.get('telegram_api_id', '')}")
        lines.append(f"TELEGRAM_API_HASH={config.get('telegram_api_hash', '')}")

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
            "added": datetime.now().isoformat(timespec="seconds"),
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

    # Memory directory structure. mkdir is best-effort; the bot creates
    # dirs on demand at runtime if a PermissionError ever surfaces here.
    memory_dir = data_dir / "memory"
    for subdir in ["projects", "topics", "decisions"]:
        try:
            (memory_dir / subdir).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass

    ok(f"User profile created for {config['user_name']}")


def store_sudo_password(password: str):
    """Store sudo password securely."""
    sudo_file = Path.home() / ".sudo_pass"
    # Atomic 0600 write: the previous write_text()+chmod() left the sudo
    # password (root access) world-readable in the window before the chmod.
    _atomic_secret_write(sudo_file, password + "\n")
    ok("Sudo password stored")


def _run_queue_mode_step(config: dict):
    """Step 5 queue scope. Picks the LLM serialization scope for the bot.

    The bot serves one OS user with multiple Telegram users sharing the
    same install. Isolation is application-level (data/users/<tid>/);
    each Telegram user always has their own per-user lock. The choice
    here is what happens when two Telegram users send a message at the
    same instant:

      universal — one LLM call at a time across the whole bot
      per_user  — each Telegram user can run an LLM call in parallel

    Hardware-constrained machines (RAM < 8 GB or cores <= 2) auto-promote
    to universal at runtime regardless of the wizard pick.

    Updates config with:
      - queue_mode (str: "universal" | "per_user")
    """
    print(f"\n{BOLD}Step 5: Queue Scope{NC}")
    print("  How LLM requests are serialized when multiple Telegram users send")
    print("  messages at once.")
    print()
    info("Measuring hardware to recommend a queue mode...")
    ram_gb = _quick_ram_gb()
    cores, cpu_model = _quick_cpu_info()
    print(f"  CPU:  {cpu_model or 'unknown'} ({cores or '?'} logical cores)")
    if ram_gb > 0:
        print(f"  RAM:  {ram_gb:.1f} GB total")
    else:
        print("  RAM:  could not detect")

    queue_default, verdict = _recommend_queue(ram_gb, cores, 1)
    print(verdict)

    print()
    print(f"    {GREEN}universal{NC}  One LLM call at a time across the whole bot.")
    print("                Other users wait. Prevents OOM on small machines.")
    print()
    print(f"    {GREEN}per-user{NC}   Each Telegram user has their own queue. Different")
    print("                users can run requests in parallel; same user's")
    print("                own messages still serialize. Best when hardware")
    print("                can comfortably run multiple LLM calls at once.")
    print()
    mode_default = "universal" if queue_default == "y" else "per-user"
    while True:
        answer = ask("Queue mode [universal/per-user]", default=mode_default).strip().lower()
        if answer in ("u", "universal"):
            config["queue_mode"] = "universal"
            ok("Queue mode: universal (one queue across all users)")
            return
        if answer in ("p", "per-user", "peruser", "per_user"):
            config["queue_mode"] = "per_user"
            ok("Queue mode: per-user (each Telegram user has their own queue)")
            return
        print(f"  {RED}Type 'universal' or 'per-user'.{NC}")


def _run_telegram_bot_api_step(config: dict):
    """Optional: collect credentials for a local Telegram Bot API server.

    Sets:
      - telegram_local_api_enabled (bool)
      - telegram_api_id (str, numeric)
      - telegram_api_hash (str, 32-char hex)
    """
    print(f"\n{BOLD}Step 5b: Local Bot API Server (optional){NC}")
    print("  Telegram's hosted Bot API caps uploads at 50MB and downloads")
    print("  at 20MB. Running a local Bot API server lifts both caps to")
    print("  ~2GB, so the bot can send and receive much larger files.")
    print()
    print(f"  {YELLOW}Trade-offs:{NC}")
    print("    - Needs a free Telegram developer api_id and api_hash")
    print("      (https://my.telegram.org — about 2 minutes)")
    print("    - Compiles from source the first time. Plan for 30-60 min")
    print("      on modern hardware, 60-120 min on older Macs.")
    print("    - Adds ~600 MB of disk for source + build artifacts.")
    print("    - Re-running the installer reuses the existing build.")
    print()

    answer = ask("Enable local Bot API server?", default="n").strip().lower()
    if answer not in ("y", "yes"):
        ok("Skipping local Bot API server. The bot will use the hosted API.")
        config["telegram_local_api_enabled"] = False
        return

    print()
    print("  Get your api_id and api_hash:")
    print("    1. Open https://my.telegram.org in a browser")
    print("    2. Sign in with your Telegram account")
    print("    3. Click 'API development tools'")
    print("    4. Fill in the form (any app name works)")
    print("    5. Copy api_id (a number) and api_hash (32-char hex string)")
    print()

    from install.telegram_bot_api import validate_credentials
    while True:
        api_id = ask("Telegram api_id (numeric)")
        api_hash = ask("Telegram api_hash (32-char hex)", secret=True)
        valid, reason = validate_credentials(api_id, api_hash)
        if valid:
            break
        warn(reason)
        retry = ask("Try again?", default="y").strip().lower()
        if retry not in ("y", "yes"):
            warn("Skipping local Bot API server.")
            config["telegram_local_api_enabled"] = False
            return

    config["telegram_local_api_enabled"] = True
    config["telegram_api_id"] = api_id.strip()
    config["telegram_api_hash"] = api_hash.strip()
    ok("Bot API credentials captured. Server will be built during install.")


# --- Helpers shared by several optional-feature entries ---
#
# These read state that lives outside .env (maintenance.json, mcp_servers.json)
# and are kept private so they don't leak into the registry's public surface.

def _load_maintenance_for_check() -> dict:
    """Read the current maintenance.json. Returns defaults if missing or
    unreadable so detection never crashes the resume path.
    """
    try:
        from utils.maintenance import load_config
        return load_config()
    except Exception:
        return {}


def _is_backup_configured() -> bool:
    cfg = _load_maintenance_for_check()
    return bool(cfg.get("backup_enabled") and cfg.get("backup_path"))


def _is_macos_updates_configured() -> bool:
    cfg = _load_maintenance_for_check()
    return bool(cfg.get("macos_system_updates"))


def _is_mcp_configured() -> bool:
    return (REPO_DIR / "mcp_servers.json").exists()


def _is_miniapp_configured() -> bool:
    from install.miniapp_setup import is_miniapp_configured
    return is_miniapp_configured(REPO_DIR)


def _run_miniapp_setup_step(config: dict):
    from install.miniapp_setup import run_miniapp_setup_step
    run_miniapp_setup_step(config, ask=ask)


def _run_backup_setup_step(config: dict):
    """Set up nightly backup destination in maintenance.json.

    Asks the user to pick a backup tool (borg or tarball), validates the
    target directory, and for borg also installs borg if missing, generates
    a passphrase, and initializes the repo.

    All settings land in maintenance.json so /maintenance can edit them at
    runtime without touching .env.
    """
    print(f"\n  {BOLD}Backup destination{NC}")
    print("  Pick a directory where nightly backups will be stored.")
    print("  Backups run at 2:00 AM. The path can be local, an external")
    print("  drive, or a network mount as long as the bot can write there.")
    print()

    tool = ask_choice(
        "Backup tool?",
        [
            ("borg", "deduplicated, encrypted, calendar pruning (recommended)"),
            ("tarball", "plain tar.gz, simpler, larger archives"),
        ],
        default="borg",
    )

    while True:
        path_str = ask("Backup target directory")
        backup_path = Path(os.path.expanduser(path_str)).resolve()
        try:
            backup_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            warn(f"Cannot create {backup_path}: {e}")
            retry = ask("Try a different path?", default="y").strip().lower()
            if retry not in ("y", "yes"):
                ok("Skipping backup setup.")
                return
            continue
        if not os.access(backup_path, os.W_OK):
            warn(f"No write permission to {backup_path}.")
            retry = ask("Try a different path?", default="y").strip().lower()
            if retry not in ("y", "yes"):
                ok("Skipping backup setup.")
                return
            continue
        break

    repo_dir = Path(__file__).parent.parent

    if tool == "borg":
        if not _setup_borg_for_wizard(backup_path, repo_dir, config):
            # Setup failed — fall back to tarball so the user still has a
            # working backup config rather than a half-broken one.
            warn("Falling back to tarball backups.")
            tool = "tarball"

    if tool == "tarball":
        retention_str = ask("How many backups to keep?", default="7")
        try:
            retention = max(1, int(retention_str))
        except ValueError:
            retention = 7
        try:
            from utils.maintenance import update_config
            update_config(
                backup_enabled=True,
                backup_tool="tarball",
                backup_path=str(backup_path),
                backup_retention=retention,
            )
        except Exception as e:
            warn(f"Could not save maintenance.json: {e}")
            return
        config["backup_enabled"] = True
        ok(f"Nightly tarball backups will go to {backup_path} (keeping {retention})")
        print(f"  {YELLOW}Restart the bot for the nightly job to register.{NC}")
        return

    # Borg succeeded — already saved the path/passphrase fields. Just confirm.
    config["backup_enabled"] = True
    ok(f"Nightly borg backups will go to {backup_path}")
    print(f"  {YELLOW}Restart the bot for the nightly job to register.{NC}")


def _setup_borg_for_wizard(backup_path: Path, repo_dir: Path,
                           config: dict) -> bool:
    """Install borg if needed, init repo, save passphrase. Returns True on
    success. Prints user-facing messages."""
    try:
        from install import borg_setup as _bs
        from utils import backup_borg as _bb
        from utils.maintenance import update_config
    except ImportError as e:
        warn(f"Borg modules not available: {e}")
        return False

    # 1. Install borg if missing.
    if not _bs.have_borg():
        info("Installing borgbackup...")
        # Wizard stores the password under "sudo_pass" (Step 7). borg_setup
        # also falls back to reading ~/.sudo_pass when None is passed.
        sudo_password = config.get("sudo_pass")
        installed, msg = _bs.install_borg(sudo_password=sudo_password)
        if not installed:
            warn(f"Could not install borg: {msg}")
            return False
        ok(msg)

    # 2. Generate or accept a passphrase.
    pass_path = _bs.passphrase_path_for(repo_dir)
    existing = _bb.read_passphrase(pass_path)
    if existing:
        info(f"Reusing existing passphrase at {pass_path}")
        passphrase = existing
    else:
        custom = ask(
            "Borg passphrase (Enter to auto-generate)",
            default="",
            required=False,
            secret=True,
        )
        passphrase = custom.strip() or _bb.generate_passphrase()
        if not _bb.write_passphrase(pass_path, passphrase):
            warn(f"Could not write passphrase to {pass_path}")
            return False
        try:
            print(f"\n  {BOLD}Borg passphrase{NC} (save this somewhere safe):")
            print(f"    {passphrase}")
            print(f"  Stored at: {pass_path} (mode 0600)")
            print(f"  {YELLOW}Without the passphrase the backup CANNOT be restored.{NC}\n")
        except Exception:
            pass

    # 3. Init repo (idempotent).
    init_ok, init_msg = _bb.init_repo(backup_path, passphrase)
    if not init_ok:
        warn(init_msg)
        return False
    ok(init_msg)

    # 4. Save settings.
    try:
        update_config(
            backup_enabled=True,
            backup_tool="borg",
            backup_path=str(backup_path),
            backup_passphrase_path=str(pass_path),
            backup_keep_daily=7,
            backup_keep_weekly=4,
            backup_keep_monthly=6,
            backup_compression="zstd,3",
        )
    except Exception as e:
        warn(f"Could not save maintenance.json: {e}")
        return False

    return True


def _run_macos_updates_step(config: dict):
    """Enable nightly macOS softwareupdate (Darwin only).

    Apple security responses already auto-install via the provisioner's
    `CriticalUpdateInstall=true`. This adds non-critical Apple updates
    (Safari, command-line tools, supplemental updates) on top.
    """
    print(f"\n  {BOLD}macOS softwareupdate{NC}")
    print("  Apple security responses already auto-install on this machine")
    print("  (the installer enabled CriticalUpdateInstall during provisioning).")
    print("  This adds non-critical Apple updates — Safari, command-line tools,")
    print("  supplemental updates — to the nightly maintenance job.")
    print()

    answer = ask("Enable macOS softwareupdate in nightly maintenance?", default="n").strip().lower()
    if answer not in ("y", "yes"):
        ok("Skipping. Apple security responses still auto-install.")
        return

    print()
    print(f"  {YELLOW}Auto-restart{NC}: some updates require a reboot to finish installing.")
    print("  When enabled, the nightly job runs `softwareupdate ... --restart`")
    print("  and the machine may reboot at 4:00 AM if any update needs it.")
    restart_answer = ask("Allow auto-restart?", default="n").strip().lower()
    auto_restart = restart_answer in ("y", "yes")

    try:
        from utils.maintenance import update_config
        update_config(
            macos_system_updates=True,
            macos_system_updates_restart=auto_restart,
        )
    except Exception as e:
        warn(f"Could not save maintenance.json: {e}")
        return

    config["macos_system_updates"] = True
    if auto_restart:
        ok("macOS softwareupdate enabled (with auto-restart).")
    else:
        ok("macOS softwareupdate enabled (no auto-restart).")


def _run_mcp_setup_step(config: dict):
    """Install the MCP SDK and create mcp_servers.json from the template.

    The user still has to edit mcp_servers.json with real server commands
    and tokens; we only scaffold so they have something to edit and the
    SDK is in place when they restart the bot.
    """
    print(f"\n  {BOLD}MCP server support{NC}")
    print("  Connects the bot to external MCP servers — databases, APIs,")
    print("  cloud services, GitHub, filesystem access, and more.")
    print("  https://modelcontextprotocol.io")
    print()
    print(f"  {YELLOW}Setup is two steps:{NC}")
    print("    1. We install the MCP Python SDK (mcp[cli], ~10 MB).")
    print("    2. We create mcp_servers.json from a template. You then")
    print("       edit it to add your servers (filesystem, github, etc.)")
    print("       and restart the bot.")
    print()

    answer = ask("Install MCP SDK and create mcp_servers.json now?", default="n").strip().lower()
    if answer not in ("y", "yes"):
        ok("Skipping MCP setup. The bot runs without MCP support.")
        return

    venv_pip = REPO_DIR / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        warn(f"Could not find {venv_pip}. Skipping SDK install.")
    else:
        info("Installing MCP SDK (mcp[cli])...")
        try:
            result = subprocess.run(
                [str(venv_pip), "install", "mcp[cli]"],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            warn("pip install timed out. Run manually: .venv/bin/pip install 'mcp[cli]'")
            return
        if result.returncode != 0:
            warn(f"pip install failed:\n{result.stderr}")
            warn("Run manually: .venv/bin/pip install 'mcp[cli]'")
            return
        ok("MCP SDK installed.")

    template = REPO_DIR / "mcp_servers.json.example"
    target = REPO_DIR / "mcp_servers.json"
    if target.exists():
        ok(f"{target.name} already exists. Edit it to add servers.")
    elif not template.exists():
        warn(f"{template} not found. Create mcp_servers.json manually.")
        return
    else:
        try:
            target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as e:
            warn(f"Could not copy template: {e}")
            return
        ok(f"Created {target.name} from the template.")

    config["mcp_enabled"] = True
    print(f"  {YELLOW}Next steps:{NC}")
    print(f"    1. Edit {target}")
    print("       (replace placeholder commands/tokens with real servers)")
    print("    2. Restart the bot to connect.")


# --- Provider authentication ---
#
# CLI providers (claude, codex) need an OAuth login before the bot can talk to
# them. Without this, the bot launches fine but every message returns the CLI's
# "Not logged in" error verbatim. The auth step probes the CLI's login state
# and, if missing, runs the interactive login flow as the install user.
#
# Hybrid retry-skip: try once, retry once on failure, then offer skip-with-
# warning. Skip is non-fatal: install completes, bot starts, but messages
# fail until the user runs `claude auth login` or `codex login` themselves.
# Decline-to-skip aborts the install (sys.exit via wizard.error()).

CLI_AUTH_PROVIDERS = ("claude", "codex")


def _find_install_cli(cmd: str) -> str | None:
    """Locate a CLI binary (claude, codex) anywhere it might have been installed.

    Mirrors the dir list in core/llm._find_cli_binary() so the wizard can
    auth even when the install user's PATH does not yet include the binary's
    location (common on macOS where ~/.local/bin is not in PATH for non-login
    shells, and on systems where npm prefix is in $HOME).
    """
    import shutil as _shutil
    path = _shutil.which(cmd)
    if path:
        return path
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / cmd,
        Path("/opt/homebrew/bin") / cmd,
        Path("/usr/local/bin") / cmd,
        home / ".npm-global" / "bin" / cmd,
        home / ".bun" / "bin" / cmd,
    ]
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        try:
            for version_dir in sorted(nvm_root.iterdir(), reverse=True):
                candidate = version_dir / "bin" / cmd
                if candidate.exists() and os.access(candidate, os.X_OK):
                    return str(candidate)
        except OSError:
            pass
    for candidate in candidates:
        try:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _claude_probe() -> tuple[bool, str]:
    """Probe `claude auth status --json`. Returns (logged_in, detail).

    detail is either the authenticated email/org, or a short reason for
    failure. Failure modes covered: binary missing, subprocess timeout,
    OS error, non-zero exit, malformed JSON, json says loggedIn=false.
    """
    binary = _find_install_cli("claude")
    if not binary:
        return False, "binary not found"
    try:
        result = subprocess.run(
            [binary, "auth", "status", "--json"],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return False, "auth status timed out after 20s"
    except OSError as e:
        return False, f"auth status failed: {e}"
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "non-zero exit").strip()
        return False, msg.splitlines()[0] if msg else "non-zero exit"
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return False, "could not parse auth status output"
    if data.get("loggedIn"):
        email = data.get("email") or data.get("orgName") or "(account)"
        return True, str(email)
    return False, "not logged in"


def _codex_probe() -> tuple[bool, str]:
    """Probe `codex login status`. Returns (logged_in, detail).

    Per OpenAI Codex CLI docs, `codex login status` exits 0 when logged in
    and prints the active auth method; non-zero otherwise.
    """
    binary = _find_install_cli("codex")
    if not binary:
        return False, "binary not found"
    try:
        result = subprocess.run(
            [binary, "login", "status"],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return False, "login status timed out after 20s"
    except OSError as e:
        return False, f"login status failed: {e}"
    if result.returncode == 0:
        detail = (result.stdout or "logged in").strip().splitlines()
        return True, detail[0] if detail else "logged in"
    msg = (result.stderr or result.stdout or "not logged in").strip()
    return False, msg.splitlines()[0] if msg else "not logged in"


def _run_cli_login(binary: str, login_args: list[str], hint: str) -> bool:
    """Run an interactive `<cli> login` subprocess. Returns True on exit 0.

    Inherits stdio so the user sees prompts and the OAuth URL. Catches
    KeyboardInterrupt so a Ctrl+C during login bubbles up as a normal
    failure rather than a stack trace.
    """
    print(f"  {YELLOW}{hint}{NC}")
    print()
    try:
        result = subprocess.run([binary, *login_args])
    except KeyboardInterrupt:
        print()
        warn("Sign-in cancelled (Ctrl+C).")
        return False
    except OSError as e:
        warn(f"Could not run {binary}: {e}")
        return False
    return result.returncode == 0


def _provider_auth_loop(
    *,
    title: str,
    probe,
    binary_finder,
    login_args: list[str],
    login_hint: str,
    skip_command: str,
) -> tuple[bool, str]:
    """Hybrid retry-skip loop shared by claude and codex auth flows.

    `probe` returns (logged_in, detail). `binary_finder` returns the CLI
    path (used to invoke login). `login_args` is the argv tail
    (e.g. ["auth", "login"] for claude, ["login"] for codex).

    Returns (success, message). success=False means the user explicitly
    declined to skip after the retries — caller should abort the install.
    """
    print(f"\n{BOLD}Provider authentication: {title}{NC}")

    logged_in, detail = probe()
    if logged_in:
        ok(f"{title} CLI authenticated as {detail}")
        return True, "already authenticated"

    if "binary not found" in detail:
        warn(
            f"{title} CLI binary not found. The install step above should "
            f"have installed it; this is unexpected."
        )
        warn("Skipping auth — bot will report the missing binary at startup.")
        return True, "binary missing — skipped"

    info(f"{title} CLI not authenticated ({detail}).")

    print()
    answer = ask("Sign in now?", default="y").strip().lower()
    if answer in ("n", "no"):
        warn(f"Skipping auth. Bot will not work until you run '{skip_command}'.")
        return True, "user declined sign-in — skipped"

    binary = binary_finder()
    if not binary:
        # Defensive: probe said "not authenticated" so binary existed; but
        # in case PATH changes between probe and login, bail cleanly.
        warn(f"{title} CLI binary disappeared between probe and login.")
        return True, "binary gone — skipped"

    # Attempt 1
    info(f"Running `{Path(binary).name} {' '.join(login_args)}`...")
    success = _run_cli_login(binary, login_args, login_hint)
    if success:
        logged_in, detail = probe()
        if logged_in:
            ok(f"Authenticated as {detail}")
            return True, "authenticated on first attempt"

    # Attempt 2 (retry once)
    warn("First sign-in attempt did not complete successfully.")
    print()
    answer = ask("Try again?", default="y").strip().lower()
    if answer not in ("n", "no"):
        info(f"Running `{Path(binary).name} {' '.join(login_args)}`...")
        success = _run_cli_login(binary, login_args, login_hint)
        if success:
            logged_in, detail = probe()
            if logged_in:
                ok(f"Authenticated as {detail}")
                return True, "authenticated on retry"

    # Both attempts failed — offer skip
    print()
    warn("Sign-in did not succeed.")
    print(f"  {YELLOW}You can skip this and authenticate later by running:{NC}")
    print(f"    {skip_command}")
    print(f"  {YELLOW}The bot will start, but messages will fail until you do.{NC}")
    print()
    answer = ask("Skip auth and continue install?", default="n").strip().lower()
    if answer in ("y", "yes"):
        warn(f"Skipping auth. Bot will not work until you run '{skip_command}'.")
        return True, "user skipped after retry"

    return False, "user declined to authenticate or skip"


def _run_provider_auth(config: dict) -> tuple[bool, str]:
    """Authenticate the install user with their LLM provider's CLI.

    For 'claude' and 'codex' providers, probes the CLI's login state and
    runs the interactive login flow if needed. Hybrid retry-skip on failure.

    For API-key providers (configured via LLM_API_KEY in .env) and 'ollama'
    (handled by ollama_setup), this is a no-op returning True.

    Returns (success, message). success=False means the install should abort
    (user declined to authenticate or skip).
    """
    provider = (config.get("llm_provider") or "").strip().lower()
    if not provider:
        return True, "no provider configured — skipping auth"
    if provider in API_KEY_PROVIDERS:
        return True, f"{provider} uses API key (.env LLM_API_KEY)"
    if provider == "ollama":
        return True, "ollama auth handled by ollama_setup"
    if provider == "claude":
        return _provider_auth_loop(
            title="Claude",
            probe=_claude_probe,
            binary_finder=lambda: _find_install_cli("claude"),
            login_args=["auth", "login"],
            login_hint=(
                "A browser window will open. Sign in with the Anthropic "
                "account that has your Pro/Max subscription. If running "
                "over SSH, copy the URL printed below into a browser on "
                "another machine."
            ),
            skip_command="claude auth login",
        )
    if provider == "codex":
        return _provider_auth_loop(
            title="Codex",
            probe=_codex_probe,
            binary_finder=lambda: _find_install_cli("codex"),
            login_args=["login"],
            login_hint=(
                "A browser window will open. Sign in with your ChatGPT "
                "Plus/Pro account. If running over SSH, choose 'Sign in "
                "with Device Code' in the menu and copy the URL/code "
                "into a browser on another machine."
            ),
            skip_command="codex login",
        )
    # Unknown provider — defensive: don't block install.
    return True, f"no auth probe defined for provider '{provider}'"


# --- Optional features registry ---
#
# Each entry describes a feature that did not exist in older installs. On
# resume (env_valid path) the wizard walks this list, asks the user once
# per missing feature, and writes the result back to .env (or maintenance.json,
# or mcp_servers.json — whatever the feature persists to). The downstream
# install steps (which are gated on config keys) then pick up the change
# automatically — no separate "install feature X" path needed.
#
# Each entry must provide:
#   - key: stable identifier, used by tests and for de-dup checks
#   - label, summary: human-readable strings shown in the prompt
#   - is_configured(config) -> bool: True when the feature is already set up.
#       Only the function decides where state lives; some features look at
#       the config dict (.env-backed), others read maintenance.json or check
#       a file on disk. The dict argument is always passed so .env-backed
#       features can stay simple.
#   - configure(config): runs the feature's interactive setup. Existing
#       wizard step functions (`_run_telegram_bot_api_step`, etc.) are
#       reusable as-is.
#   - applies_to() -> bool (optional): per-platform/per-environment filter.
#       If absent or returns True, the feature is always offered. Use this
#       to hide features that are not relevant on the current host (e.g.
#       macOS softwareupdate on Linux).
#
# Every feature MUST default to disabled. The whole point of resume-time
# detection is to surface new options without surprising existing users.

OPTIONAL_FEATURES = [
    {
        "key": "telegram_bot_api",
        "label": "Local Telegram Bot API server",
        "summary": (
            "Lifts upload caps from 50 MB to ~2 GB so the bot can send and "
            "receive large files. Adds a 30-60 min build step on first run."
        ),
        "applies_to": lambda: True,
        "is_configured": lambda c: bool(c.get("telegram_local_api_enabled")),
        "configure": lambda c: _run_telegram_bot_api_step(c),
    },
    {
        "key": "backup",
        "label": "Nightly backups",
        "summary": (
            "Schedules a 2:00 AM backup of bot state to a directory you "
            "choose. Pick borg (deduplicated + encrypted) or tarball "
            "(simpler). Borg default retention: 7 daily / 4 weekly / 6 monthly."
        ),
        "applies_to": lambda: True,
        "is_configured": lambda c: _is_backup_configured(),
        "configure": lambda c: _run_backup_setup_step(c),
    },
    {
        "key": "macos_system_updates",
        "label": "macOS system updates (non-critical)",
        "summary": (
            "Adds Safari, command-line tools, and supplemental Apple updates "
            "to the nightly maintenance run. Apple security responses "
            "already auto-install on this machine."
        ),
        "applies_to": lambda: platform.system() == "Darwin",
        "is_configured": lambda c: _is_macos_updates_configured(),
        "configure": lambda c: _run_macos_updates_step(c),
    },
    {
        "key": "mcp_servers",
        "label": "MCP server support",
        "summary": (
            "Connects the bot to external MCP servers (databases, GitHub, "
            "filesystem, cloud APIs). Installs the MCP SDK and scaffolds "
            "mcp_servers.json. You then edit it to add your servers."
        ),
        "applies_to": lambda: True,
        "is_configured": lambda c: _is_mcp_configured(),
        "configure": lambda c: _run_mcp_setup_step(c),
    },
    {
        "key": "miniapp",
        "label": "Telegram Mini App (visual dashboard)",
        "summary": (
            "A Telegram Mini App with four tabs: app settings (provider/model/"
            "effort), schedule (calendar of reminders), projects (per-user, "
            "with archive), and quick-launch (weather, media, research, voice, "
            "edit). Bind 127.0.0.1, expose via Tailscale Funnel (default), "
            "Cloudflare Tunnel, or LAN."
        ),
        "applies_to": lambda: platform.system() in ("Linux", "Darwin"),
        "is_configured": lambda c: _is_miniapp_configured(),
        "configure": lambda c: _run_miniapp_setup_step(c),
    },
]


def _offer_missing_optional_features(repo_dir: Path, config: dict) -> bool:
    """On resume, prompt the user about optional features they have not set up.

    Walks `OPTIONAL_FEATURES`, filters to entries that apply to the current
    host (via `applies_to`), then asks once per feature that's not yet
    configured. Default is "no" on every prompt so users who don't want
    anything new get a single line per feature ("Set up now? [n]:") and a
    quick way through.

    Returns True if at least one feature was newly enabled, in which case the
    caller should rewrite `.env` so downstream install steps see the change.
    """
    applicable = [
        f for f in OPTIONAL_FEATURES
        if f.get("applies_to", lambda: True)()
    ]
    missing = [f for f in applicable if not f["is_configured"](config)]
    if not missing:
        return False

    print(f"\n{BOLD}New optional features available since your last install{NC}")
    print(f"  {YELLOW}All default to off — answer 'n' (or just press Enter) to skip.{NC}")

    any_enabled = False
    for feat in missing:
        print(f"\n  {BOLD}{feat['label']}{NC}")
        print(f"    {feat['summary']}")
        answer = ask("Set up now?", default="n").strip().lower()
        if answer not in ("y", "yes"):
            continue
        feat["configure"](config)
        if feat["is_configured"](config):
            any_enabled = True

    return any_enabled


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
        # Surface any optional features added since this install was first
        # provisioned. If the user opts in to anything, rewrite .env so the
        # gated install steps below see the new flags.
        if _offer_missing_optional_features(repo_dir, config):
            info("Saving updated configuration...")
            write_env(repo_dir, config)
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

    # --- Provider authentication (claude / codex) ---
    # Runs after the CLI is installed so the install user has working
    # credentials before the bot first starts. Idempotent: already-
    # authenticated installs report "ok" and continue. For API-key and
    # ollama providers this is a no-op.
    auth_success, auth_msg = _run_provider_auth(config)
    if not auth_success:
        warn(f"Install aborted: {auth_msg}")
        warn("Re-run the installer when you are ready to authenticate.")
        sys.exit(1)
    if auth_msg not in (
        "already authenticated",
        "authenticated on first attempt",
        "authenticated on retry",
    ):
        info(f"Provider auth: {auth_msg}")

    # --- Local Telegram Bot API server (optional) ---
    # Runs after CLI installs so .env already has the credentials.
    # Idempotent: if the binary is already built, only the service file
    # is refreshed.
    if (config.get("telegram_local_api_enabled")
            and not checkpoint_done("telegram_bot_api")):
        print(f"\n{BOLD}Local Telegram Bot API Server{NC}")
        info("Building telegram-bot-api from source. This is a long step —")
        info("expect 30-60 min on modern hardware, longer on older machines.")
        try:
            from install.telegram_bot_api import setup_telegram_bot_api
            from install.os_detect import detect as _detect_os
            os_info = _detect_os()
            tba_ok = setup_telegram_bot_api(
                repo_dir, config, os_info,
                password=config.get("sudo_pass"),
            )
            if tba_ok:
                ok("Local Bot API server is up.")
                checkpoint_set("telegram_bot_api")
            else:
                warn(
                    "Local Bot API setup did not complete. The bot will "
                    "still work, but uploads are capped at Telegram's "
                    "default 50 MB. Re-run the installer to retry."
                )
        except Exception as e:
            warn(f"Local Bot API setup raised: {e}")
            warn("The bot will run without the local API. Re-run later to retry.")
    elif (config.get("telegram_local_api_enabled")
          and checkpoint_done("telegram_bot_api")):
        ok("Local Bot API server (cached)")

    # --- Service setup ---
    if not checkpoint_done("service"):
        service_cmd = [sys.executable, str(repo_dir / "install" / "service.py"),
                       "--repo-dir", str(repo_dir)]
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
        # Claude CLI — authenticates via 'claude auth login' using existing
        # Pro/Max plan. No API key. Node.js is installed during provisioning
        # if missing.
        config["llm_api_key"] = ""
        import shutil as _shutil
        print()
        print(f"  {GREEN}Claude Code CLI uses your existing Anthropic Pro or Max plan.{NC}")
        print(f"  {GREEN}No API key or credits needed — it authenticates via your browser.{NC}")
        if not _shutil.which("claude"):
            if not _shutil.which("npm") and not _shutil.which("node"):
                print(f"  {YELLOW}Node.js will be installed automatically during system provisioning.{NC}")
            print(f"  {YELLOW}Claude Code CLI will be installed automatically after provisioning.{NC}")
        print(f"  {YELLOW}The installer will run `claude auth login` for you when needed.{NC}")
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

    # Step 5: Queue scope
    _run_queue_mode_step(config)

    # Step 5b: Optional local Telegram Bot API server (for >50MB uploads)
    _run_telegram_bot_api_step(config)

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
                elif key == "QUEUE_MODE":
                    v = value.strip().lower()
                    if v == "universal":
                        config["queue_mode"] = "universal"
                    elif v in ("per_user", "per-user"):
                        config["queue_mode"] = "per_user"
                elif key == "CONCURRENT_REQUESTS":
                    # Legacy fallback for installs that pre-date QUEUE_MODE.
                    if "queue_mode" not in config:
                        config["queue_mode"] = "universal" if value not in ("", "0") else "per_user"
                elif key == "TELEGRAM_API_BASE":
                    if value.strip():
                        config["telegram_local_api_enabled"] = True
                elif key == "TELEGRAM_API_ID":
                    if value.strip():
                        config["telegram_api_id"] = value
                elif key == "TELEGRAM_API_HASH":
                    if value.strip():
                        config["telegram_api_hash"] = value
    config.setdefault("takeover", "workstation")
    config.setdefault("bot_name", "MyOldMachine")
    config.setdefault("queue_mode", "per_user")
    config.setdefault("telegram_local_api_enabled", False)

    # The sudo password lives outside .env (in ~/.sudo_pass, mode 0600).
    # Resume cases need the password back in `config` so install steps can
    # run `sudo -S` against machines that don't have passwordless sudo.
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        try:
            config["sudo_pass"] = sudo_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return config


if __name__ == "__main__":
    main()
