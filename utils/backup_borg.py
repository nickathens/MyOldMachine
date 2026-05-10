#!/usr/bin/env python3
"""
Borg backend for MyOldMachine backups.

Provides deduplicated, encrypted, calendar-pruned archives via borgbackup.
Used by utils/backup.py when backup_tool == "borg" in maintenance.json.

Public API:
    init_repo(repo_path, passphrase) -> (ok, msg)
    is_repo(repo_path) -> bool
    create_backup(repo_path, passphrase, sources, compression, hostname, excludes,
                  extra_paths) -> summary str (notify_fn optional)
    list_backups(repo_path, passphrase) -> formatted str
    info_archive(repo_path, passphrase, archive_name) -> formatted str
    restore_backup(repo_path, passphrase, archive_name, target_dir) -> summary str
    prune(repo_path, passphrase, keep_daily, keep_weekly, keep_monthly,
          archive_prefix) -> count_pruned
    generate_passphrase() -> str
    read_passphrase(path) -> str | None
    write_passphrase(path, passphrase, owner_uid_gid=None) -> bool

All borg subprocess calls pass BORG_PASSPHRASE via env, never via argv.
Repository auto-relocation tolerated (BORG_RELOCATED_REPO_ACCESS_IS_OK=yes).
"""

import logging
import os
import re
import secrets
import shutil
import string
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_PREFIX_DEFAULT = "mom"
PASSPHRASE_LENGTH = 32

# Patterns to exclude from borg create (--exclude PATTERN, fnmatch shell-style).
# These mirror the tarball exclude list so users see consistent behavior.
DEFAULT_EXCLUDES = [
    "*/__pycache__",
    "*.pyc",
    "*/.git",
    "*/node_modules",
    "*.log",
    "*/attachments",
]


def _have_borg() -> bool:
    return shutil.which("borg") is not None


def _borg_env(passphrase: str) -> dict:
    """Build the env borg subprocesses need.

    BORG_PASSPHRASE: unlock encrypted repo
    BORG_RELOCATED_REPO_ACCESS_IS_OK: tolerate users moving the repo dir
    BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK: tolerate first-touch warnings
    """
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = passphrase or ""
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    # Disable per-machine cache / security checks that prompt for input.
    env.setdefault("BORG_HOSTNAME_IS_UNIQUE", "no")
    return env


def _run_borg(args, passphrase: str, timeout: int = 3600,
              capture: bool = True) -> subprocess.CompletedProcess:
    """Run a borg command with passphrase env, never echoed.

    Returns CompletedProcess. Caller checks returncode/stderr.
    """
    cmd = ["borg"] + list(args)
    env = _borg_env(passphrase)
    return subprocess.run(
        cmd, env=env, capture_output=capture, text=True, timeout=timeout
    )


def generate_passphrase() -> str:
    """Generate a 32-char random passphrase using only [A-Za-z0-9].

    Avoids shell-special chars so the value is safe to display, copy, paste.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(PASSPHRASE_LENGTH))


def write_passphrase(path: Path, passphrase: str,
                     owner_uid: int | None = None,
                     owner_gid: int | None = None) -> bool:
    """Write a passphrase to disk with mode 0600. Optionally chown."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish: write tmp then rename.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(passphrase + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        if owner_uid is not None and owner_gid is not None:
            try:
                os.chown(str(tmp), owner_uid, owner_gid)
            except (PermissionError, OSError) as e:
                logger.warning(f"Could not chown passphrase file: {e}")
        os.replace(tmp, path)
        return True
    except (OSError, PermissionError) as e:
        logger.error(f"Failed to write passphrase to {path}: {e}")
        return False


def read_passphrase(path) -> str | None:
    """Read a passphrase from disk. Returns None if not found / unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def is_repo(repo_path) -> bool:
    """Cheap structural check: a borg repo always has a `config` file and
    a `data/` subdir at its top level.
    """
    p = Path(repo_path)
    return p.is_dir() and (p / "config").is_file() and (p / "data").is_dir()


def init_repo(repo_path, passphrase: str,
              encryption: str = "repokey-blake2") -> tuple[bool, str]:
    """Initialize an encrypted borg repo at repo_path. Idempotent."""
    if not _have_borg():
        return False, "borg binary not found in PATH"

    p = Path(repo_path)
    if is_repo(p):
        return True, f"borg repo already initialized at {p}"

    # Borg refuses to init into a non-empty dir unless it's a repo. We
    # create the parent and let borg own the leaf.
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"cannot create parent of {p}: {e}"

    # If the dir exists and is non-empty (and not a repo), bail.
    if p.exists() and any(p.iterdir()):
        return False, (
            f"target {p} exists and is non-empty (not a borg repo). "
            f"Choose an empty directory or remove existing contents."
        )

    result = _run_borg(
        ["init", "--encryption", encryption, "--make-parent-dirs", str(p)],
        passphrase=passphrase,
        timeout=120,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return False, f"borg init failed: {stderr or 'unknown error'}"

    return True, f"borg repo initialized at {p} (encryption={encryption})"


def _build_archive_name(prefix: str, hostname: str, when: datetime) -> str:
    """Build a borg archive name. Borg archive names must be valid dir names."""
    safe_host = re.sub(r"[^A-Za-z0-9._-]", "-", hostname or "host")
    return f"{prefix}-{safe_host}-{when:%Y-%m-%d_%H%M%S}"


def create_backup(repo_path, passphrase: str, sources: list,
                  compression: str = "zstd,3",
                  hostname: str | None = None,
                  excludes: list | None = None,
                  extra_paths: list | None = None,
                  archive_prefix: str = ARCHIVE_PREFIX_DEFAULT,
                  timeout: int = 3600) -> tuple[bool, str]:
    """Create one borg archive of the given sources.

    sources: list of absolute path strings (or Path) to back up.
    extra_paths: additional absolute paths to include (skipped if missing).
    Returns (ok, summary).
    """
    if not _have_borg():
        return False, "borg binary not found in PATH"
    if not is_repo(repo_path):
        return False, f"not a borg repo: {repo_path}"

    paths: list[str] = []
    for s in sources:
        sp = Path(s)
        if sp.exists():
            paths.append(str(sp))
        else:
            logger.info(f"Backup source missing, skipping: {s}")
    for s in (extra_paths or []):
        sp = Path(s)
        if sp.exists():
            paths.append(str(sp))
        else:
            logger.info(f"Extra backup path missing, skipping: {s}")

    if not paths:
        return False, "no source paths exist; nothing to back up"

    if hostname is None:
        try:
            hostname = os.uname().nodename
        except AttributeError:
            hostname = "host"

    archive_name = _build_archive_name(archive_prefix, hostname, datetime.now())
    archive_ref = f"{repo_path}::{archive_name}"

    excl = list(excludes or DEFAULT_EXCLUDES)
    args = ["create", "--stats", "--compression", compression]
    for pattern in excl:
        args += ["--exclude", pattern]
    args.append(archive_ref)
    args.extend(paths)

    result = _run_borg(args, passphrase=passphrase, timeout=timeout)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # Borg returns 1 for warnings, 2 for errors. We surface both as failure
        # only when borg itself flags it; warnings are accepted but logged.
        if result.returncode == 1:
            logger.warning(f"borg create completed with warnings: {stderr}")
        else:
            return False, f"borg create failed (rc={result.returncode}): {stderr or 'unknown error'}"

    # Parse stats from stderr. Borg writes "Number of files" etc. there.
    summary = _summarize_create_output(result.stderr or result.stdout, archive_name)
    return True, summary


_STATS_KEYS = {
    "Original size:": "original",
    "Deduplicated size:": "dedup",
    "Number of files:": "files",
    "Time (start):": "start",
    "Duration:": "duration",
}


def _summarize_create_output(text: str, archive_name: str) -> str:
    """Extract the stats footer borg --stats prints and format compactly."""
    if not text:
        return f"Backup created: {archive_name}"
    fields: dict[str, str] = {}
    for line in text.splitlines():
        for marker, key in _STATS_KEYS.items():
            if marker in line:
                # Take everything after the marker, look at the THIS archive col.
                # Borg --stats output has "This archive" column second.
                tail = line.split(marker, 1)[1].strip()
                # Strip leading "This archive:" if present (older borg variants)
                tail = tail.replace("This archive:", "").strip()
                # Take the first whitespace-separated chunk that looks like a value.
                m = re.match(r"([^\s].*?)(?:\s{2,}|$)", tail)
                fields[key] = (m.group(1).strip() if m else tail.split()[0])
                break

    parts = [f"Backup created: {archive_name}"]
    if "files" in fields:
        parts.append(f"files={fields['files']}")
    if "original" in fields:
        parts.append(f"orig={fields['original']}")
    if "dedup" in fields:
        parts.append(f"dedup={fields['dedup']}")
    if "duration" in fields:
        parts.append(f"took {fields['duration']}")
    return " | ".join(parts)


def list_backups(repo_path, passphrase: str,
                 archive_prefix: str | None = None) -> str:
    """List all archives in the repo. Returns formatted multi-line string."""
    if not _have_borg():
        return "borg binary not found in PATH"
    if not is_repo(repo_path):
        return f"not a borg repo: {repo_path}"

    args = ["list", "--short"]
    if archive_prefix:
        args += ["--glob-archives", f"{archive_prefix}-*"]
    args.append(str(repo_path))

    result = _run_borg(args, passphrase=passphrase, timeout=120)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return f"borg list failed: {stderr or 'unknown error'}"

    archives = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if not archives:
        return f"No backups in {repo_path}"

    # Use repo info for total size.
    total_line = ""
    info_result = _run_borg(["info", "--json", str(repo_path)],
                            passphrase=passphrase, timeout=60)
    if info_result.returncode == 0:
        try:
            import json as _json
            data = _json.loads(info_result.stdout)
            cache = data.get("cache", {}) or {}
            stats = cache.get("stats", {}) or {}
            unique_size = stats.get("unique_size")
            if isinstance(unique_size, int):
                total_line = f"\n  Repo size on disk: {_human_bytes(unique_size)}"
        except (ValueError, KeyError, TypeError):
            pass

    lines = [f"Borg backups in {repo_path}:", ""]
    for a in archives:
        lines.append(f"  {a}")
    lines.append(f"\n  Total: {len(archives)} archive(s){total_line}")
    return "\n".join(lines)


def _human_bytes(n: int) -> str:
    """Format byte counts compactly."""
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024:
            return f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} PB"


def info_archive(repo_path, passphrase: str, archive_name: str) -> str:
    """Show details for one archive."""
    if not _have_borg():
        return "borg binary not found in PATH"
    args = ["info", f"{repo_path}::{archive_name}"]
    result = _run_borg(args, passphrase=passphrase, timeout=120)
    if result.returncode != 0:
        return f"borg info failed: {(result.stderr or '').strip()}"
    return result.stdout or "(no output)"


def restore_backup(repo_path, passphrase: str, archive_name: str,
                   target_dir, paths: list | None = None,
                   timeout: int = 3600) -> tuple[bool, str]:
    """Extract one archive into target_dir.

    Borg extracts relative to cwd, so we cd into target_dir before extracting.
    paths: optional list of paths to restrict extraction to.
    """
    if not _have_borg():
        return False, "borg binary not found in PATH"
    if not is_repo(repo_path):
        return False, f"not a borg repo: {repo_path}"

    target = Path(target_dir)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"cannot create target dir {target}: {e}"

    archive_ref = f"{repo_path}::{archive_name}"
    args = ["extract", "--list", archive_ref]
    if paths:
        args.extend(paths)

    cmd = ["borg"] + args
    env = _borg_env(passphrase)
    try:
        result = subprocess.run(
            cmd, env=env, cwd=str(target),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"borg extract timed out after {timeout}s"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return False, f"borg extract failed: {stderr or 'unknown error'}"

    # Count extracted files from --list output (one per line).
    extracted = sum(1 for line in (result.stdout or "").splitlines() if line.strip())
    return True, f"Restored {archive_name} to {target} ({extracted} entries)"


def prune(repo_path, passphrase: str,
          keep_daily: int = 7,
          keep_weekly: int = 4,
          keep_monthly: int = 6,
          archive_prefix: str = ARCHIVE_PREFIX_DEFAULT) -> tuple[bool, str]:
    """Apply retention rules to a borg repo. Calendar-based prune.

    Borg deletes the data when prune runs but space is reclaimed by `borg compact`
    which we do not call here (it can be expensive). Repo size grows slowly
    until next compact. Acceptable trade-off for nightly prune.
    """
    if not _have_borg():
        return False, "borg binary not found in PATH"
    if not is_repo(repo_path):
        return False, f"not a borg repo: {repo_path}"

    args = [
        "prune", "--list", "--stats",
        "--keep-daily", str(int(keep_daily)),
        "--keep-weekly", str(int(keep_weekly)),
        "--keep-monthly", str(int(keep_monthly)),
        "--glob-archives", f"{archive_prefix}-*",
        str(repo_path),
    ]
    result = _run_borg(args, passphrase=passphrase, timeout=600)
    if result.returncode != 0:
        return False, f"borg prune failed: {(result.stderr or '').strip()}"

    # Borg prints "Pruning archive: NAME" lines to stderr/stdout for each prune.
    pruned = 0
    for line in (result.stderr or "").splitlines() + (result.stdout or "").splitlines():
        if "Pruning archive" in line:
            pruned += 1
    return True, f"Pruned {pruned} archive(s)"
