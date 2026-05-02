#!/usr/bin/env python3
"""
MyOldMachine: Multi-user OS abstraction layer.

All OS-specific calls for slot-based isolation live here:
- Creating / deleting system users (Linux useradd, macOS sysadminctl)
- File ownership and permissions
- Sudoers fragment installation with visudo validation
- CLI binary path resolution

Privacy model:
- mom_orchestrator runs the bot process and dispatches CLI subprocesses
- mom_user1..mom_user4 own each slot's data directory (mode 02770, setgid +
  group=mom_orchestrator so subdirs created by the slot CLI inherit the
  orchestrator group and the bot can clean them up without sudo)
- Sudoers grants mom_orchestrator the right to spawn ONLY the CLI binaries
  as ONLY the slot accounts. No other commands, no other targets.

The kernel enforces every cross-user file access. This module's only job
is to set up the substrate correctly.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

ORCHESTRATOR_USER = "mom_orchestrator"
SLOT_USER_PREFIX = "mom_user"
SUDOERS_FRAGMENT_PATH = "/etc/sudoers.d/myoldmachine"
USER_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")


# ─── Helpers ──────────────────────────────────────────────────────────


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _validate_username(name: str) -> None:
    if not USER_NAME_RE.fullmatch(name):
        raise ValueError(
            f"Invalid system user name: {name!r}. "
            f"Must match {USER_NAME_RE.pattern}."
        )


def _run(cmd: list[str], *, timeout: int = 30, input_data: Optional[str] = None,
         check: bool = False) -> subprocess.CompletedProcess:
    """Run a command without shell interpolation. Never use shell=True here."""
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        input=input_data, check=check,
    )


def _sudo_run(cmd: list[str], password: Optional[str] = None, *,
              timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command via sudo. Password is piped via stdin if provided."""
    if password is not None:
        full = ["sudo", "-S", "--"] + cmd
        return _run(full, timeout=timeout, input_data=password + "\n")
    full = ["sudo", "-n", "--"] + cmd
    return _run(full, timeout=timeout)


# ─── User existence ───────────────────────────────────────────────────


def system_user_exists(name: str) -> bool:
    """True if the named OS user exists on this machine."""
    _validate_username(name)
    try:
        result = _run(["id", "-u", name], timeout=10)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


# ─── User creation ────────────────────────────────────────────────────


def create_system_user(
    name: str,
    *,
    password: Optional[str] = None,
    home_dir: Optional[Path] = None,
) -> bool:
    """Create a system user with no login shell. Idempotent.

    Returns True if the user exists at end (whether we created it or it
    already existed). Returns False if creation failed.
    """
    _validate_username(name)

    if system_user_exists(name):
        logger.info(f"System user {name} already exists")
        return True

    if _is_linux():
        cmd = [
            "useradd",
            "-r",                                   # system account
            "-s", "/usr/sbin/nologin",              # no shell
        ]
        if home_dir is not None:
            cmd += ["-d", str(home_dir), "-M"]      # set home but don't create
        else:
            cmd += ["-M"]                            # no home directory at all
        cmd.append(name)
        result = _sudo_run(cmd, password)
        if result.returncode != 0:
            logger.error(
                f"Failed to create user {name}: rc={result.returncode} "
                f"stderr={result.stderr.strip()[:300]}"
            )
            return False
        return system_user_exists(name)

    if _is_macos():
        # macOS role accounts use UIDs 200-400 by default. sysadminctl picks one.
        cmd = ["sysadminctl", "-addUser", name, "-roleAccount", "-fullName", name]
        result = _sudo_run(cmd, password, timeout=120)
        if result.returncode != 0:
            logger.error(
                f"Failed to create macOS role account {name}: "
                f"rc={result.returncode} stderr={result.stderr.strip()[:300]}"
            )
            return False
        return system_user_exists(name)

    logger.error(f"Unsupported OS for system user creation: {platform.system()}")
    return False


def delete_system_user(name: str, *, password: Optional[str] = None) -> bool:
    """Delete a system user. Idempotent: no-op if user doesn't exist."""
    _validate_username(name)

    if not system_user_exists(name):
        return True

    if _is_linux():
        result = _sudo_run(["userdel", name], password, timeout=30)
        if result.returncode != 0:
            logger.error(
                f"Failed to delete user {name}: rc={result.returncode} "
                f"stderr={result.stderr.strip()[:300]}"
            )
            return False
        return True

    if _is_macos():
        result = _sudo_run(["sysadminctl", "-deleteUser", name], password, timeout=120)
        if result.returncode != 0:
            logger.error(
                f"Failed to delete macOS user {name}: "
                f"rc={result.returncode} stderr={result.stderr.strip()[:300]}"
            )
            return False
        return True

    return False


# ─── Ownership and permissions ────────────────────────────────────────


def set_owner(path: Path, user: str, group: Optional[str] = None,
              *, password: Optional[str] = None, recursive: bool = False) -> bool:
    """chown a path to user[:group]. Returns True on success."""
    _validate_username(user)
    if group is not None:
        _validate_username(group)

    target = f"{user}:{group}" if group else user
    cmd = ["chown"]
    if recursive:
        cmd.append("-R")
    cmd += [target, str(path)]

    result = _sudo_run(cmd, password)
    if result.returncode != 0:
        logger.error(
            f"chown {target} {path} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()[:200]}"
        )
        return False
    return True


def set_perms(path: Path, mode: int, *, password: Optional[str] = None,
              recursive: bool = False) -> bool:
    """chmod a path. Mode is an integer like 0o750."""
    if not 0 <= mode <= 0o7777:
        raise ValueError(f"Invalid mode: {oct(mode)}")

    cmd = ["chmod"]
    if recursive:
        cmd.append("-R")
    cmd += [f"{mode:o}", str(path)]

    result = _sudo_run(cmd, password)
    if result.returncode != 0:
        logger.error(
            f"chmod {oct(mode)} {path} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()[:200]}"
        )
        return False
    return True


# ─── Sudoers ──────────────────────────────────────────────────────────


def find_cli_binary(name: str) -> Optional[Path]:
    """Find a CLI binary on PATH. Returns the absolute symlink path, not resolved.

    sudo's command-matching is literal: it compares the command path argv[0]
    against the sudoers entry as written. npm-installed CLIs (claude, codex)
    are typically symlinks under /usr/local/bin/ pointing to .js files in
    node_modules. We must NOT resolve the symlink, because the bot will
    invoke the binary via its PATH location and that's what sudo will see.
    """
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found).absolute()
    if not path.exists():
        return None
    return path


def build_sudoers_fragment(
    orchestrator: str,
    target_users: Iterable[str],
    binaries: Iterable[Path],
) -> str:
    """Build the sudoers fragment text. Does not write or validate."""
    _validate_username(orchestrator)
    targets = []
    for u in target_users:
        _validate_username(u)
        targets.append(u)
    if not targets:
        raise ValueError("At least one target user is required")

    bins = []
    for b in binaries:
        path = Path(b).absolute()
        if not path.is_absolute():
            raise ValueError(f"Binary path must be absolute: {b}")
        bins.append(str(path))
    if not bins:
        raise ValueError("At least one binary path is required")

    runas = ", ".join(targets)
    cmnds = ", ".join(bins)
    lines = [
        "# Managed by MyOldMachine: do not edit manually.",
        "# Grants the orchestrator the right to spawn CLI subprocesses",
        "# as the per-slot system users. Limited to specific binaries.",
        f"Runas_Alias MOM_USERS = {runas}",
        f"Cmnd_Alias MOM_BINS = {cmnds}",
        f"{orchestrator} ALL=(MOM_USERS) NOPASSWD: MOM_BINS",
        "",
    ]
    return "\n".join(lines)


def _find_visudo() -> Optional[str]:
    """Locate visudo. Falls back to /usr/sbin and /sbin since those are
    typically not in a non-root user's PATH but visudo always lives there.
    """
    found = shutil.which("visudo")
    if found:
        return found
    for candidate in ("/usr/sbin/visudo", "/sbin/visudo"):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def validate_sudoers_fragment(content: str) -> tuple[bool, str]:
    """Run visudo -cf on the content. Returns (ok, message)."""
    visudo = _find_visudo()
    if not visudo:
        return False, "visudo binary not found: cannot validate sudoers"

    fd, tmp_path = tempfile.mkstemp(prefix="mom_sudoers_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp_path, 0o440)
        result = _run([visudo, "-cf", tmp_path], timeout=10)
        ok = result.returncode == 0
        msg = result.stdout.strip() or result.stderr.strip() or "no output"
        return ok, msg
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def grant_sudo(
    orchestrator: str,
    target_users: Iterable[str],
    binaries: Iterable[Path],
    *,
    password: Optional[str] = None,
    fragment_path: str = SUDOERS_FRAGMENT_PATH,
) -> tuple[bool, str]:
    """Build, validate, and install the sudoers fragment.

    Returns (success, message). Never installs an unvalidated fragment.
    """
    content = build_sudoers_fragment(orchestrator, target_users, binaries)
    ok, msg = validate_sudoers_fragment(content)
    if not ok:
        return False, f"sudoers validation failed: {msg}"

    fd, tmp_path = tempfile.mkstemp(prefix="mom_sudoers_install_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp_path, 0o440)

        copy = _sudo_run(["cp", tmp_path, fragment_path], password)
        if copy.returncode != 0:
            return False, (
                f"cp to {fragment_path} failed: rc={copy.returncode} "
                f"stderr={copy.stderr.strip()[:200]}"
            )
        chmod = _sudo_run(["chmod", "0440", fragment_path], password)
        if chmod.returncode != 0:
            return False, (
                f"chmod 0440 {fragment_path} failed: "
                f"stderr={chmod.stderr.strip()[:200]}"
            )
        chown = _sudo_run(["chown", "root:root", fragment_path], password)
        if chown.returncode != 0:
            logger.warning(
                f"chown root:root on {fragment_path} failed (may be macOS root:wheel): "
                f"{chown.stderr.strip()[:200]}"
            )
            # macOS: try root:wheel instead
            if _is_macos():
                _sudo_run(["chown", "root:wheel", fragment_path], password)
        return True, f"sudoers fragment installed at {fragment_path}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def revoke_sudo(*, password: Optional[str] = None,
                fragment_path: str = SUDOERS_FRAGMENT_PATH) -> bool:
    """Remove the sudoers fragment. Idempotent."""
    if not Path(fragment_path).exists():
        return True
    result = _sudo_run(["rm", "-f", fragment_path], password)
    if result.returncode != 0:
        logger.error(
            f"Failed to remove {fragment_path}: "
            f"stderr={result.stderr.strip()[:200]}"
        )
        return False
    return True


# ─── Convenience ──────────────────────────────────────────────────────


@dataclass
class MultiuserConfig:
    """The validated outcome of multi-user provisioning."""
    enabled: bool
    orchestrator_user: str
    slot_users: list[str]
    cli_binaries: list[Path]
    sudoers_path: str

    @property
    def num_slots(self) -> int:
        return len(self.slot_users)


def slot_user(slot: int) -> str:
    """Return the system user name for a slot (1-indexed)."""
    if slot < 1:
        raise ValueError(f"Slot must be >= 1, got {slot}")
    return f"{SLOT_USER_PREFIX}{slot}"


def can_sudo_passwordless() -> bool:
    """Check if the current user can sudo without a password.

    Useful for the wizard to decide whether to prompt for a password upfront.
    """
    try:
        result = _run(["sudo", "-n", "true"], timeout=5)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


# ─── CLI test entry point ─────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="MyOldMachine multi-user OS layer")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("info", help="Show detected OS and capabilities")

    p_check = sub.add_parser("check-binary", help="Resolve a CLI binary path")
    p_check.add_argument("name", help="Binary name (e.g. claude)")

    p_build = sub.add_parser("build-sudoers", help="Print sudoers fragment for inspection")
    p_build.add_argument("--orchestrator", default=ORCHESTRATOR_USER)
    p_build.add_argument("--slots", type=int, default=4)
    p_build.add_argument("--binaries", nargs="+", required=True,
                         help="Absolute paths to CLI binaries")

    args = parser.parse_args()

    if args.cmd == "info":
        print(json.dumps({
            "os": platform.system(),
            "linux": _is_linux(),
            "macos": _is_macos(),
            "passwordless_sudo": can_sudo_passwordless(),
            "visudo": shutil.which("visudo"),
            "useradd": shutil.which("useradd"),
            "sysadminctl": shutil.which("sysadminctl"),
        }, indent=2))
    elif args.cmd == "check-binary":
        path = find_cli_binary(args.name)
        if path:
            print(f"{args.name} -> {path}")
            sys.exit(0)
        print(f"{args.name} not found in PATH")
        sys.exit(1)
    elif args.cmd == "build-sudoers":
        targets = [slot_user(i) for i in range(1, args.slots + 1)]
        bins = [Path(b) for b in args.binaries]
        text = build_sudoers_fragment(args.orchestrator, targets, bins)
        print(text)
        ok, msg = validate_sudoers_fragment(text)
        print(f"validation: {'OK' if ok else 'FAILED'}: {msg}", file=sys.stderr)
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()
        sys.exit(1)
