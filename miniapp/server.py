"""MyOldMachine Telegram Mini App API server.

Four panels:
  - Settings  : provider/model/effort, bot status
  - Schedule  : calendar view of reminders (per-user; admin sees all)
  - Projects  : per-user projects from data/memory/projects/ (owner-scoped)
  - Launch    : weather, media-gen, research, voice-mode, media-edit

All endpoints require a valid Telegram initData HMAC (see auth.py) and the
user must be registered in data/users.json. Writes that change global bot
config (provider, model, effort) require admin.

Bind 127.0.0.1; expose via Tailscale Funnel, Cloudflare Tunnel, or LAN.
"""

import importlib.util
import json
import logging
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

# Make MOM modules importable when running as a uvicorn child.
BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from miniapp.auth import validate_init_data  # noqa: E402

# Optional: pull provider/model catalog from the wizard so the Mini App stays
# in sync with the install flow. Falls back to a minimal hardcoded set if the
# wizard module fails to import (keeps the Mini App working in odd test envs).
try:
    from install.wizard import (  # noqa: E402
        DEFAULT_MODELS as _WIZARD_DEFAULT_MODELS,
        OPENROUTER_FREE_MODELS as _WIZARD_OPENROUTER_MODELS,
        PROVIDER_MODELS as _WIZARD_PROVIDER_MODELS,
    )
except Exception:  # pragma: no cover - very rare import path issue
    _WIZARD_PROVIDER_MODELS = {}
    _WIZARD_DEFAULT_MODELS = {}
    _WIZARD_OPENROUTER_MODELS = []

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("miniapp")

# ─── Paths and config ────────────────────────────────────────────────

load_dotenv(BOT_DIR / ".env")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

DATA_DIR = BOT_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
SKILLS_DIR = BOT_DIR / "skills"
SCHEDULER_DB = DATA_DIR / "scheduler" / "scheduler.db"
PROJECTS_DIR = DATA_DIR / "memory" / "projects"
ARCHIVE_DIR = PROJECTS_DIR / "_archive"
ENV_FILE = BOT_DIR / ".env"
STATIC_DIR = Path(__file__).parent / "static"
GENERATE_SCRIPT = SKILLS_DIR / "image-gen" / "scripts" / "generate.py"

# Service name used for status queries. Override with MINIAPP_BOT_SERVICE if
# the bot was installed under a different unit name. On Linux this is the
# systemd unit name; on macOS the LaunchAgent uses a separate label below.
BOT_SERVICE = os.getenv("MINIAPP_BOT_SERVICE", "myoldmachine")

# macOS LaunchAgent label installed by install/service.py. Override with
# MINIAPP_BOT_LAUNCHD_LABEL if a different label was used.
BOT_LAUNCHD_LABEL = os.getenv("MINIAPP_BOT_LAUNCHD_LABEL", "com.myoldmachine.bot")

# Telegram local Bot API base URL (defaults to the public bot API).
# Set TELEGRAM_API_BASE in .env to point at a local server (e.g. http://localhost:8081).
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

# Allowed keys that admins may write through Mini App. Anything else stays
# read-only — .env contains secrets we never want to expose to a webapp.
WRITABLE_ENV_KEYS = {"LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT"}

VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "X-High",
    "max": "Max",
}

# Subset of providers that accept --effort. Mirrors core/llm.py behavior.
EFFORT_PROVIDERS = {"claude", "fcc"}

# ─── App setup ───────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ─── Helpers ─────────────────────────────────────────────────────────


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _load_users() -> dict:
    data = _load_json(USERS_FILE)
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict) -> None:
    """Atomic write with fsync. Matches core/users.py semantics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _read_env_var(key: str, default: str = "") -> str:
    """Read a single variable from .env without polluting os.environ."""
    if not ENV_FILE.exists():
        return default
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return default
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$")
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            value = m.group(1)
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1]
            return value
    return default


def _write_env_var(key: str, value: str) -> None:
    """Write or update a single key in .env atomically. Preserves comments
    and ordering. Creates .env if missing. Raises on disk errors.

    Empty string is allowed for clearing a value (e.g. unset LLM_MODEL when
    a provider has no safe default).
    """
    if key not in WRITABLE_ENV_KEYS:
        raise ValueError(f"Refused to write env key {key!r}")
    if value != "" and not re.fullmatch(r"[A-Za-z0-9_.:/@\-+]+", value):
        raise ValueError(f"Refused to write env value with unsafe chars: {value!r}")

    existing_lines: list[str] = []
    if ENV_FILE.exists():
        try:
            existing_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing_lines = []

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    new_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        if pattern.match(line) and not line.lstrip().startswith("#"):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")

    content = "\n".join(new_lines) + ("\n" if not new_lines or new_lines[-1] != "" else "")
    tmp = ENV_FILE.with_suffix(ENV_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(ENV_FILE)


async def _get_user(request: Request) -> dict:
    """Validate Telegram initData, look up the user in users.json, return the
    augmented user dict. Raises 401 on auth failure, 403 if not in allowlist.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")

    user = validate_init_data(init_data, BOT_TOKEN)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = str(user.get("id", ""))
    users = _load_users()
    if user_id not in users:
        log.warning("User %s not in allowlist", user_id)
        raise HTTPException(status_code=403, detail="Not authorized")

    user["_profile"] = users[user_id]
    user["_id"] = user_id
    return user


def _is_admin(user: dict) -> bool:
    return user["_profile"].get("role") == "admin"


def _require_admin(user: dict) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")


# ─── Providers / Models / Effort ─────────────────────────────────────


def _available_providers() -> list[dict]:
    """Return providers in display order. Keep the wizard as the single
    source of truth — if the wizard knows a provider, expose it here."""
    order = [
        ("claude", "Claude Code CLI"),
        ("codex", "Codex CLI"),
        ("claude-api", "Claude API"),
        ("openai", "OpenAI"),
        ("gemini", "Gemini"),
        ("grok", "Grok"),
        ("deepseek", "DeepSeek"),
        ("kimi", "Kimi"),
        ("minimax", "MiniMax"),
        ("openrouter", "OpenRouter"),
        ("ollama", "Ollama (local)"),
        ("ollama-cloud", "Ollama Cloud"),
        ("fcc", "FCC (free-claude-code)"),
    ]
    return [{"id": pid, "label": label} for pid, label in order]


def _available_models(provider: str) -> list[dict]:
    """Return the models list for a provider. OpenRouter free models are
    surfaced as their own list; ollama returns an empty list because the
    user picks any tag they pulled themselves."""
    if provider == "openrouter":
        return [{"id": mid, "label": desc} for mid, desc in _WIZARD_OPENROUTER_MODELS]
    if provider == "ollama":
        return []
    entries = _WIZARD_PROVIDER_MODELS.get(provider, [])
    return [{"id": mid, "label": desc} for mid, desc in entries]


def _provider_supports_effort(provider: str) -> bool:
    return provider in EFFORT_PROVIDERS


def _unsupported_bot_status(service: str) -> dict:
    return {"active": False, "pid": None, "uptime_seconds": None,
            "memory_mb": None, "service": service, "supported": False}


def _ps_pid_stats(pid: int) -> tuple[int | None, int | None]:
    """Return (uptime_seconds, memory_mb) for a running PID via ps. Both
    fields may be None if ps fails or the PID has already exited."""
    try:
        result = subprocess.run(
            ["ps", "-o", "etime=,rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None, None
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        return None, None
    etime_str, rss_str = parts[0], parts[1]
    # etime format: [[DD-]HH:]MM:SS
    uptime: int | None
    try:
        days = 0
        if "-" in etime_str:
            d, etime_str = etime_str.split("-", 1)
            days = int(d)
        segments = [int(x) for x in etime_str.split(":")]
        if len(segments) == 3:
            h, m, s = segments
        elif len(segments) == 2:
            h, m, s = 0, segments[0], segments[1]
        else:
            h, m, s = 0, 0, segments[0]
        uptime = days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        uptime = None
    try:
        memory_mb: int | None = round(int(rss_str) / 1024)
    except ValueError:
        memory_mb = None
    return uptime, memory_mb


def _bot_status_linux() -> dict:
    """Read systemd unit state. Returns supported=False if systemctl is
    missing or the unit isn't registered."""
    active = False
    pid: int | None = None
    uptime_seconds: int | None = None
    memory_mb: int | None = None

    try:
        result = subprocess.run(
            ["systemctl", "is-active", BOT_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        active = result.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.SubprocessError):
        return _unsupported_bot_status(BOT_SERVICE)

    if active:
        try:
            result = subprocess.run(
                ["systemctl", "show", BOT_SERVICE,
                 "--property=MainPID,ActiveEnterTimestampMonotonic,MemoryCurrent"],
                capture_output=True, text=True, timeout=5,
            )
            monotonic_us = None
            for line in result.stdout.strip().split("\n"):
                if line.startswith("MainPID="):
                    try:
                        pid = int(line.split("=", 1)[1]) or None
                    except ValueError:
                        pass
                elif line.startswith("ActiveEnterTimestampMonotonic="):
                    val = line.split("=", 1)[1].strip()
                    if val and val != "0":
                        try:
                            monotonic_us = int(val)
                        except ValueError:
                            pass
                elif line.startswith("MemoryCurrent="):
                    val = line.split("=", 1)[1].strip()
                    if val and val != "[not set]":
                        try:
                            memory_mb = round(int(val) / 1024 / 1024)
                        except ValueError:
                            pass
            if monotonic_us is not None:
                boot_offset = int(time.time() - time.monotonic())
                active_enter_epoch = monotonic_us / 1_000_000 + boot_offset
                uptime_seconds = max(0, int(time.time() - active_enter_epoch))
        except subprocess.SubprocessError:
            pass

    return {
        "active": active,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
        "memory_mb": memory_mb,
        "service": BOT_SERVICE,
        "supported": True,
    }


def _bot_status_macos() -> dict:
    """Read launchd state for the bot's LaunchAgent. Falls through to a
    process-table scan if the agent isn't registered (e.g. when running the
    bot manually from a terminal)."""
    label = BOT_LAUNCHD_LABEL
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return _bot_status_macos_fallback(label)

    if result.returncode != 0:
        return _bot_status_macos_fallback(label)

    # `launchctl print` returns the LaunchAgent block followed by nested
    # entries (spawn settings, domain entries) which also contain "state ="
    # and other keys. We only want the top-level fields, which are indented
    # with a single leading tab. Anything more indented is a nested entry.
    pid: int | None = None
    state: str = ""
    for raw in result.stdout.splitlines():
        if not raw.startswith("\t") or raw.startswith("\t\t"):
            continue
        line = raw.strip()
        if not state and line.startswith("state ="):
            state = line.split("=", 1)[1].strip()
        elif pid is None and line.startswith("pid ="):
            try:
                pid = int(line.split("=", 1)[1].strip()) or None
            except ValueError:
                pid = None

    active = state == "running" and pid is not None
    uptime_seconds: int | None = None
    memory_mb: int | None = None
    if active and pid is not None:
        uptime_seconds, memory_mb = _ps_pid_stats(pid)

    return {
        "active": active,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
        "memory_mb": memory_mb,
        "service": label,
        "supported": True,
    }


def _bot_status_macos_fallback(label: str) -> dict:
    """If the LaunchAgent isn't registered, look for a running bot.py
    process owned by the current user. Lets the dot turn green when the bot
    is run manually (e.g. during development)."""
    bot_py = str(BOT_DIR / "bot.py")
    try:
        result = subprocess.run(
            ["pgrep", "-f", bot_py],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return _unsupported_bot_status(label)

    for token in result.stdout.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid <= 0:
            continue
        uptime_seconds, memory_mb = _ps_pid_stats(pid)
        if uptime_seconds is None and memory_mb is None:
            continue
        return {
            "active": True,
            "pid": pid,
            "uptime_seconds": uptime_seconds,
            "memory_mb": memory_mb,
            "service": label,
            "supported": True,
        }

    return {
        "active": False,
        "pid": None,
        "uptime_seconds": None,
        "memory_mb": None,
        "service": label,
        "supported": True,
    }


def _bot_status() -> dict:
    """Read the bot's service state. Dispatches by OS:
      - macOS:  launchd LaunchAgent (with a pgrep fallback for manual runs)
      - Linux:  systemd unit
      - other:  degraded status (supported=False) which the UI displays as a hint"""
    system = platform.system()
    if system == "Darwin":
        return _bot_status_macos()
    if system == "Linux":
        return _bot_status_linux()
    return _unsupported_bot_status(BOT_SERVICE)


# ─── /api/status, /api/providers, /api/model, /api/effort ────────────


@app.get("/api/status")
async def get_status(user: dict = Depends(_get_user)):
    provider = _read_env_var("LLM_PROVIDER", "claude")
    model = _read_env_var("LLM_MODEL", _WIZARD_DEFAULT_MODELS.get(provider, ""))
    effort = _read_env_var("LLM_EFFORT", "max")
    return {
        "bot": _bot_status(),
        "provider": provider,
        "available_providers": _available_providers(),
        "model": model,
        "available_models": _available_models(provider),
        "effort": effort if effort in VALID_EFFORTS else "max",
        "available_efforts": [{"id": k, "label": EFFORT_LABELS[k]} for k in VALID_EFFORTS],
        "provider_supports_effort": _provider_supports_effort(provider),
        "user": {
            "name": user["_profile"].get("display_name", user["_profile"].get("name", "User")),
            "role": user["_profile"].get("role", "user"),
        },
    }


@app.get("/api/providers/{provider_id}/models")
async def get_provider_models(provider_id: str, user: dict = Depends(_get_user)):
    return {
        "provider": provider_id,
        "models": _available_models(provider_id),
        "default_model": _WIZARD_DEFAULT_MODELS.get(provider_id, ""),
        "supports_effort": _provider_supports_effort(provider_id),
    }


@app.post("/api/provider")
async def set_provider(request: Request, user: dict = Depends(_get_user)):
    _require_admin(user)
    body = await request.json()
    provider_id = body.get("provider", "")
    valid = {p["id"] for p in _available_providers()}
    if provider_id not in valid:
        raise HTTPException(status_code=400, detail="Invalid provider")
    _write_env_var("LLM_PROVIDER", provider_id)
    default_model = _WIZARD_DEFAULT_MODELS.get(provider_id, "")
    if default_model:
        _write_env_var("LLM_MODEL", default_model)
    else:
        # No safe default for this provider (e.g. ollama — user must pick a
        # tag they have pulled locally). Clear LLM_MODEL so the bot can't try
        # to use the previous provider's model string as an ollama tag.
        _write_env_var("LLM_MODEL", "")
    return {"provider": provider_id, "model": default_model}


@app.post("/api/model")
async def set_model(request: Request, user: dict = Depends(_get_user)):
    _require_admin(user)
    body = await request.json()
    model_id = body.get("model", "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="Missing model")
    provider = _read_env_var("LLM_PROVIDER", "claude")
    valid_models = {m["id"] for m in _available_models(provider)}
    # Ollama models are user-defined (any tag the user pulled); skip validation.
    if provider != "ollama" and valid_models and model_id not in valid_models:
        raise HTTPException(status_code=400, detail="Model not valid for this provider")
    _write_env_var("LLM_MODEL", model_id)
    return {"model": model_id}


@app.post("/api/effort")
async def set_effort(request: Request, user: dict = Depends(_get_user)):
    _require_admin(user)
    body = await request.json()
    effort_id = body.get("effort", "")
    if effort_id not in VALID_EFFORTS:
        raise HTTPException(status_code=400, detail="Invalid effort level")
    _write_env_var("LLM_EFFORT", effort_id)
    return {"effort": effort_id}


# ─── /api/scheduler ─────────────────────────────────────────────────


@app.get("/api/scheduler")
async def get_scheduler(user: dict = Depends(_get_user)):
    """List scheduled jobs. Admin sees everyone; non-admin sees only their own."""
    if not SCHEDULER_DB.exists():
        return []

    conn = sqlite3.connect(str(SCHEDULER_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if _is_admin(user):
            rows = conn.execute("SELECT * FROM job_meta ORDER BY run_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM job_meta WHERE user_id = ? ORDER BY run_at",
                (int(user["_id"]),),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    return [dict(row) for row in rows]


@app.post("/api/scheduler")
async def create_job(request: Request, user: dict = Depends(_get_user)):
    """Create a new scheduled job from the Mini App calendar."""
    body = await request.json()
    date_str = body.get("date", "")
    hour = body.get("hour")
    minute = body.get("minute", 0)
    message = (body.get("message") or "").strip()
    repeat = body.get("repeat")
    until = body.get("until")

    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if len(message) > 500:
        raise HTTPException(status_code=400, detail="Message too long (500 char max)")
    if not date_str or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        raise HTTPException(status_code=400, detail="Invalid date format (YYYY-MM-DD)")
    try:
        hour = int(hour) if hour is not None else -1
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Hour must be a number 0-23")
    if not (0 <= hour <= 23):
        raise HTTPException(status_code=400, detail="Hour must be 0-23")
    try:
        minute = int(minute)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Minute must be a number 0-59")
    if not (0 <= minute <= 59):
        raise HTTPException(status_code=400, detail="Minute must be 0-59")
    if repeat and repeat not in ("daily", "weekly", "biweekly", "monthly"):
        raise HTTPException(status_code=400, detail="Invalid repeat value")
    if repeat == "":
        repeat = None

    try:
        run_at = datetime.fromisoformat(f"{date_str}T{hour:02d}:{minute:02d}:00")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date (e.g. Feb 30)")

    if not repeat and run_at < datetime.now():
        raise HTTPException(status_code=400, detail="Cannot schedule a one-time event in the past")

    end_date = None
    if until:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", until):
            raise HTTPException(status_code=400, detail="Invalid until date format (YYYY-MM-DD)")
        try:
            end_date = datetime.fromisoformat(f"{until}T23:59:59")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid until date")
        if end_date < run_at:
            raise HTTPException(status_code=400, detail="Until date must be after the start date")

    job_id = uuid.uuid4().hex
    user_id = int(user["_id"])

    from core.scheduler import _save_meta
    _save_meta(
        job_id=job_id, user_id=user_id, message=message,
        job_type="reminder", name=message[:50], notify=True,
        repeat=repeat, channel="telegram", run_at=run_at,
        raw_at=f"{date_str} {hour:02d}:{minute:02d}",
        created_context="Mini App dashboard",
        end_date=end_date,
    )

    now_iso = datetime.now().isoformat()
    return {
        "job_id": job_id,
        "user_id": user_id,
        "message": message,
        "job_type": "reminder",
        "name": message[:50],
        "notify": 1,
        "repeat": repeat,
        "channel": "telegram",
        "created_at": now_iso,
        "run_at": run_at.isoformat(),
        "end_date": end_date.isoformat() if end_date else None,
    }


@app.delete("/api/scheduler/{job_id}")
async def delete_job(job_id: str, user: dict = Depends(_get_user)):
    """Delete a scheduled job. Users may delete their own; admins delete any."""
    conn = sqlite3.connect(str(SCHEDULER_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT user_id, job_type FROM job_meta WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if not _is_admin(user) and int(row["user_id"]) != int(user["_id"]):
            raise HTTPException(status_code=403, detail="Cannot delete other user's job")
        # Protect system command jobs from non-admin deletion via Mini App.
        if row["job_type"] == "command" and not _is_admin(user):
            raise HTTPException(status_code=403, detail="System job (admin only)")
        conn.execute("DELETE FROM job_meta WHERE job_id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()

    return {"deleted": job_id}


@app.patch("/api/scheduler/{job_id}")
async def update_job(job_id: str, request: Request, user: dict = Depends(_get_user)):
    """Reword a scheduled job's message. Users may edit their own; admins edit any."""
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

    conn = sqlite3.connect(str(SCHEDULER_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT user_id FROM job_meta WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if not _is_admin(user) and int(row["user_id"]) != int(user["_id"]):
            raise HTTPException(status_code=403, detail="Cannot edit other user's job")
        conn.execute(
            "UPDATE job_meta SET message = ?, name = ? WHERE job_id = ?",
            (message, message[:50], job_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"updated": job_id, "message": message}


# ─── /api/projects ───────────────────────────────────────────────────


_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")


def _validate_slug(slug: str) -> None:
    """Reject anything that could escape PROJECTS_DIR via path joining."""
    if not _SLUG_RE.fullmatch(slug):
        raise HTTPException(status_code=400, detail="Invalid project slug")


def _user_can_see_project(state: dict, user: dict) -> bool:
    """Mirror utils/project_manager.py visibility: shared, own, or admin."""
    owner = state.get("owner") or "shared"
    if owner == "shared":
        return True
    if owner == user["_id"]:
        return True
    return _is_admin(user)


def _list_projects(user: dict) -> list[dict]:
    projects: list[dict] = []
    for base, archived in [(PROJECTS_DIR, False), (ARCHIVE_DIR, True)]:
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith("_") or d.name.startswith("."):
                continue
            sf = d / "state.json"
            if not sf.is_file():
                continue
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not _user_can_see_project(data, user):
                continue
            ns = data.get("next_steps") or []
            bl = data.get("blockers") or []
            projects.append({
                "slug": data.get("slug", d.name),
                "name": data.get("name", d.name),
                "status": data.get("status", "unknown"),
                "summary": data.get("summary", ""),
                "owner": data.get("owner", "shared"),
                "updated": data.get("updated", data.get("created", "")),
                "next_steps_count": len(ns) if isinstance(ns, list) else 0,
                "blockers_count": len(bl) if isinstance(bl, list) else 0,
                "archived": archived,
            })
    return projects


def _get_project_detail(slug: str, user: dict) -> dict | None:
    for base in [PROJECTS_DIR, ARCHIVE_DIR]:
        sf = base / slug / "state.json"
        if not sf.is_file():
            continue
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not _user_can_see_project(data, user):
            return None
        data["_archived"] = base == ARCHIVE_DIR
        return data
    return None


@app.get("/api/projects")
async def get_projects(user: dict = Depends(_get_user)):
    return _list_projects(user)


@app.get("/api/projects/{slug}")
async def get_project(slug: str, user: dict = Depends(_get_user)):
    _validate_slug(slug)
    data = _get_project_detail(slug, user)
    if not data:
        raise HTTPException(status_code=404, detail="Project not found")
    return data


def _user_can_modify_project(state: dict, user: dict) -> bool:
    """Anyone who can SEE a project can read it; only the owner or an admin
    can archive/unarchive. Shared projects are admin-only to prevent any
    allowlisted user from nuking system-wide work."""
    owner = state.get("owner") or "shared"
    if owner == user["_id"]:
        return True
    return _is_admin(user)


@app.post("/api/projects/{slug}/archive")
async def archive_project(slug: str, user: dict = Depends(_get_user)):
    _validate_slug(slug)
    src = PROJECTS_DIR / slug
    sf = src / "state.json"
    if not sf.is_file():
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not _user_can_modify_project(data, user):
        raise HTTPException(status_code=403, detail="Not authorized")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARCHIVE_DIR / slug
    if dst.exists():
        raise HTTPException(status_code=409, detail="Already archived")

    data["status"] = "archived"
    data["archived"] = time.strftime("%Y-%m-%d")
    data["updated"] = time.strftime("%Y-%m-%d")
    try:
        sf.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        src.rename(dst)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"slug": slug, "archived": True}


@app.post("/api/projects/{slug}/unarchive")
async def unarchive_project(slug: str, user: dict = Depends(_get_user)):
    _validate_slug(slug)
    src = ARCHIVE_DIR / slug
    sf = src / "state.json"
    if not sf.is_file():
        raise HTTPException(status_code=404, detail="Archived project not found")
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not _user_can_modify_project(data, user):
        raise HTTPException(status_code=403, detail="Not authorized")

    dst = PROJECTS_DIR / slug
    if dst.exists():
        raise HTTPException(status_code=409, detail="Active project with same slug exists")

    data["status"] = "in_progress"
    data.pop("archived", None)
    data["updated"] = time.strftime("%Y-%m-%d")
    try:
        sf.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        src.rename(dst)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"slug": slug, "unarchived": True}


# ─── /api/media ──────────────────────────────────────────────────────


_ALL_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"]

_PER_MODEL_RATIOS = {
    "nano_banana": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"],
    "nano_banana_flash": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"],
    "nano_banana_2": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"],
    "flux_2": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "flux_kontext": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "gpt_image_2": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
    "imagegen_2_0": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
    "grok_image": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "text2image_soul_v2": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
    "soul_cinematic": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4"],
    "soul_location": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "9:21"],
    "seedream_v4_5": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4"],
    "seedream_v5_lite": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "openai_hazel": ["1:1", "16:9", "9:16", "4:3"],
    "cinematic_studio_2_5": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "kling_omni_image": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5"],
    "image_auto": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "ms_image": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"],
    "marketing_studio_image": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"],
    "z_image": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "soul_cast": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"],
    "kling3_0": ["1:1", "16:9", "9:16"],
    "kling2_6": ["1:1", "16:9", "9:16"],
    "veo3": ["16:9", "9:16"],
    "veo3_1": ["16:9", "9:16"],
    "veo3_1_lite": ["16:9", "9:16", "1:1"],
    "seedance_2_0": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
    "seedance1_5": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
    "minimax_hailuo": [],
    "wan2_7": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "wan2_6": ["1:1", "16:9", "9:16"],
    "grok_video": ["1:1", "16:9", "9:16"],
    "cinematic_studio_3_0": ["1:1", "16:9", "9:16"],
    "cinematic_studio_video": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "cinematic_studio_video_v2": ["1:1", "16:9", "9:16", "4:3", "3:4"],
    "marketing_studio_video": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
}


@app.get("/api/media/model-ratios")
async def media_model_ratios(user: dict = Depends(_get_user)):
    try:
        spec = importlib.util.spec_from_file_location("generate", str(GENERATE_SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        aliases = {}
        for alias, jst in mod.MODEL_ALIASES.items():
            aliases[alias] = _PER_MODEL_RATIOS.get(jst, _ALL_RATIOS)
        for alias, jst in mod.VIDEO_MODEL_ALIASES.items():
            aliases[alias] = _PER_MODEL_RATIOS.get(jst, ["16:9", "9:16", "1:1"])
        return aliases
    except Exception as e:
        log.error("Model ratios failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/media/model-params")
async def media_model_params(user: dict = Depends(_get_user)):
    try:
        result = subprocess.run(
            [sys.executable, str(GENERATE_SCRIPT), "--model-params"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        log.error("Model params failed: %s", e)
    raise HTTPException(status_code=500, detail="Could not fetch model params")


@app.get("/api/media/balance")
async def media_balance(user: dict = Depends(_get_user)):
    try:
        result = subprocess.run(
            [sys.executable, str(GENERATE_SCRIPT), "--balance"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        log.error("Balance check failed: %s", e)
    raise HTTPException(status_code=500, detail="Could not fetch balance")


# ─── /api/launch ─────────────────────────────────────────────────────

LAUNCH_ACTIONS = {
    "weather": {"type": "script", "script": "weather"},
    "media-gen": {"type": "media-gen"},
    "research": {"type": "reply", "text": "What should I research?"},
    "voice-mode": {
        "type": "reply",
        "text": "Voice mode: send me a voice message and I'll reply in voice.",
    },
    "media-edit": {"type": "reply", "text": "Send me an image or video and tell me how to edit it."},
}

WEATHER_SCRIPT = SKILLS_DIR / "weather" / "scripts" / "weather.py"


def _send_bot_message(chat_id: str, text: str) -> bool:
    """Best-effort send via Telegram Bot API. Returns True on 200, False otherwise."""
    if not BOT_TOKEN:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendMessage"
    try:
        r = httpx.post(
            url,
            json={"chat_id": int(chat_id), "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code != 200:
            r2 = httpx.post(
                url,
                json={"chat_id": int(chat_id), "text": text},
                timeout=10,
            )
            return r2.status_code == 200
        return True
    except (httpx.HTTPError, ValueError, TypeError) as e:
        log.error("Failed to send bot message: %s", e)
        return False


def _run_weather() -> str:
    location = os.getenv("MINIAPP_WEATHER_LOCATION", "Athens")
    if not WEATHER_SCRIPT.is_file():
        return "Weather skill not installed. Add the 'weather' skill to use this button."
    try:
        result = subprocess.run(
            [sys.executable, str(WEATHER_SCRIPT), location],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return "Could not fetch weather data."
    except subprocess.SubprocessError as e:
        log.error("Weather script failed: %s", e)
        return "Weather check failed."


@app.post("/api/launch")
async def launch_skill(request: Request, user: dict = Depends(_get_user)):
    body = await request.json()
    skill = body.get("skill", "")

    if skill not in LAUNCH_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown skill")

    config = LAUNCH_ACTIONS[skill]
    user_id = user["_id"]

    if config["type"] == "reply":
        ok = _send_bot_message(user_id, config["text"])
        return {"ok": ok, "type": "reply"}

    if config["type"] == "script" and skill == "weather":
        text = _run_weather()
        ok = _send_bot_message(user_id, text)
        return {"ok": ok, "type": "weather"}

    if config["type"] == "media-gen":
        mg = body.get("config", {})
        mg_type = mg.get("type", "image")
        if mg_type not in ("image", "video"):
            raise HTTPException(status_code=400, detail="Invalid type")
        model = mg.get("model", "nano2")
        if not model or not model.replace("-", "").replace(".", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="Invalid model")
        aspect = mg.get("aspect_ratio", "1:1")
        valid_aspects = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9", "9:21"}
        if aspect not in valid_aspects:
            raise HTTPException(status_code=400, detail="Invalid aspect ratio")
        resolution = mg.get("resolution", "2k")
        if resolution not in ("1k", "2k", "4k"):
            raise HTTPException(status_code=400, detail="Invalid resolution")
        duration = mg.get("duration")
        if duration is not None:
            try:
                duration = int(duration)
                if duration < 1 or duration > 60:
                    raise HTTPException(status_code=400, detail="Duration must be 1-60 seconds")
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid duration")

        prompt = mg.get("prompt", "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Prompt is required")

        extra_params = mg.get("extra_params", {})
        if extra_params and not isinstance(extra_params, dict):
            extra_params = {}
        safe_extra = {}
        for k, v in extra_params.items():
            if isinstance(k, str) and len(k) < 30 and k.replace("_", "").isalpha():
                if isinstance(v, (str, bool, int, float)):
                    safe_extra[k] = v

        cost_info = ""
        try:
            cost_cmd = [sys.executable, str(GENERATE_SCRIPT), prompt[:120], "--cost", "-m", model, "-a", aspect]
            if mg_type == "image":
                cost_cmd.extend(["--resolution", resolution])
            if mg_type == "video":
                cost_cmd.append("--video")
                if duration:
                    cost_cmd.extend(["--duration", str(duration)])
            if safe_extra:
                cost_cmd.extend(["--extra", json.dumps(safe_extra)])
            cr = subprocess.run(cost_cmd, capture_output=True, text=True, timeout=15)
            if cr.returncode == 0:
                cd = json.loads(cr.stdout)
                credits = cd.get("credits", cd.get("credits_exact", "?"))
                remaining = cd.get("credits_remaining", "?")
                cost_info = f"\nEstimated cost: ~{credits} credits ({remaining} remaining)"
        except Exception as e:
            log.warning("Cost estimation failed: %s", e)

        lines = [
            f"Media Gen: {mg_type.title()} using {model}",
            f"Aspect: {aspect}",
        ]
        if mg_type == "image":
            lines.append(f"Resolution: {resolution}")
        if mg_type == "video" and duration:
            lines.append(f"Duration: {duration}s")
        for k, v in safe_extra.items():
            lines.append(f"{k.replace('_', ' ').title()}: {v}")
        if cost_info:
            lines.append(cost_info)
        lines.append(f'\nPrompt: "{prompt}"')
        lines.append("\nReply to proceed. I'll refine the prompt for this model first.")
        _send_bot_message(user_id, "\n".join(lines))

        mg["extra_params"] = safe_extra
        pending = Path(f"/tmp/media_gen_pending_{user_id}.json")
        pending.write_text(json.dumps(mg))

        return {"ok": True, "type": "media-gen"}

    raise HTTPException(status_code=400, detail="Unsupported action")


# ─── Static + health ─────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"ok": True, "service": BOT_SERVICE}


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/app", methods=["GET", "HEAD"])
async def index(request: Request):
    return FileResponse(STATIC_DIR / "index.html")
