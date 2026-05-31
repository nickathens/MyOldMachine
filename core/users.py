"""
core/users.py: User management for MyOldMachine.

The bot runs as one OS user. Multiple Telegram users may share the same
install — each gets their own data directory at data/users/<telegram_id>/
and their own profile entry in data/users.json. Isolation is application-
level: the bot routes messages by Telegram ID. The kernel does NOT enforce
isolation between Telegram users; the bot's own scoping is the trust
boundary.

Public API:
    add_user(telegram_id, name, role='user') -> (ok, message)
    remove_user(telegram_id) -> (ok, message)
    list_users() -> dict[str, profile]
    get_user(telegram_id) -> Optional[dict]
    is_registered(telegram_id) -> bool
    is_admin(telegram_id) -> bool
    get_admin_telegram_ids() -> list[int]
    resolve_user_dir(telegram_id) -> Path
    queue_mode() -> 'universal' | 'per_user'
    queue_enabled() -> bool   # legacy alias for queue_mode() == 'universal'
    concurrent_requests() -> int
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# A peer ID becomes both a users.json key and a data/users/<id>/ directory
# name, so it must be a safe slug (no path separators or traversal).
_PEER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

_BOT_DIR = Path(__file__).parent.parent
USERS_JSON = _BOT_DIR / "data" / "users.json"
USERS_DATA_DIR = _BOT_DIR / "data" / "users"


_DEFAULT_PROFILE: dict = {
    "name": "User",
    "display_name": "User",
    "role": "user",
    "can_install": False,
    "can_restart": False,
    "blocked_skills": [],
}


def _load() -> dict:
    if not USERS_JSON.exists():
        return {}
    try:
        with open(USERS_JSON, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def _save(data: dict) -> bool:
    """Atomic write of users.json. fsyncs file and parent dir on POSIX."""
    USERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_JSON.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(USERS_JSON)
        try:
            dir_fd = os.open(
                str(USERS_JSON.parent),
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ─── Profile lookup ───────────────────────────────────────────────────


def list_users() -> dict:
    """Return the full users.json contents. Keys are Telegram IDs as strings."""
    return _load()


def get_user(telegram_id: int) -> Optional[dict]:
    """Return the profile dict for a user, or None if not registered."""
    return _load().get(str(telegram_id))


def is_registered(telegram_id: int) -> bool:
    return get_user(telegram_id) is not None


def is_admin(telegram_id: int) -> bool:
    """True if the Telegram ID is registered with role='admin'."""
    profile = get_user(telegram_id)
    if not profile:
        return False
    return profile.get("role") == "admin"


def get_admin_telegram_ids() -> list[int]:
    """Return the list of Telegram IDs flagged as admin."""
    out: list[int] = []
    for tid_str, profile in _load().items():
        if isinstance(profile, dict) and profile.get("role") == "admin":
            try:
                out.append(int(tid_str))
            except (ValueError, TypeError):
                continue
    return out


# ─── User management ──────────────────────────────────────────────────


def add_user(
    telegram_id: int, name: str, *, role: str = "user"
) -> tuple[bool, str]:
    """Register a Telegram user.

    Returns (ok, message). Refuses to overwrite an existing entry — caller
    should /removeuser first if they want to change role or name.
    """
    if role not in ("user", "admin"):
        return False, f"Invalid role: {role!r}. Must be 'user' or 'admin'."
    name = (name or "").strip()
    if not name:
        return False, "Name is required."

    data = _load()
    tid = str(telegram_id)
    if tid in data:
        existing_name = data[tid].get("name", "?") if isinstance(data[tid], dict) else "?"
        return False, (
            f"User {tid} ({existing_name}) is already registered. "
            f"Use /removeuser first to change role or name."
        )

    profile = dict(_DEFAULT_PROFILE)
    profile.update({
        "name": name,
        "display_name": name,
        "role": role,
        "can_install": role == "admin",
        "can_restart": role == "admin",
        "added": datetime.now().isoformat(timespec="seconds"),
    })
    data[tid] = profile
    if not _save(data):
        return False, "Failed to write users.json."
    return True, f"Added {name} ({tid}) as {role}."


def remove_user(telegram_id: int) -> tuple[bool, str]:
    """Unregister a Telegram user. Refuses to remove the last admin."""
    data = _load()
    tid = str(telegram_id)
    if tid not in data:
        return False, f"User {tid} is not registered."

    profile = data[tid]
    if isinstance(profile, dict) and profile.get("role") == "admin":
        admin_count = sum(
            1 for v in data.values()
            if isinstance(v, dict) and v.get("role") == "admin"
        )
        if admin_count <= 1:
            return False, (
                "Cannot remove the last admin. "
                "Add another admin first, then remove this one."
            )

    name = profile.get("name", "?") if isinstance(profile, dict) else "?"
    del data[tid]
    if not _save(data):
        return False, "Failed to write users.json."
    return True, f"Removed {name} ({tid})."


def register_peer(
    peer_id: str, name: str, about: str = "", *, role: str = "user"
) -> tuple[bool, str]:
    """Register (or update) a peer agent as a conversational identity.

    A peer is another agent (for example, a bot running on a different
    machine) that talks to this bot over a side channel instead of through
    Telegram.
    It is stored in users.json like a normal user so the standard
    prompt/session/history pipeline applies unchanged, but its ID MUST be
    non-numeric. Numeric IDs are the Telegram namespace: get_allowed_users()
    and get_admin_telegram_ids() int()-parse the keys, so a non-numeric peer
    ID is automatically excluded from the Telegram allow-list and the admin
    list. That fencing is the point: the peer can hold a conversation but
    can never be mistaken for a Telegram user.

    Unlike add_user, this is idempotent: re-registering an existing peer
    updates its name/about/role in place, so the `about` text can be
    refreshed without removing the peer and discarding its history.

    Returns (ok, message).
    """
    if role not in ("user", "admin"):
        return False, f"Invalid role: {role!r}. Must be 'user' or 'admin'."
    name = (name or "").strip()
    if not name:
        return False, "Name is required."
    pid = str(peer_id).strip()
    if not pid:
        return False, "Peer ID is required."
    if not _PEER_ID_RE.fullmatch(pid):
        return False, (
            f"Peer ID {pid!r} is invalid. Use only letters, digits, hyphen and "
            f"underscore (it becomes a users.json key and a directory name)."
        )
    # The allow-list and admin list int()-parse user keys, so any int-parseable
    # ID would land in the Telegram namespace. Reject exactly that set.
    try:
        int(pid)
    except (ValueError, TypeError):
        pass
    else:
        return False, (
            f"Peer ID {pid!r} is numeric. Peer IDs must be non-numeric so they "
            f"stay out of the Telegram allow-list and admin list."
        )

    data = _load()
    existing = data.get(pid)
    profile = dict(existing) if isinstance(existing, dict) else dict(_DEFAULT_PROFILE)
    now = datetime.now().isoformat(timespec="seconds")
    profile.update({
        "name": name,
        "display_name": name,
        "role": role,
        "can_install": role == "admin",
        "can_restart": role == "admin",
        "is_peer": True,
        "about": about or "",
        "updated": now,
    })
    profile.setdefault("added", now)
    data[pid] = profile
    if not _save(data):
        return False, "Failed to write users.json."
    verb = "Updated" if isinstance(existing, dict) else "Registered"
    return True, f"{verb} peer {name} ({pid}) as {role}."


# ─── Filesystem layout ────────────────────────────────────────────────


def resolve_user_dir(telegram_id: int) -> Path:
    """Return the data directory for a Telegram ID. Caller ensures it exists."""
    return USERS_DATA_DIR / str(telegram_id)


# ─── Queue scope (env-driven) ─────────────────────────────────────────


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def queue_mode() -> str:
    """Return the active queue scope.

    'universal' = one queue across all Telegram users (one LLM call at a
    time on the whole bot). 'per_user' = each Telegram user has their own
    serialization queue, so different users can be answered in parallel.

    Reads QUEUE_MODE env. Defaults to 'per_user'.
    """
    raw = _env("QUEUE_MODE").lower()
    if raw == "universal":
        return "universal"
    if raw in ("per_user", "per-user"):
        return "per_user"
    return "per_user"


def queue_enabled() -> bool:
    """Legacy alias: True if queue_mode() == 'universal'."""
    return queue_mode() == "universal"


def concurrent_requests() -> int:
    """Number of concurrent LLM requests allowed.

    0 = unlimited. Positive integer = hard cap. Reads CONCURRENT_REQUESTS
    env. Defaults to 1 in universal mode, 0 (unlimited per-user) otherwise.
    """
    raw = _env("CONCURRENT_REQUESTS")
    if raw:
        try:
            v = int(raw)
            if v >= 0:
                return v
        except ValueError:
            pass
    return 1 if queue_mode() == "universal" else 0
