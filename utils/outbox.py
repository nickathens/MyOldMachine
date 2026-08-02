#!/usr/bin/env python3
"""Durable outbox for replies whose delivery failed.

Why this exists: ``send_chunks_with_retry`` retries a transient send failure
four times over about nine seconds and then gives up, and every caller cleared
the pending-message marker in a ``finally`` block regardless. So an outage that
outlives the retry budget destroyed a finished answer AND the record that a
turn had been interrupted, leaving the user with silence. That is not
theoretical: it happened on the production Linux bot on 2026-08-02 at 12:05,
where a nine second local Bot API outage threw away a completed 386 character
reply and the user waited 46 minutes before asking whether the bot was alive.

Retries alone cannot fix it: any outage longer than the retry budget loses the
reply. Shape of the fix: the undelivered remainder of a reply is written to
``data/users/<telegram_id>/outbox/`` on the FIRST failed attempt, before the
backoff, so a process killed mid retry still leaves the answer on disk. The
entry carries a cursor of how many chunks already landed, so redelivery never
repeats a chunk the user already has. The happy path never touches the
filesystem.

Nothing in here deletes a reply. An entry that ages out or exhausts its
redelivery attempts is MOVED to ``outbox/expired/`` and logged at ERROR, never
unlinked: destroying a finished answer is the bug this module exists to
prevent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from utils.safe_json import load_json, save_json

logger = logging.getLogger(__name__)

OUTBOX_DIRNAME = "outbox"
EXPIRED_DIRNAME = "expired"

# A reply older than this is no longer pushed at the user: it would arrive with
# no conversational context left. A day is wide enough to span an overnight
# outage plus the machine's morning restart, so an evening failure still
# reaches the user the next morning.
MAX_AGE_SECONDS = 24 * 3600

# Give-up bound for an entry that keeps failing on redelivery, i.e. a permanent
# error the transient/permanent split in send_chunks_with_retry did not catch.
# At the sweep interval in bot.py this is about twenty minutes of trying.
MAX_ATTEMPTS = 10


def outbox_dir(user_dir: Path | str) -> Path:
    """Directory holding a single user's queued replies."""
    return Path(user_dir) / OUTBOX_DIRNAME


def record(
    user_dir: Path | str,
    user_id: int,
    chunks: list[str],
    sent: int = 0,
) -> Path | None:
    """Persist the undelivered remainder of a reply.

    Returns the entry path, or ``None`` if nothing needed queueing or the write
    failed. A ``None`` return on a real remainder means the reply is genuinely
    lost, which is the one case the caller must react to.

    Never raises: this is called from inside the send path's exception handler,
    where a second exception would destroy the very reply it is trying to save.
    """
    try:
        if sent >= len(chunks):
            return None
        directory = outbox_dir(user_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now()
        path = directory / f"{stamp.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}.json"
        save_json(path, {
            "user_id": int(user_id),
            "created": stamp.isoformat(),
            "chunks": list(chunks),
            "sent": int(sent),
            "attempts": 0,
            "header_sent": False,
        })
        logger.warning(
            "Queued undelivered reply for user %s as %s (%d of %d chunks still owed)",
            user_id, path.name, len(chunks) - sent, len(chunks),
        )
        return path
    except Exception as exc:
        logger.error("Could not queue undelivered reply for user %s: %s", user_id, exc)
        return None


def update(path: Path | str, **fields) -> None:
    """Merge ``fields`` into an existing entry. No-op if it has gone.

    Never raises, for the same reason ``record`` does not.
    """
    try:
        path = Path(path)
        data = load_json(path, default={})
        if not isinstance(data, dict) or not data:
            return
        data.update(fields)
        save_json(path, data)
    except Exception as exc:
        logger.error("Could not update outbox entry %s: %s", path, exc)


def clear(path: Path | str) -> None:
    """Drop an entry that was delivered in full."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not clear outbox entry %s: %s", path, exc)


def retire(path: Path | str, reason: str) -> Path | None:
    """Move an entry out of the delivery queue WITHOUT deleting it.

    Used for entries that aged out, exhausted their attempts, or cannot be
    parsed. Logged at ERROR because it means a finished answer never reached
    the user, which is exactly what the nightly report should surface.
    """
    path = Path(path)
    try:
        target_dir = path.parent / EXPIRED_DIRNAME
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            target = target_dir / f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
        path.rename(target)
        logger.error("Gave up redelivering %s (%s); kept at %s", path.name, reason, target)
        return target
    except OSError as exc:
        logger.error("Could not retire outbox entry %s (%s): %s", path, reason, exc)
        return None


def load(path: Path | str) -> dict | None:
    """Read and validate one entry. Returns ``None`` if it is unusable.

    Validation is strict because a malformed entry would otherwise be retried
    forever by the drain: every field the drain touches is checked here so the
    drain itself can index without guarding.
    """
    data = load_json(Path(path), default={})
    if not isinstance(data, dict):
        return None

    chunks = data.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return None
    if not all(isinstance(chunk, str) for chunk in chunks):
        return None

    try:
        data["user_id"] = int(data["user_id"])
    except (KeyError, TypeError, ValueError):
        return None

    try:
        datetime.fromisoformat(data["created"])
    except (KeyError, TypeError, ValueError):
        return None

    sent = data.get("sent")
    data["sent"] = sent if isinstance(sent, int) and 0 <= sent <= len(chunks) else 0
    attempts = data.get("attempts")
    data["attempts"] = attempts if isinstance(attempts, int) and attempts >= 0 else 0
    data["header_sent"] = bool(data.get("header_sent"))
    return data


def age_seconds(entry: dict) -> float:
    """Seconds since the reply was written. ``load`` guarantees it parses."""
    return (datetime.now() - datetime.fromisoformat(entry["created"])).total_seconds()


def pending(users_dir: Path | str) -> list[tuple[Path, dict]]:
    """Every queued reply across all users, oldest first.

    Unreadable entries are retired here rather than returned, so one corrupt
    file cannot stall redelivery for everybody behind it.
    """
    found: list[tuple[str, Path, dict]] = []
    users_dir = Path(users_dir)
    if not users_dir.is_dir():
        return []

    for user_dir in sorted(users_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        directory = outbox_dir(user_dir)
        if not directory.is_dir():
            continue
        # safe_json writes through "<name>.json.tmp", which *.json cannot
        # match, so a half-written entry is never picked up mid-rename.
        for path in sorted(directory.glob("*.json")):
            entry = load(path)
            if entry is None:
                retire(path, "unreadable entry")
                continue
            found.append((entry["created"], path, entry))

    found.sort(key=lambda item: (item[0], item[1].name))
    return [(path, entry) for _, path, entry in found]
