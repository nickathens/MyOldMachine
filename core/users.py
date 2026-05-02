"""
core/users.py: Slot-based user management for multi-user mode.

When multi-user mode is enabled (data/orchestrator/users.json exists), the
bot maps Telegram IDs to slots (1..N). Each slot has a dedicated system user
(mom_userN) and a private data directory (data/users/userN). The bot
dispatches CLI subprocesses as the slot's user via sudo.

When multi-user mode is disabled (the file doesn't exist), this module's
functions all return single-user defaults. The bot then uses the legacy
flat structure where each Telegram ID has its own data dir keyed by ID.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

ORCHESTRATOR_USER = "mom_orchestrator"
SLOT_USER_PREFIX = "mom_user"

_BOT_DIR = Path(__file__).parent.parent
ORCHESTRATOR_DIR = _BOT_DIR / "data" / "orchestrator"
USERS_JSON = ORCHESTRATOR_DIR / "users.json"


def is_multiuser_enabled() -> bool:
    """True if the orchestrator's slot table exists. False = legacy single-user."""
    return USERS_JSON.exists()


def _load() -> dict:
    if not USERS_JSON.exists():
        return {}
    try:
        with open(USERS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def _save(data: dict) -> bool:
    """Atomic write of the slot table. Returns True on success."""
    if not ORCHESTRATOR_DIR.exists():
        return False
    tmp = USERS_JSON.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o600)
        tmp.rename(USERS_JSON)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def lookup_slot(telegram_id: int) -> Optional[tuple[int, dict]]:
    """Find the slot bound to a Telegram ID.

    Returns (slot_number, info_dict) if bound, None otherwise.
    """
    data = _load()
    slots = data.get("slots", {}) or {}
    tid_str = str(telegram_id)
    for slot_str, info in slots.items():
        if info and str(info.get("telegram_id")) == tid_str:
            try:
                return int(slot_str), info
            except (ValueError, TypeError):
                continue
    return None


def get_admin_telegram_ids() -> list[int]:
    """Return the list of Telegram IDs that are flagged as admin in slots."""
    data = _load()
    slots = data.get("slots", {}) or {}
    admins: list[int] = []
    for info in slots.values():
        if info and info.get("is_admin"):
            try:
                admins.append(int(info["telegram_id"]))
            except (ValueError, TypeError, KeyError):
                continue
    return admins


def is_multiuser_admin(telegram_id: int) -> bool:
    return telegram_id in get_admin_telegram_ids()


def list_slots() -> dict:
    """Return the raw slot table for /users command."""
    data = _load()
    return data.get("slots", {}) or {}


def num_slots() -> int:
    data = _load()
    try:
        return int(data.get("num_slots", 0))
    except (ValueError, TypeError):
        return 0


def queue_enabled() -> bool:
    data = _load()
    return bool(data.get("queue_enabled", False))


def concurrent_requests() -> int:
    """Number of concurrent LLM requests allowed. 0 = unlimited."""
    data = _load()
    try:
        return int(data.get("concurrent_requests", 0))
    except (ValueError, TypeError):
        return 0


def find_free_slot() -> Optional[int]:
    """Return the lowest unbound slot number, or None if all slots are full."""
    data = _load()
    slots = data.get("slots", {}) or {}
    n = num_slots()
    for i in range(1, n + 1):
        if not slots.get(str(i)):
            return i
    return None


def add_user(telegram_id: int, name: str) -> tuple[bool, str, Optional[int]]:
    """Bind a free slot to a Telegram ID.

    Returns (ok, message, slot_or_None). Caller is expected to have already
    authenticated this as an admin command.
    """
    if lookup_slot(telegram_id):
        return False, f"Telegram ID {telegram_id} is already bound to a slot.", None
    slot = find_free_slot()
    if slot is None:
        return False, "No free slots available. All slots are taken.", None

    data = _load()
    if "slots" not in data:
        data["slots"] = {}
    data["slots"][str(slot)] = {
        "telegram_id": str(telegram_id),
        "name": name,
        "is_admin": False,
        "added": datetime.now().isoformat(timespec="seconds"),
    }
    if not _save(data):
        return False, "Failed to write users.json (check orchestrator dir perms).", None
    return True, f"Bound {name} ({telegram_id}) to slot {slot}.", slot


def remove_user(telegram_id: int) -> tuple[bool, str, Optional[int]]:
    """Unbind a Telegram ID from its slot. Refuses to remove the admin.

    Returns (ok, message, slot_or_None). Does NOT delete the slot's data
    directory: that's a separate concern (archived for recovery).
    """
    found = lookup_slot(telegram_id)
    if not found:
        return False, f"Telegram ID {telegram_id} is not bound to any slot.", None
    slot, info = found
    if info.get("is_admin"):
        return False, "Cannot remove the admin. Reinstall to change admin.", None

    data = _load()
    data["slots"][str(slot)] = None
    if not _save(data):
        return False, "Failed to write users.json.", None
    return True, f"Removed user from slot {slot} ({info.get('name', '?')}).", slot


def slot_user_name(slot: int) -> str:
    """OS user account name for a slot (1-indexed)."""
    if slot < 1:
        raise ValueError(f"Slot must be >= 1, got {slot}")
    return f"{SLOT_USER_PREFIX}{slot}"


def slot_data_dir(slot: int) -> Path:
    """Filesystem directory for a slot's private data."""
    if slot < 1:
        raise ValueError(f"Slot must be >= 1, got {slot}")
    return _BOT_DIR / "data" / "users" / f"user{slot}"


def resolve_user_dir(telegram_id: int) -> Path:
    """Return the data directory for a Telegram ID.

    Multi-user mode: returns data/users/userN/ where N is the bound slot.
    Single-user mode (or unbound): returns data/users/<telegram_id>/.

    The caller is responsible for ensuring the dir exists.
    """
    if is_multiuser_enabled():
        found = lookup_slot(telegram_id)
        if found:
            slot, _ = found
            return slot_data_dir(slot)
    return _BOT_DIR / "data" / "users" / str(telegram_id)
