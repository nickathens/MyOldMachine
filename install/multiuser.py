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
SUDOERS_FRAGMENT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def _validate_sudoers_fragment_path(path: str) -> None:
    """Reject any path outside /etc/sudoers.d/ with a safe filename.

    Both grant_sudo and revoke_sudo run as root. A caller-supplied path
    like /etc/passwd or /etc/sudoers would let a buggy or compromised
    caller clobber/delete arbitrary system files. Pin the parent dir
    and the filename character set instead of trusting the input.
    """
    p = Path(path)
    if not p.is_absolute() or str(p) != os.path.normpath(str(p)):
        raise ValueError(f"sudoers fragment path must be a normalized absolute path: {path!r}")
    if str(p.parent) != "/etc/sudoers.d":
        raise ValueError(
            f"sudoers fragment must live in /etc/sudoers.d/, got parent {p.parent!r}"
        )
    if not SUDOERS_FRAGMENT_NAME_RE.fullmatch(p.name):
        raise ValueError(
            f"sudoers fragment name must match {SUDOERS_FRAGMENT_NAME_RE.pattern}, got {p.name!r}"
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


# ─── User / group existence ───────────────────────────────────────────


def system_user_exists(name: str) -> bool:
    """True if the named OS user exists on this machine."""
    _validate_username(name)
    try:
        result = _run(["id", "-u", name], timeout=10)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def system_group_exists(name: str) -> bool:
    """True if the named OS group exists on this machine."""
    _validate_username(name)  # group names share user-name char rules here
    try:
        if _is_macos():
            # `dscl . -read /Groups/<name>` exits 0 only if the record exists
            result = _run(["dscl", ".", "-read", f"/Groups/{name}"], timeout=10)
            return result.returncode == 0
        # Linux + others: getent works on /etc/group + nss
        result = _run(["getent", "group", name], timeout=10)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


# ─── macOS group helpers ──────────────────────────────────────────────


# macOS reserves UID/GID 0..200 for the system, and `sysadminctl -roleAccount`
# allocates user UIDs in 200..400. Pick role-group GIDs from 401..499 to stay
# clear of those bands and below the regular-user start at 500/501.
_MACOS_ROLE_GID_LO = 401
_MACOS_ROLE_GID_HI = 499


def _list_macos_used_gids() -> set[int]:
    """Return the set of GIDs currently allocated to /Groups via dscl."""
    try:
        result = _run(
            ["dscl", ".", "-list", "/Groups", "PrimaryGroupID"], timeout=10,
        )
    except subprocess.TimeoutExpired:
        return set()
    if result.returncode != 0:
        return set()
    used: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                used.add(int(parts[1]))
            except ValueError:
                continue
    return used


def _pick_free_macos_gid() -> Optional[int]:
    """Pick the lowest free GID in the role-account band, or None if full."""
    used = _list_macos_used_gids()
    for gid in range(_MACOS_ROLE_GID_LO, _MACOS_ROLE_GID_HI + 1):
        if gid not in used:
            return gid
    return None


def _macos_user_is_member(user: str, group: str) -> bool:
    """True if `user` is already a member of `group` on macOS."""
    try:
        result = _run(
            ["dseditgroup", "-o", "checkmember", "-m", user, group], timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _macos_add_user_to_group(user: str, group: str,
                             password: Optional[str] = None) -> bool:
    """Idempotently add `user` to `group` on macOS."""
    if _macos_user_is_member(user, group):
        return True
    result = _sudo_run(
        ["dseditgroup", "-o", "edit", "-a", user, "-t", "user", group],
        password, timeout=20,
    )
    if result.returncode != 0:
        logger.error(
            f"dseditgroup add {user} to {group} failed: "
            f"rc={result.returncode} stderr={result.stderr.strip()[:200]}"
        )
        return False
    return True


def _ensure_macos_group(name: str, password: Optional[str] = None) -> bool:
    """Create a macOS group named `name` with a free role-band GID. Idempotent."""
    if not _is_macos():
        return True
    _validate_username(name)
    if system_group_exists(name):
        return True
    gid = _pick_free_macos_gid()
    if gid is None:
        logger.error(
            f"No free GID in {_MACOS_ROLE_GID_LO}-{_MACOS_ROLE_GID_HI} "
            f"to create group {name}"
        )
        return False
    # The macOS group record requires several attributes; create them in
    # sequence. dscl rejects the whole operation if any single step fails,
    # so each is checked independently.
    steps = [
        ["dscl", ".", "-create", f"/Groups/{name}"],
        ["dscl", ".", "-create", f"/Groups/{name}", "PrimaryGroupID", str(gid)],
        ["dscl", ".", "-create", f"/Groups/{name}", "RealName", name],
        ["dscl", ".", "-create", f"/Groups/{name}", "Password", "*"],
    ]
    for cmd in steps:
        result = _sudo_run(cmd, password, timeout=20)
        if result.returncode != 0:
            logger.error(
                f"dscl group {name} step {' '.join(cmd[3:])!r} failed: "
                f"rc={result.returncode} stderr={result.stderr.strip()[:200]}"
            )
            return False
    return system_group_exists(name)


def _delete_macos_group(name: str, password: Optional[str] = None) -> bool:
    """Delete a macOS group. Idempotent."""
    if not _is_macos():
        return True
    _validate_username(name)
    if not system_group_exists(name):
        return True
    result = _sudo_run(
        ["dscl", ".", "-delete", f"/Groups/{name}"], password, timeout=20,
    )
    if result.returncode != 0:
        logger.error(
            f"dscl delete /Groups/{name} failed: "
            f"rc={result.returncode} stderr={result.stderr.strip()[:200]}"
        )
        return False
    return True


# ─── User creation ────────────────────────────────────────────────────


def create_system_user(
    name: str,
    *,
    password: Optional[str] = None,
    home_dir: Optional[Path] = None,
) -> bool:
    """Create a system user with no login shell. Idempotent.

    On macOS, also creates a matching group with the same name and adds the
    user to it — sysadminctl alone does not, so without this step a later
    `chown <name>:<name>` would fail with "illegal group name".

    Returns True if the user exists at end (whether we created it or it
    already existed). Returns False if creation failed.
    """
    _validate_username(name)

    if system_user_exists(name):
        logger.info(f"System user {name} already exists")
        # Still ensure the matching macOS group exists; older installs that
        # ran before the group fix landed are missing the group record.
        if _is_macos():
            if not _ensure_macos_group(name, password):
                return False
            if not _macos_add_user_to_group(name, name, password):
                return False
            # Migration path for installs that pre-date the NFSHomeDirectory
            # fix: idempotently re-set the home dir so re-running the wizard
            # repairs broken slots that have HOME=/var/empty.
            if home_dir is not None:
                dscl_cmd = [
                    "dscl", ".", "-create",
                    f"/Users/{name}", "NFSHomeDirectory", str(home_dir),
                ]
                dscl_result = _sudo_run(dscl_cmd, password)
                if dscl_result.returncode != 0:
                    logger.error(
                        f"Failed to update NFSHomeDirectory for {name}: "
                        f"rc={dscl_result.returncode} "
                        f"stderr={dscl_result.stderr.strip()[:200]}"
                    )
                    return False
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
        # useradd -r creates a matching group implicitly on every distro
        # we support, so no extra group step is needed here.
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
        if not system_user_exists(name):
            return False
        # sysadminctl does NOT create a matching group — do it ourselves so
        # `chown user:user` and setgid-group operations work later.
        if not _ensure_macos_group(name, password):
            return False
        if not _macos_add_user_to_group(name, name, password):
            return False
        # sysadminctl -roleAccount sets NFSHomeDirectory to /var/empty (a
        # read-only system path). When the bot dispatches `sudo -n -u <name>
        # claude ...`, sudo resolves HOME from passwd → /var/empty, and claude
        # then looks for credentials at /var/empty/.claude/ (which can never
        # exist). Point HOME at the requested home_dir so the slot user has a
        # writable home matching the data layout the wizard provisions.
        if home_dir is not None:
            dscl_cmd = [
                "dscl", ".", "-create",
                f"/Users/{name}", "NFSHomeDirectory", str(home_dir),
            ]
            dscl_result = _sudo_run(dscl_cmd, password)
            if dscl_result.returncode != 0:
                logger.error(
                    f"Failed to set NFSHomeDirectory for {name}: "
                    f"rc={dscl_result.returncode} "
                    f"stderr={dscl_result.stderr.strip()[:200]}"
                )
                return False
        return True

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
        # Best-effort: remove the matching role group we created in
        # create_system_user. A failure here is logged but not fatal: the
        # user record is gone, the group is harmless if it remains.
        _delete_macos_group(name, password)
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


def mkdir_as_root(path: Path, *, password: Optional[str] = None) -> bool:
    """Create a directory and any missing parents via sudo. Idempotent.

    Use this when the parent directory is owned by another system user (e.g.
    after we've chowned it to mom_orchestrator) and the install-user process
    can no longer write into it. The caller is responsible for setting the
    final ownership and mode via set_owner / set_perms — `mkdir -p` runs as
    root, so the directory is briefly root-owned with the umask-derived
    mode until those calls land.
    """
    cmd = ["mkdir", "-p", str(path)]
    result = _sudo_run(cmd, password)
    if result.returncode != 0:
        logger.error(
            f"mkdir -p {path} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()[:200]}"
        )
        return False
    return True


def propagate_claude_credentials(
    install_user: str,
    slot_users_to_homes: dict[str, Path],
    *,
    password: Optional[str] = None,
) -> tuple[int, list[str]]:
    """Copy install user's Claude credentials to each slot user's home.

    The Claude CLI looks for ~/.claude/.credentials.json relative to HOME.
    In multi-user mode HOME resolves from passwd to the slot user's home,
    which by default is empty. Without this step, each slot would need its
    own `sudo -u <slot> claude auth login` (terminal access + browser flow
    per slot). Copying the install user's credentials makes the bot work
    out of the box.

    Args:
        install_user: Username of the user who ran `claude auth login`.
            Their ~/.claude/.credentials.json is the source.
        slot_users_to_homes: Mapping of slot username -> home dir Path. The
            home dir is the slot user's NFSHomeDirectory / passwd home, NOT
            the slot's data dir if those differ.
        password: Sudo password for elevated operations.

    Returns:
        (count, errors). count is the number of slots that received
        credentials. errors is a list of human-readable per-slot failure
        messages. Callers should treat a missing source as informational
        (the user may plan to use a non-Claude provider) and a non-empty
        errors list with count > 0 as partial success.

    Idempotent: safe to re-run; existing creds are overwritten with the
    install user's current copy.
    """
    try:
        install_home = Path(f"~{install_user}").expanduser()
    except RuntimeError:
        # Python 3.12+: Path.expanduser raises if the user doesn't resolve
        # in passwd/dscl. Treat as a missing source rather than crashing.
        return (0, [f"source missing: install user {install_user!r} not in passwd"])
    src_creds = install_home / ".claude" / ".credentials.json"
    if not src_creds.exists():
        return (0, [f"source missing: {src_creds}"])

    errors: list[str] = []
    count = 0
    for slot_name, slot_home in slot_users_to_homes.items():
        target_dir = slot_home / ".claude"
        target_creds = target_dir / ".credentials.json"

        if not mkdir_as_root(target_dir, password=password):
            errors.append(f"{slot_name}: mkdir {target_dir} failed")
            continue

        cp_cmd = ["cp", str(src_creds), str(target_creds)]
        cp_result = _sudo_run(cp_cmd, password)
        if cp_result.returncode != 0:
            errors.append(
                f"{slot_name}: cp {src_creds} -> {target_creds} failed: "
                f"{cp_result.stderr.strip()[:120]}"
            )
            continue

        if not set_owner(target_dir, slot_name, slot_name,
                         password=password, recursive=True):
            errors.append(f"{slot_name}: chown {target_dir} failed")
            continue
        if not set_perms(target_dir, 0o700, password=password):
            errors.append(f"{slot_name}: chmod 700 {target_dir} failed")
            continue
        if not set_perms(target_creds, 0o600, password=password):
            errors.append(f"{slot_name}: chmod 600 {target_creds} failed")
            continue

        count += 1

    return (count, errors)


# ─── Sudoers ──────────────────────────────────────────────────────────


def find_cli_binary(name: str) -> Optional[Path]:
    """Find a CLI binary, falling back through known install locations.

    Returns the absolute symlink path, NOT the resolved target. sudo's
    command-matching is literal: it compares the command path argv[0]
    against the sudoers entry as written. npm-installed CLIs (claude,
    codex) are typically symlinks under /usr/local/bin/ pointing to .js
    files in node_modules. We must NOT resolve the symlink, because the
    bot will invoke the binary via its PATH location and that's what
    sudo will see.

    Search order: PATH → ~/.local/bin (Anthropic native installer) →
    /opt/homebrew/bin → /usr/local/bin → ~/.npm-global/bin →
    ~/.bun/bin → ~/.nvm/versions/node/*/bin. Mirrors core/llm
    ._find_cli_binary so multi-user provisioning can locate the binary
    even when the install user's PATH does not include the install
    location (common on macOS where ~/.local/bin is not on PATH for
    non-login shells).
    """
    found = shutil.which(name)
    if found:
        path = Path(found).absolute()
        if path.exists():
            return path
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        home / ".npm-global" / "bin" / name,
        home / ".bun" / "bin" / name,
    ]
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        try:
            for version_dir in sorted(nvm_root.iterdir(), reverse=True):
                candidates.append(version_dir / "bin" / name)
        except OSError:
            pass
    for candidate in candidates:
        try:
            if candidate.exists() and not candidate.is_dir():
                return candidate.absolute()
        except OSError:
            continue
    return None


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
    try:
        _validate_sudoers_fragment_path(fragment_path)
    except ValueError as e:
        return False, str(e)
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
    try:
        _validate_sudoers_fragment_path(fragment_path)
    except ValueError as e:
        logger.error(f"Refusing to revoke sudoers at unsafe path: {e}")
        return False
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
    p_build.add_argument("--slots", type=int, default=8)
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
