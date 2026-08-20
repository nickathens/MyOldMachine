#!/usr/bin/env python3
"""
Backup utility for MyOldMachine.

Dispatcher for the configured backup tool.
  - tarball (default for back-compat): plain tar.gz, retention by count
  - borg: deduplicated, encrypted, calendar-pruned via borgbackup

Tool selection from data/maintenance.json::backup_tool. Falls back to tarball
when the field is missing or its value is unrecognized.

Designed to run as a scheduled command job (nightly).
Can also be run standalone:
  python utils/backup.py create [--target /path]
  python utils/backup.py list
  python utils/backup.py restore --archive <name-or-path> [--target /path]
"""

import json
import logging
import os
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

logger = logging.getLogger(__name__)

# Default retention for tarball mode (count-based). Canonical value: every
# other module reads this rather than repeating a literal, so the default
# cannot drift between the writer, the installer, and the status readout.
#
# Kept at one. A tarball is a full copy, not a delta, so each night costs the
# whole archive again, and the target is normally a directory on the same disk
# as the originals it protects. The prune runs after the new archive is renamed
# into place, so a retention of N needs room for N + 1 archives at the peak of
# the run, and a growing project tree moves that peak every night: on this Mac
# mini the nightly archive went 22 GB to 66 GB in six days, two copies reached
# 116 GB (a quarter of the volume), and the run on 2026-08-20 drove the disk to
# zero and died mid-write with ENOSPC.
#
# What the second copy bought was a survivor if the newest archive is corrupt
# or truncated. That is better bought from the snapshot layer already covering
# the volume (Time Machine on macOS), which keeps history without paying for a
# whole extra copy nightly on the disk being protected, and which is also where
# a user who wants real depth should be pointed. Raising this to 2 or more is a
# supported choice per install via `backup_retention`; it just should not be
# what every machine pays by default.
DEFAULT_RETENTION = 1

# Suffix an archive carries while it is still being written. The prune and the
# listing both glob for "myoldmachine_*.tar.gz", and neither opens a candidate,
# so a half-written archive left at the final name would count as a backup and
# evict a good one. Writing under this suffix and renaming on success means an
# interrupted run leaves nothing either of them can see. The rename is atomic
# within a directory, so no archive is ever visible in a partial state.
PARTIAL_SUFFIX = ".part"

# How long a partial must have gone untouched before a later run treats it as
# abandoned and deletes it. Nothing in-process can clean up after SIGKILL, so
# the sweep is the only thing that stops a dead 20GB partial sitting there
# forever. tarfile writes continuously, so a run that is genuinely still going
# has a current mtime whatever its total duration; only a dead one goes quiet.
# This matters because the backup has no lock: the nightly job and
# `/maintenance run backup` can overlap, and the sweep must not touch a live
# partial belonging to the other one.
STALE_PARTIAL_AGE_SEC = 3600

# Default retention for borg mode (calendar-based).
DEFAULT_KEEP_DAILY = 7
DEFAULT_KEEP_WEEKLY = 4
DEFAULT_KEEP_MONTHLY = 6

# Directories/files to back up (relative to BOT_DIR).
# Same scope for both tools so users see consistent coverage on either backend.
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

# Patterns to exclude from backup.
EXCLUDE_PATTERNS = [
    "__pycache__",
    "*.pyc",
    ".git",
    "node_modules",
    "*.log",
    "attachments/",
]


def get_maintenance_config() -> dict:
    """Load maintenance config (lazy import to avoid circular deps in tests)."""
    config_file = DATA_DIR / "maintenance.json"
    if config_file.exists():
        try:
            return json.loads(config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return {}


def _backup_tool(config: dict | None = None) -> str:
    """Return the configured backup tool. Defaults to 'tarball'."""
    cfg = config if config is not None else get_maintenance_config()
    tool = (cfg.get("backup_tool") or "tarball").strip().lower()
    return "borg" if tool == "borg" else "tarball"


# ---------------------------------------------------------------------------
# Public dispatcher API
# ---------------------------------------------------------------------------

def create_backup(target_dir: str, notify_fn=None) -> str:
    """Create a backup using the configured tool. Returns a summary string."""
    config = get_maintenance_config()
    tool = _backup_tool(config)
    if tool == "borg":
        return _create_borg_backup(target_dir, config, notify_fn=notify_fn)
    return _create_tarball_backup(target_dir, notify_fn=notify_fn)


def list_backups(target_dir: str) -> str:
    """List existing backups for the configured tool."""
    config = get_maintenance_config()
    tool = _backup_tool(config)
    if tool == "borg":
        return _list_borg_backups(target_dir, config)
    return _list_tarball_backups(target_dir)


def restore_backup(archive: str, restore_dir: str | None = None) -> str:
    """Restore from a backup. For tarball: archive is a .tar.gz path. For borg:
    archive is the archive name (e.g. mom-host-2026-05-10_0200), and the repo
    is read from maintenance.json::backup_path.
    """
    config = get_maintenance_config()
    tool = _backup_tool(config)
    if tool == "borg":
        repo = config.get("backup_path") or ""
        return _restore_borg_backup(repo, archive, restore_dir, config)
    return _restore_tarball_backup(archive, restore_dir)


# ---------------------------------------------------------------------------
# Tarball backend (preserved verbatim from prior implementation)
# ---------------------------------------------------------------------------

def _should_exclude(path: str) -> bool:
    """Check if a path should be excluded from a tarball backup."""
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


def _open_log(target_dir: str) -> "callable":
    """Build a (log, notify) closure pair for backup runs.

    Under MOM_TEST the file write is skipped, the same gate bot.py and
    utils/reflect.py use for their logs. Tests drive real backup runs over
    temp trees, and their synthetic "Backup complete" lines are
    indistinguishable from the real thing once they are in the file an
    operator reads to find out whether last night's backup ran.
    """
    if os.environ.get("MOM_TEST"):
        return lambda msg: logger.info(msg)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "backup.log"

    # Rotate if it grew big.
    try:
        if log_file.exists() and log_file.stat().st_size > 200_000:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            log_file.write_text("\n".join(lines[-100:]) + "\n", encoding="utf-8")
    except OSError:
        pass

    def log(msg: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {msg}\n")
        except OSError:
            pass
        logger.info(msg)

    return log


def _create_tarball_backup(target_dir: str, notify_fn=None) -> str:
    log = _open_log(target_dir)

    def notify(msg: str):
        if notify_fn:
            try:
                notify_fn(msg)
            except Exception as e:
                logger.warning(f"Notification failed: {e}")

    log("=== Backup started (tarball) ===")

    target = Path(target_dir)
    if not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
            log(f"Created backup directory: {target}")
        except OSError as e:
            msg = f"Backup failed: cannot create target directory {target}: {e}"
            log(msg)
            notify(msg)
            return msg

    if not os.access(str(target), os.W_OK):
        msg = f"Backup failed: no write permission to {target}"
        log(msg)
        notify(msg)
        return msg

    try:
        st = os.statvfs(str(target))
        free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        if free_mb < 100:
            msg = f"Backup failed: only {free_mb:.0f}MB free on {target}"
            log(msg)
            notify(msg)
            return msg
    except OSError:
        pass

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    archive_name = f"myoldmachine_{timestamp}.tar.gz"
    archive_path = target / archive_name
    partial_path = target / (archive_name + PARTIAL_SUFFIX)

    swept = _sweep_stale_partials(target)
    if swept:
        log(f"Discarded {swept} abandoned partial archive(s) from an interrupted run")

    file_count = 0
    try:
        # Create the archive 0600 before writing anything into it: it bundles
        # .env (the bot token + every provider API key) in cleartext, and the
        # default umask would otherwise leave it world-readable (0644) on a
        # shared or removable backup target for the whole write. os.chmod covers
        # the rare case of reusing a stale archive name from the same minute.
        # The mode travels with the rename, so the published archive is 0600 too.
        os.close(os.open(str(partial_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        os.chmod(str(partial_path), 0o600)
        with tarfile.open(str(partial_path), "w:gz", compresslevel=6, dereference=True) as tar:
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
        # The archive is closed and complete only here. Publishing it under the
        # name the prune and the listing look for is the last thing that
        # happens, so an interrupted run can never leave one behind.
        os.replace(str(partial_path), str(archive_path))
    except Exception as e:
        msg = f"Backup failed: {e}"
        log(msg)
        notify(msg)
        try:
            partial_path.unlink(missing_ok=True)
        except OSError:
            pass
        return msg

    try:
        size_mb = archive_path.stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0

    log(f"Archive: {archive_name} ({size_mb:.1f} MB, {file_count} files)")

    config = get_maintenance_config()
    retention = config.get("backup_retention", DEFAULT_RETENTION)
    pruned = _prune_old_tarballs(target, retention)
    if pruned:
        log(f"Pruned {pruned} old backup(s) (retention: {retention})")

    try:
        st = os.statvfs(str(target))
        free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        free_str = f"{free_gb:.1f}GB free"
    except OSError:
        free_str = "unknown free"

    summary = f"Backup complete: {archive_name} ({size_mb:.1f} MB, {file_count} files). {free_str} on target."
    log(summary)
    notify(summary)
    log("=== Backup complete ===")
    return summary


def _sweep_stale_partials(target: Path, now: float | None = None) -> int:
    """Delete partials left behind by a run that died without cleaning up.

    Only ones that have gone untouched for STALE_PARTIAL_AGE_SEC: a partial
    still being written by a concurrent run has a current mtime, and deleting
    that would break a backup that is working.
    """
    now = time.time() if now is None else now
    removed = 0
    for partial in target.glob(f"myoldmachine_*.tar.gz{PARTIAL_SUFFIX}"):
        try:
            if not partial.is_file() or now - partial.stat().st_mtime < STALE_PARTIAL_AGE_SEC:
                continue
            partial.unlink()
            removed += 1
        except OSError as e:
            logger.warning(f"Failed to remove abandoned partial {partial.name}: {e}")
    return removed


def _prune_old_tarballs(target: Path, keep: int) -> int:
    """Remove old tarballs, keeping the most recent `keep` archives.

    Partials are invisible here by construction: they carry PARTIAL_SUFFIX
    until the archive is complete, so this glob cannot match one and a killed
    run cannot spend a retention slot.
    """
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
        except OSError as e:
            logger.warning(f"Failed to remove old backup {old.name}: {e}")
    return removed


def _list_tarball_backups(target_dir: str) -> str:
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

    total_mb = sum(b.stat().st_size for b in backups) / (1024 * 1024)
    lines.append(f"\n  Total: {len(backups)} backup(s), {total_mb:.1f} MB")
    return "\n".join(lines)


def _restore_tarball_backup(archive_path: str, restore_dir: str | None = None) -> str:
    archive = Path(archive_path)
    if not archive.exists():
        return f"Archive not found: {archive}"

    target = Path(restore_dir) if restore_dir else BOT_DIR

    try:
        with tarfile.open(str(archive), "r:gz") as tar:
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

            try:
                tar.extractall(str(target), filter='data')
            except TypeError:
                tar.extractall(str(target))
        return f"Restored from {archive.name} to {target}"
    except Exception as e:
        return f"Restore failed: {e}"


# ---------------------------------------------------------------------------
# Borg backend (delegates to utils.backup_borg)
# ---------------------------------------------------------------------------

def _read_passphrase(config: dict) -> str | None:
    """Read the borg passphrase from the path stored in config."""
    pass_path = config.get("backup_passphrase_path")
    if not pass_path:
        return None
    try:
        return Path(pass_path).read_text(encoding="utf-8").strip() or None
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _create_borg_backup(target_dir: str, config: dict, notify_fn=None) -> str:
    """Wrap utils.backup_borg.create_backup with the same logging/notify
    contract as the tarball path."""
    from utils import backup_borg as _bb

    log = _open_log(target_dir)

    def notify(msg: str):
        if notify_fn:
            try:
                notify_fn(msg)
            except Exception as e:
                logger.warning(f"Notification failed: {e}")

    log("=== Backup started (borg) ===")

    repo_path = target_dir or config.get("backup_path") or ""
    if not repo_path:
        msg = "Backup failed: no repo path configured"
        log(msg)
        notify(msg)
        return msg

    if not _bb.is_repo(repo_path):
        msg = (
            f"Backup failed: {repo_path} is not a borg repo. "
            f"Run /maintenance backup-tool borg to (re-)initialize."
        )
        log(msg)
        notify(msg)
        return msg

    passphrase = _read_passphrase(config)
    if not passphrase:
        msg = "Backup failed: borg passphrase not available"
        log(msg)
        notify(msg)
        return msg

    sources = [str(BOT_DIR / s) for s in BACKUP_SOURCES]
    extra_paths = list(config.get("backup_extra_paths") or [])
    compression = config.get("backup_compression", "zstd,3")

    try:
        ok, summary = _bb.create_backup(
            repo_path=repo_path,
            passphrase=passphrase,
            sources=sources,
            compression=compression,
            extra_paths=extra_paths,
        )
    except Exception as e:
        msg = f"Backup failed: {e}"
        log(msg)
        notify(msg)
        return msg

    log(summary)
    if not ok:
        notify(summary)
        return summary

    keep_daily = int(config.get("backup_keep_daily", DEFAULT_KEEP_DAILY))
    keep_weekly = int(config.get("backup_keep_weekly", DEFAULT_KEEP_WEEKLY))
    keep_monthly = int(config.get("backup_keep_monthly", DEFAULT_KEEP_MONTHLY))

    try:
        prune_ok, prune_msg = _bb.prune(
            repo_path=repo_path,
            passphrase=passphrase,
            keep_daily=keep_daily,
            keep_weekly=keep_weekly,
            keep_monthly=keep_monthly,
        )
        if prune_ok:
            log(prune_msg)
        else:
            log(f"Prune warning: {prune_msg}")
    except Exception as e:
        log(f"Prune raised: {e}")

    notify(summary)
    log("=== Backup complete ===")
    return summary


def _list_borg_backups(target_dir: str, config: dict) -> str:
    from utils import backup_borg as _bb

    repo_path = target_dir or config.get("backup_path") or ""
    if not repo_path:
        return "Borg repo path not configured."
    passphrase = _read_passphrase(config)
    if not passphrase:
        return "Borg passphrase not available."
    return _bb.list_backups(repo_path, passphrase)


def _restore_borg_backup(repo_path: str, archive_name: str,
                         restore_dir: str | None, config: dict) -> str:
    from utils import backup_borg as _bb

    if not repo_path:
        return "Borg repo path not configured."
    passphrase = _read_passphrase(config)
    if not passphrase:
        return "Borg passphrase not available."

    target = restore_dir or str(BOT_DIR)
    ok, msg = _bb.restore_backup(
        repo_path=repo_path,
        passphrase=passphrase,
        archive_name=archive_name,
        target_dir=target,
    )
    return msg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="MyOldMachine backup utility")
    parser.add_argument("action", choices=["create", "list", "restore"],
                        help="Action to perform")
    parser.add_argument("--target", type=str,
                        help="Backup target directory (or restore destination)")
    parser.add_argument("--archive", type=str,
                        help="Archive path (tarball) or archive name (borg) for restore")
    parser.add_argument("--notify", action="store_true",
                        help="Send Telegram notification")
    parser.add_argument("--user-id", type=str, default="",
                        help="Telegram user ID for notifications")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = get_maintenance_config()
    if not args.target and args.action in ("create", "list"):
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
