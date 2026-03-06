#!/usr/bin/env python3
"""
Auto-cleanup for MyOldMachine.

Removes old attachments, temp files, and log rotations.
Designed to run as a scheduled command job (daily at 3 AM).
"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
USERS_DIR = DATA_DIR / "users"
LOG_DIR = DATA_DIR / "logs"

logger = logging.getLogger(__name__)

# Files older than this many days get cleaned up
DEFAULT_MAX_AGE_DAYS = 10


def cleanup_attachments(max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
    """Remove old attachment files from all user directories."""
    if not USERS_DIR.exists():
        return 0

    now = time.time()
    max_age_seconds = max_age_days * 86400
    removed = 0

    for user_dir in USERS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        attach_dir = user_dir / "attachments"
        if not attach_dir.exists():
            continue

        for f in attach_dir.iterdir():
            if not f.is_file():
                continue
            try:
                age = now - f.stat().st_mtime
                if age > max_age_seconds:
                    f.unlink()
                    removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove {f}: {e}")

    return removed


def cleanup_logs(max_size_mb: int = 50) -> int:
    """Truncate log files that are too large."""
    if not LOG_DIR.exists():
        return 0

    truncated = 0
    max_size = max_size_mb * 1024 * 1024

    for log_file in LOG_DIR.glob("*.log"):
        try:
            if log_file.stat().st_size > max_size:
                # Keep last 1MB
                with open(log_file, 'rb') as f:
                    f.seek(-1024 * 1024, 2)
                    tail = f.read()
                with open(log_file, 'wb') as f:
                    f.write(tail)
                truncated += 1
        except Exception as e:
            logger.warning(f"Failed to truncate {log_file}: {e}")

    return truncated


def cleanup_temp() -> int:
    """Remove old temp files from /tmp that belong to us."""
    removed = 0
    now = time.time()
    max_age = 86400  # 1 day for temp files

    for pattern in ["myoldmachine_*", "mom_*"]:
        for f in Path("/tmp").glob(pattern):
            try:
                if f.is_file() and (now - f.stat().st_mtime) > max_age:
                    f.unlink()
                    removed += 1
            except Exception:
                pass

    return removed


def cleanup_archived_conversations(max_age_days: int = 30) -> int:
    """Remove old archived conversation files."""
    if not USERS_DIR.exists():
        return 0

    now = time.time()
    max_age_seconds = max_age_days * 86400
    removed = 0

    for user_dir in USERS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        for f in user_dir.glob("conversation_*.json"):
            try:
                if (now - f.stat().st_mtime) > max_age_seconds:
                    f.unlink()
                    removed += 1
            except Exception:
                pass

    return removed


def run_cleanup(max_age_days: int = DEFAULT_MAX_AGE_DAYS, dry_run: bool = False) -> str:
    """Run full cleanup and return summary."""
    lines = [f"Cleanup report ({datetime.now():%Y-%m-%d %H:%M})"]

    if dry_run:
        lines.append("(DRY RUN — no files removed)")
        return "\n".join(lines)

    attachments = cleanup_attachments(max_age_days)
    logs = cleanup_logs()
    temp = cleanup_temp()
    archives = cleanup_archived_conversations()

    lines.append(f"  Attachments removed: {attachments} (>{max_age_days} days)")
    lines.append(f"  Logs truncated: {logs}")
    lines.append(f"  Temp files removed: {temp}")
    lines.append(f"  Archived conversations removed: {archives}")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MyOldMachine cleanup")
    parser.add_argument("--days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(run_cleanup(args.days, args.dry_run))
