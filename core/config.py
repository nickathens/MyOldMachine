"""
Configuration management for MyOldMachine.

All paths and settings are derived from environment variables or sensible defaults.
No hardcoded user-specific paths.
"""

import json
import os
from pathlib import Path
from typing import Optional


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key, "").strip()
    if val.lstrip('-').isdigit() and val.count('-') <= 1 and val:
        return int(val)
    return default


def _env_list(key: str) -> list[int]:
    """Parse comma-separated integers from env var."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


# Base directories (relative to project root)
BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
SKILLS_DIR = BOT_DIR / "skills"
USERS_DIR = DATA_DIR / "users"
MEMORY_DIR = DATA_DIR / "memory"
SCHEDULER_DIR = DATA_DIR / "scheduler"
IDENTITY_DIR = DATA_DIR / "identities"
LOG_DIR = DATA_DIR / "logs"

# Ensure directories exist
for d in [DATA_DIR, USERS_DIR, MEMORY_DIR, SCHEDULER_DIR, IDENTITY_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def get_telegram_token() -> str:
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN environment variable is required. "
            "Get one from @BotFather on Telegram."
        )
    return token


def get_telegram_api_base() -> Optional[str]:
    """Return custom Telegram API base URL, or None for default."""
    base = _env("TELEGRAM_API_BASE")
    return base if base else None


def get_allowed_users() -> list[int]:
    return _env_list("ALLOWED_USERS")


def get_bot_name() -> str:
    return _env("BOT_NAME", "MyOldMachine")


def get_timezone() -> str:
    return _env("TIMEZONE", "UTC")


def get_webhook_port() -> int:
    return _env_int("WEBHOOK_PORT", 0)


# LLM settings
def get_llm_provider() -> str:
    return _env("LLM_PROVIDER", "claude")


def get_llm_model() -> str:
    return _env("LLM_MODEL", "claude-sonnet-4-6")


def get_llm_api_key() -> str:
    return _env("LLM_API_KEY", "")


def get_ollama_base_url() -> str:
    return _env("OLLAMA_BASE_URL", "http://localhost:11434")


# User profiles
USERS_PROFILES_FILE = DATA_DIR / "users.json"


def load_user_profiles() -> dict:
    if not USERS_PROFILES_FILE.exists():
        return {}
    try:
        with open(USERS_PROFILES_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


DEFAULT_USER_PROFILE = {
    "name": "User",
    "display_name": "User",
    "role": "admin",
    "can_install": True,
    "can_restart": True,
    "blocked_skills": [],
}


def get_user_profile(user_id: int) -> dict:
    profiles = load_user_profiles()
    profile = profiles.get(str(user_id), DEFAULT_USER_PROFILE.copy())
    for key, default_value in DEFAULT_USER_PROFILE.items():
        if key not in profile:
            profile[key] = default_value
    return profile


def is_admin(user_id: int) -> bool:
    return get_user_profile(user_id).get("role") == "admin"
