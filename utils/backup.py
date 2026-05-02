#!/usr/bin/env python3
"""
Backup utility for MyOldMachine.

Creates compressed tar archives of the bot's data, config, and code.
Supports configurable backup location, retention policy, and notifications.

Designed to run as a scheduled command job (nightly).
Can also be run standalone: python utils/backup.py --target /path/to/backups
"""

import json
import logging
import os
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

logger = logging.getLogger(__name__)

# Default retention: keep this many backups
DEFAULT_RETENTION = 7

# Directories/files to back up (relative to BOT_DIR)
BACKUP_SOURCES = [
    "bot.py",
    "core/",
    "utils/",
    "skills/",
    "data/users/",
    "data/memory/",
    "data/scheduler/",
    ".env",
]

# Patterns to exclude from backup
EXCLUDE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    ".git",
    "node_modules",
    "*.log",
    "attachments/",  # Large user uploads, not critical
]


def get_maintenance_config() -> dict:
    """Load maintenance config."""
    config_file = DATA_DIR / "maintenance.json"
    if config_file.exists():
        try:
            return json.loads(config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return {}


def _should_exclude(path: str) -> bool:
    """Check if a path should be excluded from backup."""
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            if path.endswith(pattern[1:]):
                return True
        elif pattern.endswith("/"):
            if f"/{pattern}" in path or path.startswith(pattern):
                return True
        else:
            if f"/{pattern}/" in path or f"/{pattern}" in path:
                return True
    return False


def create_backup(target_dir: str, notify_fn=None) -> str:
    """
    Create a backup archive.

    target_dir: directory to store the backup archive
    notify_fn: optional callable(message: str) for notifications
    Returns a summary string.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "backup.log"

    def log(msg: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {msg}\n")
        except Exception:
            pass
        logger.info(msg)

    def notify(msg: str):
        if notify_fn:
            try:
                notify_fn(msg)
            except Exception as e:
                logger.warning(f"Notification failed: {e}")

    # Rotate log
    try:
        if log_file.exists() and log_file.stat().st_size > 200_000:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            log_file.write_text("\n".join(lines[-100:]) + "\n", encoding="utf-8")
    except Exception:
        pass

    log("=== Backup started ===")

    # Validate target directory
    target = Path(target_dir)
    if not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
            log(f"Created backup directory: {target}")
        except Exception as e:
            msg = f"Backup failed: cannot create target directory {target}: {e}"
            log(msg)
            notify(msg)
            return msg

    if not os.access(str(target), os.W_OK):
        msg = f"Backup failed: no write permission to {target}"
        log(msg)
        notify(msg)
        return msg

    # Check disk space (need at least 100MB free on target)
    try:
        st = os.statvfs(str(target))
        free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        if free_mb < 100:
            msg = f"Backup failed: only {free_mb:.0f}MB free on {target}"
            log(msg)
            notify(msg)
            return msg
    except Exception:
        pass

    # Build archive
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    archive_name = f"myoldmachine_{timestamp}.tar.gz"
    archive_path = target / archive_name

    file_count = 0
    try:
        with tarfile.open(str(archive_path), "w:gz", compresslevel=6, dereference=True) as tar:
            for source in BACKUP_SOURCES:
                full_path = BOT_DIR / source
                if not full_path.exists():
                    continue

                if full_path.is_file():
                    if not _should_exclude(source):
                        tar.add(str(full_path), arcname=source)
                        file_count += 1
                elif full_path.is_dir():
                    for root, dirs, files in os.walk(str(full_path)):
                        # Skip excluded directories
                        dirs[:] = [d for d in dirs
                                   if not _should_exclude(d + "/")
                                   and not _should_exclude(d)]
                        for f in files:
                            file_path = os.path.join(root, f)
                            rel_path = os.path.relpath(file_path, str(BOT_DIR))
                            if not _should_exclude(rel_path):
                                try:
                                    tar.add(file_path, arcname=rel_path)
                                    file_count += 1
                                except (PermissionError, OSError) as e:
                                    log(f"  Skipped {rel_path}: {e}")
    except Exception as e:
        msg = f"Backup failed: {e}"
        log(msg)
        notify(msg)
        # Clean up partial archive
        try:
            archive_path.unlink(missing_ok=True)
        except Exception:
            pass
        return msg

    # Get archive size
    try:
        size_mb = archive_path.stat().st_size / (1024 * 1024)
    except Exception:
        size_mb = 0

    log(f"Archive: {archive_name} ({size_mb:.1f} MB, {file_count} files)")

    # Enforce retention policy
    config = get_maintenance_config()
    retention = config.get("backup_retention", DEFAULT_RETENTION)
    pruned = _prune_old_backups(target, retention)
    if pruned:
        log(f"Pruned {pruned} old backup(s) (retention: {retention})")

    # Get free space on target after backup
    try:
        st = os.statvfs(str(target))
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        free_str = f"{free_gb:.1f}GB free"
    except Exception:
        free_str = "unknown free"

    summary = f"Backup complete: {archive_name} ({size_mb:.1f} MB, {file_count} files). {free_str} on target."
    log(summary)
    notify(summary)
    log("=== Backup complete ===")
    return summary


def _prune_old_backups(target: Path, keep: int) -> int:
    """Remove old backups, keeping the most recent `keep` archives."""
    backups = sorted(
        [f for f in target.glob("myoldmachine_*.tar.gz") if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    removed = 0
    for old in backups[keep:]:
        try:
            old.unlink()
            removed += 1
        except Exception as e:
            logger.warning(f"Failed to remove old backup {old.name}: {e}")
    return removed


def list_backups(target_dir: str) -> str:
    """List existing backups in the target directory."""
    target = Path(target_dir)
    if not target.exists():
        return f"Backup directory does not exist: {target}"

    backups = sorted(
        [f for f in target.glob("myoldmachine_*.tar.gz") if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not backups:
        return f"No backups found in {target}"

    lines = [f"Backups in {target}:", ""]
    for b in backups:
        size_mb = b.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(b.stat().st_mtime)
        lines.append(f"  {b.name} ({size_mb:.1f} MB, {mtime:%Y-%m-%d %H:%M})")

    # Total size
    total_mb = sum(b.stat().st_size for b in backups) / (1024 * 1024)
    lines.append(f"\n  Total: {len(backups)} backup(s), {total_mb:.1f} MB")
    return "\n".join(lines)


def restore_backup(archive_path: str, restore_dir: str = None) -> str:
    """
    Restore from a backup archive.

    archive_path: path to the .tar.gz archive
    restore_dir: where to extract (defaults to BOT_DIR)
    Returns a summary string.
    """
    archive = Path(archive_path)
    if not archive.exists():
        return f"Archive not found: {archive}"

    target = Path(restore_dir) if restore_dir else BOT_DIR

    try:
        with tarfile.open(str(archive), "r:gz") as tar:
            # Security: reject symlink/hardlink members outright, and validate
            # every path for traversal. create_backup() archives with
            # dereference=True, so legitimate archives never contain link
            # members -- any such member here is a sign of tampering.
            resolved_target = target.resolve()
            for member in tar.getmembers():
                if member.issym() or member.islnk():
                    return (
                        f"Restore aborted: archive contains link member "
                        f"(type={member.type!r}): {member.name} -> {member.linkname}"
                    )
                if member.isdev() or member.isfifo():
                    return (
                        f"Restore aborted: archive contains special file: "
                        f"{member.name}"
                    )
                member_path = Path(target / member.name).resolve()
                if not member_path.is_relative_to(resolved_target):
                    return f"Restore aborted: archive contains unsafe path: {member.name}"

            # Use tar filter for safe extraction (Python 3.12+). Fall back to
            # the unfiltered call on older Pythons; we've already rejected
            # link/device members above so this is still safe.
            try:
                tar.extractall(str(target), filter='data')
            except TypeError:
                tar.extractall(str(target))
        return f"Restored from {archive.name} to {target}"
    except Exception as e:
        return f"Restore failed: {e}"


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="MyOldMachine backup utility")
    parser.add_argument("action", choices=["create", "list", "restore"],
                        help="Action to perform")
    parser.add_argument("--target", type=str,
                        help="Backup target directory")
    parser.add_argument("--archive", type=str,
                        help="Archive path (for restore)")
    parser.add_argument("--notify", action="store_true",
                        help="Send Telegram notification")
    parser.add_argument("--user-id", type=str, default="",
                        help="Telegram user ID for notifications")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load target from config if not specified
    if not args.target:
        config = get_maintenance_config()
        args.target = config.get("backup_path", "")

    if args.action == "create":
        if not args.target:
            print("Error: --target required (or set backup_path in maintenance config)")
            sys.exit(1)

        notify_fn = None
        if args.notify and args.user_id:
            send_script = BOT_DIR / "utils" / "send_to_telegram.py"
            venv_python = BOT_DIR / ".venv" / "bin" / "python"
            python_cmd = str(venv_python) if venv_python.exists() else sys.executable

            def notify_fn(msg):
                subprocess.run(
                    [python_cmd, str(send_script), "--user", args.user_id, "--message", msg],
                    timeout=30, capture_output=True,
                )

        print(create_backup(args.target, notify_fn=notify_fn))

    elif args.action == "list":
        if not args.target:
            print("Error: --target required")
            sys.exit(1)
        print(list_backups(args.target))

    elif args.action == "restore":
        if not args.archive:
            print("Error: --archive required for restore")
            sys.exit(1)
        print(restore_backup(args.archive, args.target))


if __name__ == "__main__":
    main()
