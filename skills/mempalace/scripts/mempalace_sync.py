#!/usr/bin/env python3
"""
MemPalace per-user sync: export new messages from one user's message_log.db and
mine them into that user's permanent palace.

Each user owns their palace under <user_dir>/mempalace/. This script operates
on exactly one user at a time, which makes scheduling per slot straightforward
and keeps multi-user isolation enforced at the filesystem layer.

Run with the shared mempalace venv Python:
    <BOT_DIR>/data/mempalace/venv/bin/python <this> --user-dir <user_dir>

Flags:
    --user-dir PATH   Required. The user's data directory.
    --force-today     Re-mine today's session (otherwise only complete days are mined).
    --dry-run         Show what would happen without writing.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def _palace_path(user_dir: Path) -> Path:
    return user_dir / "mempalace" / "palace"


def _convos_dir(user_dir: Path) -> Path:
    return user_dir / "mempalace" / "convos"


def _sync_state_file(user_dir: Path) -> Path:
    return user_dir / "mempalace" / "sync_state.json"


def _wing_for(user_dir: Path) -> str:
    return f"user_{user_dir.name}"


def _message_log(user_dir: Path) -> Path:
    return user_dir / "message_log.db"


def load_sync_state(user_dir: Path) -> dict:
    path = _sync_state_file(user_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_sync_state(user_dir: Path, state: dict) -> None:
    mempalace_dir = user_dir / "mempalace"
    mempalace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _sync_state_file(user_dir)
    tmp = str(path) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, str(path))


def export_messages(user_dir: Path, cutoff_date: str | None = None) -> dict[str, list[dict]]:
    """Export messages grouped by ISO date (YYYY-MM-DD)."""
    db_path = _message_log(user_dir)
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
                (cutoff_date + "T23:59:59",),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"  ERROR: Could not read {db_path}: {e}", flush=True)
        return {}

    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        date = row["timestamp"][:10]
        by_date[date].append({"role": row["role"], "content": row["content"]})
    return dict(by_date)


def write_session_files(by_date: dict[str, list[dict]], output_dir: Path) -> list[str]:
    """Write per-day JSON exports. Skips days whose content is unchanged."""
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    written: list[str] = []
    for date, messages in sorted(by_date.items()):
        path = output_dir / f"session_{date}.json"
        new_content = json.dumps(messages, indent=2, ensure_ascii=False)
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == new_content:
                    continue
            except OSError:
                pass
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        written.append(str(path))
    return written


def mine_sessions(user_dir: Path, dry_run: bool = False, force_today: bool = False) -> dict:
    """Mine the user's exported sessions into their palace."""
    try:
        from mempalace.convo_miner import mine_convos  # type: ignore[import-not-found]
        from mempalace.palace import get_collection  # type: ignore[import-not-found]
    except ImportError:
        print("ERROR: mempalace not importable. Activate the mempalace venv.", flush=True)
        sys.exit(1)

    palace = _palace_path(user_dir)
    convos = _convos_dir(user_dir)
    wing = _wing_for(user_dir)

    if force_today:
        today_file = str(convos / f"session_{datetime.now().strftime('%Y-%m-%d')}.json")
        try:
            collection = get_collection(str(palace), create=False)
            collection.delete(where={"source_file": today_file})
            print(f"  Purged today's drawers for re-mining: {today_file}", flush=True)
        except Exception as e:
            print(f"  Could not purge today's drawers: {e}", flush=True)

    try:
        mine_convos(
            convo_dir=str(convos),
            palace_path=str(palace),
            wing=wing,
            agent="daily_sync",
            dry_run=dry_run,
        )
    except Exception as e:
        print(f"  ERROR: Mining failed for wing {wing}: {e}", flush=True)
        return {"total_drawers": -1, "error": str(e)}

    try:
        collection = get_collection(str(palace), create=False)
        total = collection.count()
    except Exception:
        total = -1
    return {"total_drawers": total}


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-user MemPalace sync")
    parser.add_argument("--user-dir", required=True, help="The user's data directory")
    parser.add_argument("--force-today", action="store_true", help="Re-mine today's session")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be mined")
    args = parser.parse_args()

    user_dir = Path(args.user_dir).expanduser().resolve()
    if not user_dir.exists():
        print(f"ERROR: --user-dir does not exist: {user_dir}", flush=True)
        sys.exit(2)

    if not _message_log(user_dir).exists():
        print(f"No message_log.db in {user_dir}. Nothing to sync.", flush=True)
        return

    start = datetime.now()
    wing = _wing_for(user_dir)
    print(f"[{start.isoformat()}] MemPalace sync for {user_dir} (wing: {wing})", flush=True)

    cutoff = datetime.now().strftime("%Y-%m-%d") if args.force_today else None

    print("  Exporting messages...", flush=True)
    by_date = export_messages(user_dir, cutoff_date=cutoff)
    if by_date:
        write_session_files(by_date, _convos_dir(user_dir))
        msg_count = sum(len(v) for v in by_date.values())
        print(f"  {len(by_date)} day(s), {msg_count} messages", flush=True)
    else:
        print("  No messages to export", flush=True)

    print(f"  Mining into palace at {_palace_path(user_dir)}...", flush=True)
    stats = mine_sessions(user_dir, dry_run=args.dry_run, force_today=args.force_today)

    if not args.dry_run:
        state = load_sync_state(user_dir)
        state["last_sync"] = start.isoformat()
        state["last_total_drawers"] = stats.get("total_drawers", -1)
        save_sync_state(user_dir, state)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n[{datetime.now().isoformat()}] Sync complete in {elapsed:.1f}s", flush=True)
    print(f"  Total drawers in palace: {stats.get('total_drawers', 'unknown')}", flush=True)


if __name__ == "__main__":
    main()
