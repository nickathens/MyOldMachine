#!/usr/bin/env python3
"""
Auto-cleanup for MyOldMachine.

Removes old attachments, temp files, and log rotations.
Designed to run as a scheduled command job (daily at 3 AM).
"""

import gzip
import logging
import os
import shutil
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

# Supervisor-owned logs: launchd (macOS) and systemd (Linux) redirect the
# process stdout/stderr here. Nothing inside the bot rotates them, because the
# supervisor opens the file once at spawn and holds that descriptor for the
# whole process lifetime.
SERVICE_LOG_PATTERNS = ("*.launchd.log", "*.systemd.log")

# Rotate at 20 MB and keep 5 compressed generations (~10 weeks at the observed
# ~1.5 MB/day). Deliberately lower than cleanup_logs()' 50 MB backstop so
# service logs are archived before that blunt truncate would discard them.
SERVICE_LOG_MAX_MB = 20
SERVICE_LOG_KEEP = 5


def _is_service_log(path: Path) -> bool:
    """True if `path` is a supervisor-owned stdout/stderr log."""
    return any(path.match(pat) for pat in SERVICE_LOG_PATTERNS)


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


def rotate_service_logs(
    max_size_mb: int = SERVICE_LOG_MAX_MB,
    keep: int = SERVICE_LOG_KEEP,
) -> int:
    """Rotate supervisor-owned logs, preserving history as .gz archives.

    Uses copy-truncate rather than rename: launchd and systemd open the
    stdout/stderr file once at spawn and hold that descriptor until the process
    exits. A rename would leave the supervisor appending to the renamed inode
    forever, so the "fresh" log would stay empty while the archive grew without
    bound. Truncating in place keeps the inode the supervisor is writing to.

    The write offset is not a concern: both supervisors open these files
    O_APPEND (verified via lsof), so the kernel seeks to EOF on every write and
    a truncated file simply resumes from zero instead of leaving a sparse hole.

    Like logrotate's copytruncate, this has a small race: lines written between
    the copy and the truncate are lost. The window is milliseconds and the
    alternative (restarting the service to reopen the descriptor) is worse.
    """
    if not LOG_DIR.exists():
        return 0

    rotated = 0
    max_size = max_size_mb * 1024 * 1024

    for pattern in SERVICE_LOG_PATTERNS:
        for log_file in LOG_DIR.glob(pattern):
            try:
                if log_file.stat().st_size <= max_size:
                    continue

                # bot.launchd.log -> bot.launchd.until-2026-07-21.log.gz,
                # matching the archive naming already in use on disk.
                stem = log_file.name[: -len(".log")]
                stamp = datetime.now().strftime("%Y-%m-%d")
                archive = LOG_DIR / f"{stem}.until-{stamp}.log.gz"
                seq = 1
                while archive.exists():
                    archive = LOG_DIR / f"{stem}.until-{stamp}.{seq}.log.gz"
                    seq += 1

                # Compress first, truncate only if that succeeded: a failed
                # archive must not cost us the log it was meant to preserve.
                with open(log_file, "rb") as src, gzip.open(archive, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.truncate(log_file, 0)
                rotated += 1

                archives = sorted(
                    LOG_DIR.glob(f"{stem}.until-*.log.gz"),
                    key=lambda p: p.stat().st_mtime,
                )
                for old in archives[:-keep] if keep > 0 else archives:
                    old.unlink()
            except Exception as e:
                logger.warning(f"Failed to rotate {log_file}: {e}")

    return rotated


def cleanup_logs(max_size_mb: int = 50) -> int:
    """Truncate oversized app logs as a last-resort backstop.

    Service logs are excluded: rotate_service_logs() archives those with their
    history intact, whereas this discards everything but the last 1 MB.
    """
    if not LOG_DIR.exists():
        return 0

    truncated = 0
    max_size = max_size_mb * 1024 * 1024

    for log_file in LOG_DIR.glob("*.log"):
        if _is_service_log(log_file):
            continue
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
    # Rotate before truncating so oversized service logs are archived by
    # rotate_service_logs() rather than reaching cleanup_logs' 50 MB backstop.
    service_logs = rotate_service_logs()
    logs = cleanup_logs()
    temp = cleanup_temp()
    archives = cleanup_archived_conversations()

    lines.append(f"  Attachments removed: {attachments} (>{max_age_days} days)")
    lines.append(f"  Service logs rotated: {service_logs}")
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
