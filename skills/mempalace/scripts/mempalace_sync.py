#!/usr/bin/env python3
"""
MemPalace daily sync: export new messages and mine into the palace.

Discovers users automatically from data/users/. Each user gets their own wing
(user_<telegram_id>) for data isolation.

Must run with the mempalace venv Python:
    <BOT_DIR>/data/mempalace/venv/bin/python <this_script>

Flags:
    --force-today   Re-mine today's session (normally only complete days are mined)
    --dry-run       Show what would be mined without filing
    --user USER_ID  Sync specific user only (default: all users with message logs)
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
USERS_DIR = BOT_DIR / "data" / "users"
MEMPALACE_DIR = BOT_DIR / "data" / "mempalace"
PALACE_PATH = str(MEMPALACE_DIR / "palace")
SYNC_STATE_FILE = MEMPALACE_DIR / "sync_state.json"


def get_all_user_ids():
    """Discover user IDs from data/users/ that have message logs."""
    if not USERS_DIR.exists():
        return []
    user_ids = []
    for d in USERS_DIR.iterdir():
        if d.is_dir() and d.name.isdigit():
            if (d / "message_log.db").exists():
                user_ids.append(d.name)
    return sorted(user_ids)


def user_wing(user_id):
    """Derive wing name from user ID."""
    return f"user_{user_id}"


def load_sync_state():
    if SYNC_STATE_FILE.exists():
        try:
            return json.loads(SYNC_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_sync_state(state):
    MEMPALACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = str(SYNC_STATE_FILE) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, str(SYNC_STATE_FILE))


def export_messages(user_id, cutoff_date=None):
    """Export messages from message_log.db grouped by date.

    Args:
        user_id: Telegram user ID string.
        cutoff_date: Only export messages on or before this date (YYYY-MM-DD).
                     If None, exports up to yesterday (complete days only).

    Returns:
        dict mapping date strings to list of {role, content} dicts.
    """
    db_path = USERS_DIR / user_id / "message_log.db"
    if not db_path.exists():
        return {}

    if cutoff_date is None:
        cutoff_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT timestamp, role, content FROM messages "
                "WHERE timestamp <= ? ORDER BY timestamp",
                (cutoff_date + "T23:59:59",)
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"  ERROR: Could not read message_log.db for user {user_id}: {e}", flush=True)
        return {}

    by_date = defaultdict(list)
    for row in rows:
        date = row["timestamp"][:10]
        by_date[date].append({"role": row["role"], "content": row["content"]})

    return dict(by_date)


def write_session_files(by_date, output_dir):
    """Write per-day JSON session files. Skips files whose content hasn't changed.

    Returns list of file paths that were actually written (new or changed).
    """
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    written = []
    for date, messages in sorted(by_date.items()):
        path = output_dir / f"session_{date}.json"
        new_content = json.dumps(messages, indent=2, ensure_ascii=False)
        if path.exists():
            try:
                if path.read_text() == new_content:
                    continue
            except OSError:
                pass
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new_content)
        written.append(str(path))
    return written


def mine_sessions(convos_dir, wing, dry_run=False, force_today=False):
    """Mine conversation sessions into the palace.

    Returns stats dict with drawer count.
    """
    try:
        from mempalace.convo_miner import mine_convos
        from mempalace.palace import get_collection
    except ImportError:
        print("ERROR: mempalace not importable. Is the mempalace venv active?", flush=True)
        sys.exit(1)

    if force_today:
        today_file = str(convos_dir / f"session_{datetime.now().strftime('%Y-%m-%d')}.json")
        try:
            collection = get_collection(PALACE_PATH, create=False)
            collection.delete(where={"source_file": today_file})
            print(f"  Purged today's drawers for re-mining: {today_file}", flush=True)
        except Exception as e:
            print(f"  Could not purge today's drawers: {e}", flush=True)

    try:
        mine_convos(
            convo_dir=str(convos_dir),
            palace_path=PALACE_PATH,
            wing=wing,
            agent="daily_sync",
            dry_run=dry_run,
        )
    except Exception as e:
        print(f"  ERROR: Mining failed for wing {wing}: {e}", flush=True)
        return {"total_drawers": -1, "error": str(e)}

    try:
        collection = get_collection(PALACE_PATH, create=False)
        total = collection.count()
    except Exception:
        total = -1

    return {"total_drawers": total}


def main():
    parser = argparse.ArgumentParser(description="MemPalace daily sync")
    parser.add_argument("--force-today", action="store_true",
                        help="Re-mine today's session (for mid-day updates)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be mined without filing")
    parser.add_argument("--user", default=None,
                        help="Sync specific user ID (default: all users with message logs)")
    args = parser.parse_args()

    start = datetime.now()
    print(f"[{start.isoformat()}] MemPalace sync starting", flush=True)

    if args.user:
        if not args.user.isdigit():
            print(f"ERROR: User ID must be numeric, got: {args.user}", flush=True)
            sys.exit(1)
        user_ids = [args.user]
    else:
        user_ids = get_all_user_ids()
        if not user_ids:
            print("No users with message logs found.", flush=True)
            sys.exit(0)

    print(f"  Users to sync: {', '.join(user_ids)}", flush=True)

    cutoff = None
    if args.force_today:
        cutoff = datetime.now().strftime("%Y-%m-%d")

    last_stats = {}
    for uid in user_ids:
        wing = user_wing(uid)
        convos_dir = MEMPALACE_DIR / "convos" / uid
        print(f"\n  User {uid} (wing: {wing}):", flush=True)

        print("    Exporting messages...", flush=True)
        by_date = export_messages(uid, cutoff_date=cutoff)
        if by_date:
            write_session_files(by_date, convos_dir)
            msg_count = sum(len(v) for v in by_date.values())
            print(f"    {len(by_date)} day(s), {msg_count} messages", flush=True)
        else:
            print("    No messages to export", flush=True)

        print(f"    Mining into palace (wing: {wing})...", flush=True)
        last_stats = mine_sessions(convos_dir, wing, dry_run=args.dry_run, force_today=args.force_today)

    state = load_sync_state()
    state["last_sync"] = start.isoformat()
    state["last_total_drawers"] = last_stats.get("total_drawers", -1)
    if not args.dry_run:
        save_sync_state(state)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n[{datetime.now().isoformat()}] Sync complete in {elapsed:.1f}s", flush=True)
    print(f"  Total drawers in palace: {last_stats.get('total_drawers', 'unknown')}", flush=True)


if __name__ == "__main__":
    main()
