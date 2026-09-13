"""Per-user preferences: small, user-writable settings kept out of users.json.

``data/users.json`` is the registry (who exists, what role they have) and it
is read on every authorisation check by both processes. Preferences a user
may change at will do not belong in it: a read-modify-write race on that file
loses a role, and a role is the trust boundary. They live here instead, one
file per user inside the directory that user already owns.

    data/users/<telegram_id>/prefs.json

Today there is exactly one key, ``engine`` (see core.engines). The store is
deliberately a plain dict so the next one needs no migration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.users import resolve_user_dir
from utils.safe_json import load_json, save_json

PREFS_FILENAME = "prefs.json"


def _prefs_path(user_id: int) -> Path:
    """Path to this user's prefs file.

    The id is coerced to int first: it is interpolated into a filesystem
    path, and every caller reaches here from either a Telegram update or an
    initData HMAC, both of which carry it as a number. A value that is not
    one is a bug or an attempt, never a user with an unusual name.
    """
    return resolve_user_dir(int(user_id)) / PREFS_FILENAME


def load_prefs(user_id: int) -> dict:
    """Every stored preference for this user. Missing or corrupt file -> {}."""
    data = load_json(_prefs_path(user_id), {})
    return data if isinstance(data, dict) else {}


def get_pref(user_id: int, key: str, default: Any = None) -> Any:
    return load_prefs(user_id).get(key, default)


def set_pref(user_id: int, key: str, value: Any) -> bool:
    """Store one preference. Returns False when the write failed.

    ponytail: read-modify-write, no lock. Two processes can write this file
    (the bot on a command, the Mini App on a tap) and a simultaneous write of
    two DIFFERENT keys could drop one. Both writers are driven by a human
    pressing something, and the file holds one key today. If a second
    frequently-written key ever lands here, take a lock file around the pair.
    """
    prefs = load_prefs(user_id)
    if prefs.get(key) == value:
        return True  # nothing to write; do not churn the file
    prefs[key] = value
    return _save(user_id, prefs)


def clear_pref(user_id: int, key: str) -> bool:
    prefs = load_prefs(user_id)
    if key not in prefs:
        return True
    del prefs[key]
    return _save(user_id, prefs)


def _save(user_id: int, prefs: dict) -> bool:
    path = _prefs_path(user_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, prefs)
        return True
    except OSError:
        return False
