"""
Configuration management for MyOldMachine.

All paths and settings are derived from environment variables or sensible defaults.
No hardcoded user-specific paths.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

# .env hot-reload. bot.py loads .env into os.environ exactly once at boot
# (load_dotenv), so a later edit of the file was invisible to the running
# process until restart: the Mini App writes LLM_MODEL from a separate
# uvicorn process, and a direct edit (ssh, editor, wizard re-run) never
# landed at all. _reload_env_if_changed() stats the file on every config
# read and applies only the keys whose *file* value changed since the last
# parse. A key that boot-time load_dotenv(override=False) left shadowed by
# a pre-set process environment value stays shadowed until its file entry
# is actually edited, so startup precedence is preserved.
ENV_FILE = Path(__file__).parent.parent / ".env"
_env_lock = threading.Lock()
_env_file_mtime: Optional[float] = None
_env_file_values: dict[str, str] = {}


def _reload_env_if_changed() -> None:
    global _env_file_mtime, _env_file_values
    try:
        mtime = ENV_FILE.stat().st_mtime
    except OSError:
        return  # no .env (fresh install, tests): nothing to watch
    if mtime == _env_file_mtime:
        return
    with _env_lock:
        if mtime == _env_file_mtime:  # another thread already reloaded
            return
        try:
            from dotenv import dotenv_values
        except ImportError:
            return  # deps not installed yet (install-time imports)
        new_values = {
            k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None
        }
        first_sight = _env_file_mtime is None
        old_values = _env_file_values
        _env_file_values = new_values
        _env_file_mtime = mtime
        if first_sight:
            # Baseline only: the boot-time load_dotenv already applied this
            # state without overriding pre-set process env; re-applying here
            # would flip that precedence.
            return
        for key, old in old_values.items():
            # A key removed from the file falls back to the default, but
            # only when the live env value is the one the file put there.
            if key not in new_values and os.environ.get(key) == old:
                os.environ.pop(key, None)
        for key, value in new_values.items():
            if value != old_values.get(key):
                os.environ[key] = value


def _env(key: str, default: str = "") -> str:
    _reload_env_if_changed()
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    _reload_env_if_changed()
    val = os.environ.get(key, "").strip()
    if val.lstrip('-').isdigit() and val.count('-') <= 1 and val:
        return int(val)
    return default


def _env_list(key: str) -> list[int]:
    """Parse comma-separated integers from env var."""
    _reload_env_if_changed()
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


# Capture the boot-time baseline immediately: bot.py runs load_dotenv before
# importing this module, so the file state seen here is the state already in
# os.environ. Everything after this point is a live edit.
_reload_env_if_changed()


# Base directories (relative to project root)
BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
SKILLS_DIR = BOT_DIR / "skills"
USERS_DIR = DATA_DIR / "users"
MEMORY_DIR = DATA_DIR / "memory"
SCHEDULER_DIR = DATA_DIR / "scheduler"
IDENTITY_DIR = DATA_DIR / "identities"
LOG_DIR = DATA_DIR / "logs"

# Ensure directories exist, private to the bot's OS account. Conversations,
# memory, identities, and logs all live under data/; 0700 keeps other local
# accounts out. Re-applied on every import so a loosened tree self-heals on
# the next boot (same pattern as ensure_scheduler_dir in core/scheduler.py).
# chmod failures are non-fatal: a warning beats refusing to boot on a
# filesystem that does not support POSIX permissions.
for d in [DATA_DIR, USERS_DIR, MEMORY_DIR, SCHEDULER_DIR, IDENTITY_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError as _chmod_exc:
        logging.getLogger(__name__).warning(
            "Could not chmod %s to 0700: %s", d, _chmod_exc
        )


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
    """Return the list of allowed Telegram IDs.

    data/users.json is the source of truth: every registered Telegram ID
    is allowed. ALLOWED_USERS in .env acts as a bootstrap fallback when
    users.json is empty (typical right after install, before the wizard
    has written the admin profile) so the admin is not locked out.
    """
    base = _env_list("ALLOWED_USERS")
    try:
        from core.users import list_users
    except ImportError:
        return base
    registered: list[int] = []
    for tid_str in list_users().keys():
        try:
            registered.append(int(tid_str))
        except (ValueError, TypeError):
            continue
    if registered:
        seen: set[int] = set()
        result: list[int] = []
        for uid in registered:
            if uid not in seen:
                seen.add(uid)
                result.append(uid)
        return result
    return base


def get_primary_admin_id() -> Optional[int]:
    """Return the Telegram ID that admin-facing notifications go to.

    Never use get_allowed_users()[0] for this: users.json is written with
    sort_keys=True, so the first *user* is whoever has the smallest Telegram
    ID, not the admin. Registering any user with a smaller ID would silently
    re-route reports and alerts to them. First admin wins; the allowed-users
    fallback covers the bootstrap window when users.json does not exist yet
    (ALLOWED_USERS env) and legacy installs with no admin role recorded.
    """
    try:
        from core.users import get_admin_telegram_ids
        admins = get_admin_telegram_ids()
    except ImportError:
        admins = []
    if admins:
        return admins[0]
    allowed = get_allowed_users()
    return allowed[0] if allowed else None


def get_bot_name() -> str:
    return _env("BOT_NAME", "MyOldMachine")


def get_timezone() -> str:
    return _env("TIMEZONE", "UTC")


def get_webhook_port() -> int:
    return _env_int("WEBHOOK_PORT", 0)


def get_heartbeat_url() -> str:
    """Ping URL for the opt-in external heartbeat. Empty string means disabled."""
    return _env("HEARTBEAT_URL", "").strip()


def get_heartbeat_interval_minutes() -> int:
    """Heartbeat ping interval in minutes (default 2, minimum 1)."""
    return max(1, _env_int("HEARTBEAT_INTERVAL_MIN", 2))


# LLM settings
def get_llm_provider() -> str:
    return _env("LLM_PROVIDER", "claude")


def get_llm_model() -> str:
    return _env("LLM_MODEL", "claude-sonnet-5")


def get_llm_api_key() -> str:
    return _env("LLM_API_KEY", "")


_VALID_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def get_llm_effort() -> str:
    """Effort level passed to `claude --effort`. Validates against the CLI's
    accepted set; falls back to "max" if the env var is unset or unrecognized."""
    value = _env("LLM_EFFORT", "max").strip().lower()
    if value in _VALID_EFFORT_LEVELS:
        return value
    return "max"


DEFAULT_BACKGROUND_MODEL = "claude-haiku-4-5-20251001"


def get_background_model() -> str:
    """Model for non-interactive background Claude CLI work (compaction).

    Background `claude -p` spawns previously passed no --model flag and
    silently billed at the CLI's default (premium) model. Summarization does
    not need a frontier model, so these default to Haiku. Override with
    BACKGROUND_MODEL in .env. Interactive chat keeps LLM_MODEL.
    """
    return _env("BACKGROUND_MODEL", DEFAULT_BACKGROUND_MODEL)


def get_ollama_base_url() -> str:
    return _env("OLLAMA_BASE_URL", "http://localhost:11434")


# User profiles
USERS_PROFILES_FILE = DATA_DIR / "users.json"


def load_user_profiles() -> dict:
    if not USERS_PROFILES_FILE.exists():
        return {}
    try:
        with open(USERS_PROFILES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, UnicodeDecodeError):
        return {}


DEFAULT_USER_PROFILE = {
    "name": "User",
    "display_name": "User",
    "role": "user",
    "can_install": False,
    "can_restart": False,
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
    """True if the Telegram ID has admin privileges.

    Reads the role from data/users.json (the single source of truth).
    """
    return get_user_profile(user_id).get("role") == "admin"
