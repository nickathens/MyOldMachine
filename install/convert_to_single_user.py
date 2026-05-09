#!/usr/bin/env python3
"""
MyOldMachine: convert a multi-user install to single-user.

One-shot conversion script invoked by `./install.sh --convert-multiuser-to-single`.

What it does, in order:
  1. Reads .env. Aborts if the install is already single-user.
  2. Confirms with the user before any destructive action.
  3. Stops the running service (LaunchDaemon on macOS / systemd on Linux).
  4. Removes the system service registration (/Library/LaunchDaemons/...
     plist on macOS, /etc/systemd/system/... unit on Linux).
  5. Revokes the sudoers fragment (/etc/sudoers.d/myoldmachine).
  6. Deletes the slot system users (mom_user1..N) and the orchestrator
     (mom_orchestrator). On macOS, removes the matching role groups.
  7. Chowns data/ recursively back to the install user so the bot can
     read its own files in single-user mode.
  8. Removes data/orchestrator/ since the new single-user runtime never
     reads it. .env and per-user data files (data/users/...) are preserved.
  9. Rewrites .env: drops MULTIUSER_* keys, sets MULTIUSER_ENABLED=0.
 10. Removes "multiuser_v2" from the install checkpoint file so a
     subsequent ./install.sh resume does not try to re-provision.
 11. Re-registers the service in single-user mode (LaunchAgent on macOS,
     systemd unit owned by the install user on Linux).
 12. Starts the service.

The conversion preserves .env, per-user conversation/attachment data, and
memory state. The slot directory structure (data/users/userN/) is NOT
moved — the bot in single-user mode writes to data/users/<telegram_id>/
directly, so any pre-existing conversation history under data/users/userN/
will be orphaned. A note is printed at the end if the script detects
slot directories that still hold meaningful data.

Idempotent: re-running on an already-converted install is a no-op past
the early "already single-user" check.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Ensure the repo root is importable for `from install import ...`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from install import multiuser as _mu  # noqa: E402

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def _info(msg: str) -> None:
    print(f"{BLUE}[INFO]{NC} {msg}")


def _ok(msg: str) -> None:
    print(f"{GREEN}[OK]{NC} {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def _error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}", file=sys.stderr)


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


# ─────────────────────────────────────────────────────────
# .env helpers
# ─────────────────────────────────────────────────────────


def read_env(repo_dir: Path) -> dict[str, str]:
    """Parse .env into a dict. Missing file yields an empty dict.

    Order is not preserved — we only need values for inspection. Writeback
    uses a different code path that strips multi-user keys and is order-
    insensitive (since .env has no semantic ordering requirement).
    """
    env_file = repo_dir / ".env"
    if not env_file.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


_MULTIUSER_KEYS_TO_DROP = (
    "MULTIUSER_ENABLED",
    "MULTIUSER_NUM_SLOTS",
    "MULTIUSER_ORCHESTRATOR_USER",
    "QUEUE_MODE",
    "CONCURRENT_REQUESTS",
    # The local Telegram Bot API server was registered to run as
    # mom_orchestrator. Its launch unit is removed by stop_service_*; here
    # we drop the env vars so the bot does not try to talk to a now-dead
    # http://localhost:8081 endpoint. The user can opt back in via
    # ./install.sh's optional-features prompt — `is_configured` will see
    # that telegram_local_api_enabled is unset.
    "TELEGRAM_API_BASE",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
)


def rewrite_env_to_single_user(repo_dir: Path) -> bool:
    """Strip multi-user keys from .env and set MULTIUSER_ENABLED=0.

    Atomic write via tempfile + os.replace. Preserves all other keys and
    blank/comment lines. Returns True on success.
    """
    env_file = repo_dir / ".env"
    if not env_file.exists():
        _warn(f".env not found at {env_file}, nothing to rewrite")
        return True
    new_lines: list[str] = []
    saw_enabled = False
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.partition("=")[0].strip()
            if key == "MULTIUSER_ENABLED":
                new_lines.append("MULTIUSER_ENABLED=0")
                saw_enabled = True
                continue
            if key in _MULTIUSER_KEYS_TO_DROP:
                # Skip — these are multi-user-only.
                continue
        new_lines.append(line)
    if not saw_enabled:
        new_lines.append("MULTIUSER_ENABLED=0")

    tmp = env_file.with_suffix(env_file.suffix + ".tmp")
    try:
        tmp.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
        os.replace(tmp, env_file)
    except OSError as e:
        _error(f"Failed to rewrite {env_file}: {e}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    _ok(f"Rewrote {env_file} for single-user mode")
    return True


# ─────────────────────────────────────────────────────────
# Slot detection / state probe
# ─────────────────────────────────────────────────────────


def detect_slot_users(repo_dir: Path, env: dict[str, str]) -> list[str]:
    """Find slot users (mom_user1..N) configured for this install.

    Looks at:
      - MULTIUSER_NUM_SLOTS in .env (authoritative)
      - data/orchestrator/users.json (more accurate after /adduser)
      - existing system users matching mom_userN (worst-case fallback)

    Returns the union, deduplicated. We err on the side of finding more
    slots so deletion catches everything.
    """
    found: set[str] = set()

    # From .env
    try:
        n = int(env.get("MULTIUSER_NUM_SLOTS", "0") or "0")
    except ValueError:
        n = 0
    for slot in range(1, max(n, 0) + 1):
        found.add(_mu.slot_user(slot))

    # From orchestrator users.json
    users_json = repo_dir / "data" / "orchestrator" / "users.json"
    if users_json.exists():
        try:
            text = users_json.read_text(encoding="utf-8")
            parsed = json.loads(text) if text.strip() else {}
            num = int(parsed.get("num_slots", 0) or 0)
            for slot in range(1, max(num, 0) + 1):
                found.add(_mu.slot_user(slot))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # System users beyond the configured count (defensive — captures stray
    # accounts left by a prior install with a higher slot count).
    for slot in range(1, 9):  # MOM cap is 8
        name = _mu.slot_user(slot)
        if _mu.system_user_exists(name):
            found.add(name)

    return sorted(found)


# ─────────────────────────────────────────────────────────
# Service control
# ─────────────────────────────────────────────────────────


def _run(cmd: list[str], *, timeout: int = 60,
         input_data: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, input=input_data,
    )


def _sudo_run(cmd: list[str], password: Optional[str] = None, *,
              timeout: int = 60) -> subprocess.CompletedProcess:
    if password is not None:
        return _run(["sudo", "-S", "--"] + cmd, timeout=timeout,
                    input_data=password + "\n")
    return _run(["sudo", "-n", "--"] + cmd, timeout=timeout)


def stop_service_macos(password: Optional[str]) -> bool:
    """Stop and remove the multi-user LaunchDaemon. Idempotent.

    Also stops + removes the telegram-bot-api LaunchDaemon, which was
    registered with UserName=mom_orchestrator (or another slot user) and
    would crash-loop with EX_CONFIG (78) once that user is deleted later
    in this conversion. The user can opt back in to a fresh single-user
    Bot API later via ./install.sh.
    """
    daemon_path = "/Library/LaunchDaemons/com.myoldmachine.bot.plist"
    if Path(daemon_path).exists():
        _info(f"Unloading LaunchDaemon: {daemon_path}")
        _sudo_run(["launchctl", "bootout", "system/com.myoldmachine.bot"],
                  password, timeout=15)
        _sudo_run(["launchctl", "unload", daemon_path], password, timeout=15)
        result = _sudo_run(["rm", "-f", daemon_path], password)
        if result.returncode != 0:
            _warn(f"Could not remove {daemon_path}: {result.stderr.strip()[:200]}")
            return False
        _ok(f"Removed {daemon_path}")

    tba_path = "/Library/LaunchDaemons/com.telegram-bot-api.plist"
    if Path(tba_path).exists():
        _info(f"Unloading LaunchDaemon: {tba_path}")
        _sudo_run(["launchctl", "bootout", "system/com.telegram-bot-api"],
                  password, timeout=15)
        _sudo_run(["launchctl", "unload", tba_path], password, timeout=15)
        result = _sudo_run(["rm", "-f", tba_path], password)
        if result.returncode != 0:
            _warn(f"Could not remove {tba_path}: {result.stderr.strip()[:200]}")
            # Non-fatal: we keep going so the rest of the conversion runs.
        else:
            _ok(f"Removed {tba_path}")
    return True


def stop_service_linux(password: Optional[str]) -> bool:
    """Stop and remove the systemd unit. Idempotent.

    Also stops + disables + removes the telegram-bot-api unit, which was
    registered with User=mom_orchestrator and would fail to start once
    that user is deleted. The user can opt back in via ./install.sh.
    """
    unit_path = "/etc/systemd/system/myoldmachine.service"
    _info("Stopping systemd unit: myoldmachine")
    _sudo_run(["systemctl", "stop", "myoldmachine"], password, timeout=30)
    _sudo_run(["systemctl", "disable", "myoldmachine"], password, timeout=30)
    if Path(unit_path).exists():
        result = _sudo_run(["rm", "-f", unit_path], password)
        if result.returncode != 0:
            _warn(f"Could not remove {unit_path}: {result.stderr.strip()[:200]}")
            return False
        _sudo_run(["systemctl", "daemon-reload"], password, timeout=30)
        _ok(f"Removed {unit_path}")

    tba_unit = "/etc/systemd/system/telegram-bot-api.service"
    if Path(tba_unit).exists():
        _info("Stopping systemd unit: telegram-bot-api")
        _sudo_run(["systemctl", "stop", "telegram-bot-api"], password, timeout=30)
        _sudo_run(["systemctl", "disable", "telegram-bot-api"], password, timeout=30)
        result = _sudo_run(["rm", "-f", tba_unit], password)
        if result.returncode != 0:
            _warn(f"Could not remove {tba_unit}: {result.stderr.strip()[:200]}")
            # Non-fatal: keep going.
        else:
            _sudo_run(["systemctl", "daemon-reload"], password, timeout=30)
            _ok(f"Removed {tba_unit}")
    return True


def stop_service(password: Optional[str]) -> bool:
    if _is_macos():
        return stop_service_macos(password)
    if _is_linux():
        return stop_service_linux(password)
    _warn(f"Unknown OS {platform.system()!r} — skipping service stop")
    return True


def reregister_single_user_service(repo_dir: Path) -> bool:
    """Run install/service.py without --orchestrator-user so it lays down
    a single-user LaunchAgent / systemd unit owned by the install user."""
    venv_python = repo_dir / ".venv" / "bin" / "python"
    if not venv_python.exists():
        _error(f"venv python missing: {venv_python}")
        return False
    cmd = [
        str(venv_python),
        str(repo_dir / "install" / "service.py"),
        "--repo-dir", str(repo_dir),
    ]
    _info("Re-registering service in single-user mode...")
    try:
        result = subprocess.run(cmd, timeout=120)
    except subprocess.TimeoutExpired:
        _error("service.py timed out")
        return False
    if result.returncode != 0:
        _warn("service.py exited non-zero. The bot may not start automatically.")
        _warn(f"Run manually: {' '.join(cmd)}")
        return False
    return True


# ─────────────────────────────────────────────────────────
# User / sudoers cleanup
# ─────────────────────────────────────────────────────────


def revoke_orchestrator_sudo(password: Optional[str]) -> bool:
    """Remove the sudoers fragment that scoped the orchestrator's
    privileged dispatch."""
    return _mu.revoke_sudo(password=password)


def delete_slot_and_orchestrator_users(slot_users: list[str],
                                       password: Optional[str]) -> tuple[int, list[str]]:
    """Delete each slot user, then mom_orchestrator. Returns (deleted_count, errors).

    Group records (macOS) and matching primary groups (Linux) are removed
    by delete_system_user as part of the user delete on each platform.
    """
    deleted = 0
    errors: list[str] = []
    for name in slot_users:
        if not _mu.system_user_exists(name):
            continue
        if _mu.delete_system_user(name, password=password):
            _ok(f"Deleted slot user: {name}")
            deleted += 1
        else:
            errors.append(name)
            _warn(f"Could not delete: {name}")

    orch = _mu.ORCHESTRATOR_USER
    if _mu.system_user_exists(orch):
        if _mu.delete_system_user(orch, password=password):
            _ok(f"Deleted orchestrator user: {orch}")
            deleted += 1
        else:
            errors.append(orch)
            _warn(f"Could not delete: {orch}")
    return deleted, errors


# ─────────────────────────────────────────────────────────
# Filesystem cleanup
# ─────────────────────────────────────────────────────────


def chown_data_back_to_install_user(repo_dir: Path,
                                    install_user: str,
                                    password: Optional[str]) -> bool:
    """Recursive chown of data/ to the install user.

    Without this step, every file under data/ owned by mom_orchestrator or
    mom_userN remains unreadable by the install user once the slot system
    accounts no longer exist (their UIDs become numeric orphans). Also
    chowns .env (which the wizard sets to install_user:mom_orchestrator
    mode 0640 in multi-user mode) back to install_user.
    """
    data_dir = repo_dir / "data"
    env_file = repo_dir / ".env"

    targets: list[Path] = []
    if data_dir.exists():
        targets.append(data_dir)
    if env_file.exists():
        targets.append(env_file)
    if not targets:
        return True

    # Linux chown accepts "user:" as shorthand for "user:user-primary-group",
    # macOS chown does too. Some older systems are stricter; if the first
    # form fails we resolve the install user's primary group explicitly.
    primary_group: Optional[str] = None

    def _resolve_primary_group() -> str:
        nonlocal primary_group
        if primary_group is not None:
            return primary_group
        try:
            import pwd as _pwd
            import grp as _grp
            primary_group = _grp.getgrgid(_pwd.getpwnam(install_user).pw_gid).gr_name
        except Exception:
            primary_group = install_user
        return primary_group

    ok_all = True
    for target in targets:
        _info(f"Chowning {target} back to {install_user}...")
        cmd = ["chown", "-R", f"{install_user}:", str(target)]
        result = _sudo_run(cmd, password, timeout=300)
        if result.returncode != 0:
            grpname = _resolve_primary_group()
            cmd2 = ["chown", "-R", f"{install_user}:{grpname}", str(target)]
            result = _sudo_run(cmd2, password, timeout=300)
            if result.returncode != 0:
                _warn(f"chown of {target} failed: {result.stderr.strip()[:200]}")
                ok_all = False
                continue
        _ok(f"Ownership of {target} restored to {install_user}")

    # Tighten .env back to mode 0600. The wizard set it to 0640 in
    # multi-user mode so the orchestrator group could read it; that group
    # is gone now, so 0640 leaks bytes to "other" via the (orphan) group.
    if env_file.exists():
        _sudo_run(["chmod", "600", str(env_file)], password, timeout=30)

    return ok_all


def remove_orchestrator_dir(repo_dir: Path, password: Optional[str]) -> bool:
    """Remove data/orchestrator/. Idempotent."""
    target = repo_dir / "data" / "orchestrator"
    if not target.exists():
        return True
    _info(f"Removing {target}")
    # Sudo for safety — this dir is mode 0700 owned by the now-deleted
    # mom_orchestrator user. After the chown step it might already be
    # owned by the install user, but a stale orchestrator-owned subdir
    # is also possible if the chown skipped a path (e.g. socket file).
    result = _sudo_run(["rm", "-rf", str(target)], password, timeout=120)
    if result.returncode != 0:
        _warn(f"Could not remove {target}: {result.stderr.strip()[:200]}")
        return False
    _ok(f"Removed {target}")
    return True


def relax_slot_dir_perms(repo_dir: Path, password: Optional[str]) -> None:
    """Reset slot dir mode from 02770 → 0755 so the install user can
    traverse them after the orchestrator group is gone."""
    users_root = repo_dir / "data" / "users"
    if not users_root.exists():
        return
    for slot in range(1, 9):
        slot_dir = users_root / f"user{slot}"
        if not slot_dir.exists():
            continue
        _sudo_run(["chmod", "-R", "u+rwX,go+rX", str(slot_dir)],
                  password, timeout=60)


# ─────────────────────────────────────────────────────────
# Checkpoint cleanup
# ─────────────────────────────────────────────────────────


def remove_multiuser_checkpoint() -> bool:
    """Drop the multiuser_v2 checkpoint so a future ./install.sh does
    not skip multi-user provisioning thinking it's still done."""
    ckpt_file = Path(os.environ.get(
        "MYOLDMACHINE_CHECKPOINT_FILE",
        str(Path.home() / ".myoldmachine_install_checkpoints"),
    ))
    if not ckpt_file.exists():
        return True
    try:
        kept = []
        for line in ckpt_file.read_text(encoding="utf-8").splitlines():
            if line.strip() in ("multiuser_v2", "multiuser"):
                continue
            kept.append(line)
        ckpt_file.write_text("\n".join(kept).rstrip() + ("\n" if kept else ""),
                             encoding="utf-8")
        return True
    except OSError as e:
        _warn(f"Could not edit checkpoint file: {e}")
        return False


# ─────────────────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────────────────


def _resolve_install_user() -> str:
    """Return the human user who owns this repo / runs `./install.sh`.

    On a multi-user install the script may be invoked as the install user
    directly or via sudo. SUDO_USER, when set, is the unsudo'd identity.
    """
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if sudo_user and sudo_user != "root":
        return sudo_user
    return getpass.getuser()


def _read_sudo_password() -> Optional[str]:
    """Try to load the saved sudo password from ~/.sudo_pass.

    The wizard stores it there at install time (mode 0600). If the file
    is missing or unreadable we return None — _sudo_run will then attempt
    -n (no prompt) which works on systems with NOPASSWD or recent sudo
    auth cache. If neither, the caller will see an error and can re-run
    after `sudo -v`.
    """
    sudo_file = Path.home() / ".sudo_pass"
    if not sudo_file.exists():
        return None
    try:
        return sudo_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _confirm(prompt: str, default: str = "n") -> bool:
    """y/N prompt. Default to 'n' for destructive operations."""
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        ans = default
    return ans in ("y", "yes")


def convert(repo_dir: Path, *, force: bool = False) -> int:
    """Run the full conversion. Returns a process exit code.

    `force=True` skips the interactive confirmation. Used by tests; not
    exposed via install.sh today (we always want the user to confirm).
    """
    if not repo_dir.is_dir():
        _error(f"Repo dir does not exist: {repo_dir}")
        return 2

    env = read_env(repo_dir)

    if env.get("MULTIUSER_ENABLED", "0") != "1":
        _ok("This install is already in single-user mode. Nothing to do.")
        return 0

    install_user = _resolve_install_user()
    sudo_pass = _read_sudo_password()
    slot_users = detect_slot_users(repo_dir, env)

    print()
    print(f"{BOLD}This will convert the multi-user install at {repo_dir}{NC}")
    print(f"{BOLD}back to single-user mode.{NC}")
    print()
    print(f"  Install user:      {install_user}")
    print(f"  Slot users to delete: {', '.join(slot_users) if slot_users else '(none detected)'}")
    print(f"  Orchestrator user: {_mu.ORCHESTRATOR_USER}")
    print(f"  Sudoers fragment:  {_mu.SUDOERS_FRAGMENT_PATH}")
    print()
    print("  Preserved: .env, data/users/<telegram_id>/, data/memory/,")
    print("             data/scheduler/, data/identities/, data/logs/.")
    print()
    print(f"  {YELLOW}Slot directories (data/users/userN/) are NOT migrated.{NC}")
    print(f"  {YELLOW}If they hold ongoing conversation history, you can move{NC}")
    print(f"  {YELLOW}files manually after the conversion completes.{NC}")
    print()

    if not force and not _confirm("Proceed? [y/N]: ", default="n"):
        _info("Aborted. No changes made.")
        return 1

    # 1. Stop the running service.
    stop_service(sudo_pass)

    # 2. Revoke sudoers fragment FIRST so the orchestrator can no longer
    # spawn slot CLIs while we tear down.
    if not revoke_orchestrator_sudo(sudo_pass):
        _warn("Could not revoke sudoers fragment. Proceeding anyway.")

    # 3. Delete slot + orchestrator users (and their groups on the platforms
    # where delete_system_user handles that).
    deleted, user_errors = delete_slot_and_orchestrator_users(slot_users, sudo_pass)
    if user_errors:
        _warn(f"User deletion partial: {len(user_errors)} failed: {', '.join(user_errors)}")

    # 4. Restore data/ ownership BEFORE removing data/orchestrator/, so the
    # install user owns whatever is left if the rm partially fails.
    if not chown_data_back_to_install_user(repo_dir, install_user, sudo_pass):
        _warn("Could not chown data/ back. Bot may fail to read files.")

    # 5. Remove the orchestrator's data subtree.
    remove_orchestrator_dir(repo_dir, sudo_pass)

    # 6. Relax slot dir perms so the bot in single-user mode can read them.
    relax_slot_dir_perms(repo_dir, sudo_pass)

    # 7. Rewrite .env to drop multi-user keys.
    if not rewrite_env_to_single_user(repo_dir):
        _warn(".env rewrite failed. The bot may still see MULTIUSER_ENABLED=1.")

    # 8. Remove the multiuser checkpoint so a future ./install.sh resume
    # does not pick up stale state.
    remove_multiuser_checkpoint()

    # 9. Re-register service in single-user mode and start it.
    if not reregister_single_user_service(repo_dir):
        _warn("Service re-registration failed. Run install/service.py manually.")

    # 10. Final hint about orphaned slot data.
    leftover = []
    for slot in range(1, 9):
        slot_dir = repo_dir / "data" / "users" / f"user{slot}"
        if slot_dir.is_dir():
            try:
                if any(slot_dir.iterdir()):
                    leftover.append(str(slot_dir))
            except OSError:
                continue
    if leftover:
        print()
        _info("Slot directories still present. The bot in single-user mode")
        _info("writes to data/users/<telegram_id>/ instead. To migrate any")
        _info("conversation history, move files manually:")
        for d in leftover:
            print(f"    {d}")

    print()
    _ok("Conversion complete. The bot now runs in single-user mode.")
    print()
    print("  Verify the service:")
    if _is_macos():
        print("    launchctl list | grep myoldmachine")
        print(f"    tail -f {repo_dir}/data/logs/bot.log")
    else:
        print("    systemctl --user status myoldmachine 2>/dev/null || "
              "sudo systemctl status myoldmachine")
        print("    journalctl -u myoldmachine -f")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a multi-user MyOldMachine install to single-user.",
    )
    parser.add_argument(
        "--repo-dir", required=True, type=Path,
        help="Path to the MyOldMachine repository.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    args = parser.parse_args()

    return convert(args.repo_dir.resolve(), force=args.force)


if __name__ == "__main__":
    sys.exit(main())
