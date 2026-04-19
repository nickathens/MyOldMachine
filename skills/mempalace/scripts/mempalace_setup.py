#!/usr/bin/env python3
"""
MemPalace setup: create isolated venv, install mempalace, and mine existing history.

Run with the bot's Python (NOT the mempalace venv):
    <BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py

Creates:
    <BOT_DIR>/data/mempalace/venv/    -- isolated Python venv
    <BOT_DIR>/data/mempalace/palace/  -- ChromaDB vector store
    <BOT_DIR>/data/mempalace/convos/  -- per-user session exports
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
MEMPALACE_DIR = BOT_DIR / "data" / "mempalace"
VENV_DIR = MEMPALACE_DIR / "venv"
PALACE_DIR = MEMPALACE_DIR / "palace"
USERS_DIR = BOT_DIR / "data" / "users"

VENV_PYTHON = VENV_DIR / "bin" / "python"
VENV_PIP = VENV_DIR / "bin" / "pip"


def check_existing():
    """Check if mempalace is already installed and working."""
    if not VENV_PYTHON.exists():
        return False
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import mempalace; print(mempalace.__version__)"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            print(f"MemPalace {version} already installed at {VENV_DIR}", flush=True)
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def create_venv():
    """Create the isolated Python venv."""
    MEMPALACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"Creating isolated venv at {VENV_DIR}...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        print("ERROR: venv creation timed out.", flush=True)
        return False

    if result.returncode != 0:
        print(f"ERROR: Failed to create venv: {result.stderr}", flush=True)
        if "ensurepip" in result.stderr.lower():
            print("TIP: Install python3-venv (e.g., sudo apt install python3-venv)", flush=True)
        return False

    print("Upgrading pip...", flush=True)
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True, text=True, timeout=60
    )

    print("Installing mempalace...", flush=True)
    try:
        result = subprocess.run(
            [str(VENV_PIP), "install", "mempalace"],
            capture_output=True, text=True, timeout=600
        )
    except subprocess.TimeoutExpired:
        print("ERROR: pip install timed out (>10 min). Check network.", flush=True)
        return False

    if result.returncode != 0:
        print(f"ERROR: pip install failed:\n{result.stderr[-500:]}", flush=True)
        return False

    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import mempalace; print(mempalace.__version__)"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print(f"MemPalace {result.stdout.strip()} installed successfully.", flush=True)
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass

    print("ERROR: mempalace installed but import failed.", flush=True)
    return False


def get_user_ids():
    """Discover user IDs with message logs."""
    if not USERS_DIR.exists():
        return []
    user_ids = []
    for d in USERS_DIR.iterdir():
        if d.is_dir() and d.name.isdigit():
            if (d / "message_log.db").exists():
                user_ids.append(d.name)
    return sorted(user_ids)


def count_messages(user_id):
    """Count messages in a user's log."""
    db_path = USERS_DIR / user_id / "message_log.db"
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        try:
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            conn.close()
        return count
    except (sqlite3.Error, OSError):
        return 0


def initial_mine():
    """Export all messages and mine into palace."""
    user_ids = get_user_ids()
    if not user_ids:
        print("\nNo users with message logs found. Palace created empty.", flush=True)
        PALACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        return True

    total_msgs = 0
    print(f"\nFound {len(user_ids)} user(s) with conversation history:", flush=True)
    for uid in user_ids:
        count = count_messages(uid)
        total_msgs += count
        print(f"  User {uid}: {count} messages", flush=True)

    print(f"\nMining {total_msgs} messages into palace...", flush=True)
    print("This may take several minutes on slower hardware.\n", flush=True)

    sync_script = str(BOT_DIR / "skills" / "mempalace" / "scripts" / "mempalace_sync.py")
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), sync_script, "--force-today"],
            text=True, timeout=900
        )
    except subprocess.TimeoutExpired:
        print("\nWARNING: Mining timed out after 15 minutes.", flush=True)
        print("Run the sync script manually to complete:", flush=True)
        print(f"  {VENV_PYTHON} {sync_script} --force-today", flush=True)
        return False

    return result.returncode == 0


def main():
    print("=== MemPalace Setup ===\n", flush=True)

    if check_existing():
        print("\nSkipping venv creation (already installed).", flush=True)
    else:
        if not create_venv():
            print("\nSetup failed: could not install mempalace.", flush=True)
            sys.exit(1)

    if not initial_mine():
        print("\nSetup completed with warnings. Check output above.", flush=True)
    else:
        print("\nSetup complete. MemPalace is ready.", flush=True)

    print(f"\nPalace:      {PALACE_DIR}", flush=True)
    print(f"Venv Python: {VENV_PYTHON}", flush=True)
    mp_search = BOT_DIR / "skills" / "mempalace" / "scripts" / "mempalace_search.py"
    print(f'\nTest search: {VENV_PYTHON} {mp_search} "test query" --wing user_<ID>', flush=True)
    print("\nRemember to set up a daily sync job for automatic updates.", flush=True)


if __name__ == "__main__":
    main()
