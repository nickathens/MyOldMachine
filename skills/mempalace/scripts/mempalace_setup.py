#!/usr/bin/env python3
"""
MemPalace setup: install the shared mempalace venv (once), then provision a
per-user palace and mine the user's existing message log into it.

Run with the bot's Python:
    <BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py \\
        --user-dir <user_dir>

Provisioning is split:
  - The mempalace venv is shared at <BOT_DIR>/data/mempalace/venv/. One
    install per machine.
  - The actual palace, conversation exports, and sync state live entirely
    inside <user_dir>/mempalace/, one tree per Telegram user.

Pass --shared-only to install just the venv (e.g. during one-time bootstrap).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
SHARED_MEMPALACE_DIR = BOT_DIR / "data" / "mempalace"
SHARED_VENV_DIR = SHARED_MEMPALACE_DIR / "venv"
SHARED_VENV_PYTHON = SHARED_VENV_DIR / "bin" / "python"
SHARED_VENV_PIP = SHARED_VENV_DIR / "bin" / "pip"


def shared_venv_installed() -> bool:
    """True if mempalace is importable from the shared venv."""
    if not SHARED_VENV_PYTHON.exists():
        return False
    try:
        result = subprocess.run(
            [str(SHARED_VENV_PYTHON), "-c",
             "import mempalace; print(mempalace.__version__)"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            print(f"MemPalace {result.stdout.strip()} already installed at {SHARED_VENV_DIR}",
                  flush=True)
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def install_shared_venv() -> bool:
    """Create the shared venv and pip-install mempalace into it."""
    SHARED_MEMPALACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)

    print(f"Creating shared venv at {SHARED_VENV_DIR}...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(SHARED_VENV_DIR)],
            capture_output=True, text=True, timeout=60,
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
        [str(SHARED_VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True, text=True, timeout=60,
    )

    print("Installing mempalace...", flush=True)
    try:
        result = subprocess.run(
            [str(SHARED_VENV_PIP), "install", "mempalace"],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: pip install timed out (>10 min). Check network.", flush=True)
        return False
    if result.returncode != 0:
        print(f"ERROR: pip install failed:\n{result.stderr[-500:]}", flush=True)
        return False

    try:
        result = subprocess.run(
            [str(SHARED_VENV_PYTHON), "-c",
             "import mempalace; print(mempalace.__version__)"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            print(f"MemPalace {result.stdout.strip()} installed.", flush=True)
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass

    print("ERROR: mempalace installed but import failed.", flush=True)
    return False


def provision_user(user_dir: Path) -> bool:
    """Create the per-user palace dir tree. Returns True on success."""
    if not user_dir.exists():
        print(f"ERROR: --user-dir does not exist: {user_dir}", flush=True)
        return False

    mempalace_dir = user_dir / "mempalace"
    palace_dir = mempalace_dir / "palace"
    convos_dir = mempalace_dir / "convos"

    mempalace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    palace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    convos_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"Provisioned palace dirs under {mempalace_dir}", flush=True)
    return True


def initial_mine(user_dir: Path) -> bool:
    """Mine the user's existing message log into their freshly provisioned palace."""
    msg_log = user_dir / "message_log.db"
    if not msg_log.exists():
        print(f"  No message_log.db in {user_dir}. Palace stays empty.", flush=True)
        return True

    sync_script = BOT_DIR / "skills" / "mempalace" / "scripts" / "mempalace_sync.py"
    print("\nMining existing history into palace...", flush=True)
    try:
        result = subprocess.run(
            [str(SHARED_VENV_PYTHON), str(sync_script),
             "--user-dir", str(user_dir), "--force-today"],
            text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        print("\nWARNING: Mining timed out after 15 minutes.", flush=True)
        print("Run sync manually to complete:", flush=True)
        print(f"  {SHARED_VENV_PYTHON} {sync_script} --user-dir {user_dir} --force-today",
              flush=True)
        return False
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="MemPalace setup (shared venv + per-user palace)")
    parser.add_argument(
        "--user-dir",
        help="The user's data directory. Required unless --shared-only is set.",
    )
    parser.add_argument(
        "--shared-only", action="store_true",
        help="Install just the shared mempalace venv. Skip per-user provisioning.",
    )
    parser.add_argument(
        "--reinstall", action="store_true",
        help="Reinstall the shared venv even if already present.",
    )
    args = parser.parse_args()

    if not args.shared_only and not args.user_dir:
        parser.error("--user-dir is required (or pass --shared-only to install just the venv).")

    print("=== MemPalace Setup ===\n", flush=True)

    needs_install = args.reinstall or not shared_venv_installed()
    if needs_install:
        if args.reinstall and SHARED_VENV_DIR.exists():
            print(f"Removing existing venv at {SHARED_VENV_DIR}...", flush=True)
            shutil.rmtree(SHARED_VENV_DIR)
        if not install_shared_venv():
            print("\nSetup failed: shared venv could not be installed.", flush=True)
            sys.exit(1)
    else:
        print("\nShared venv ready. Skipping install.", flush=True)

    if args.shared_only:
        print(f"\nDone. Shared venv Python: {SHARED_VENV_PYTHON}", flush=True)
        return

    user_dir = Path(args.user_dir).expanduser().resolve()
    if not provision_user(user_dir):
        sys.exit(1)

    if not initial_mine(user_dir):
        print("\nSetup completed with warnings. Check output above.", flush=True)
    else:
        print("\nSetup complete. MemPalace is ready for this user.", flush=True)

    palace = user_dir / "mempalace" / "palace"
    print(f"\nPalace:      {palace}", flush=True)
    print(f"Venv Python: {SHARED_VENV_PYTHON}", flush=True)
    search = BOT_DIR / "skills" / "mempalace" / "scripts" / "mempalace_search.py"
    print(f'\nTest search: {SHARED_VENV_PYTHON} {search} "test query" --user-dir {user_dir}',
          flush=True)


if __name__ == "__main__":
    main()
