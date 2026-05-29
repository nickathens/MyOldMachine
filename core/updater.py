"""
Self-Update Mechanism.

Pulls latest code from git, reinstalls pip dependencies, and restarts the service.
Triggered via /update command in Telegram.
"""

import logging
import os
import platform
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_sudo_password():
    """Read sudo password. Delegates to install.sudo."""
    from install.sudo import get_sudo_password as _shared_get_sudo_password
    return _shared_get_sudo_password()


def _run(cmd: str, cwd: str = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=cwd, timeout=timeout
    )


def _is_dubious_ownership(stderr: str) -> bool:
    """Detect git's safe.directory rejection."""
    return "dubious ownership" in (stderr or "").lower()


def _add_safe_directory(path: str) -> bool:
    """Register path with git's global safe.directory list. Idempotent."""
    quoted = shlex.quote(path)
    check = _run(
        "git config --global --get-all safe.directory",
        timeout=10,
    )
    if check.returncode == 0 and path in check.stdout.splitlines():
        return True
    add = _run(
        f"git config --global --add safe.directory {quoted}",
        timeout=10,
    )
    return add.returncode == 0


def _run_git(cmd: str, cwd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """
    Run a git command, auto-recovering from 'dubious ownership' once by adding
    cwd to safe.directory. Avoids forcing the user into a Terminal session
    when we can fix the trust boundary ourselves.
    """
    result = _run(cmd, cwd=cwd, timeout=timeout)
    if result.returncode != 0 and _is_dubious_ownership(result.stderr):
        logger.warning(
            "git rejected %s as dubious — adding to safe.directory and retrying",
            cwd,
        )
        if _add_safe_directory(cwd):
            result = _run(cmd, cwd=cwd, timeout=timeout)
    return result


def get_current_version(bot_dir: Path) -> str:
    """Get the current git commit hash (short)."""
    result = _run_git("git rev-parse --short HEAD", cwd=str(bot_dir))
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def get_current_branch(bot_dir: Path) -> str:
    """Get the current git branch."""
    result = _run_git("git rev-parse --abbrev-ref HEAD", cwd=str(bot_dir))
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def check_for_updates(bot_dir: Path) -> tuple[bool, str]:
    """
    Check if there are updates available.
    Returns (has_updates, description).
    """
    result = _run_git("git fetch origin", cwd=str(bot_dir))
    if result.returncode != 0:
        return False, f"Failed to check: {result.stderr[:100]}"

    branch = get_current_branch(bot_dir)
    result = _run_git(f"git log HEAD..origin/{branch} --oneline", cwd=str(bot_dir))
    if result.returncode != 0:
        return False, "Could not compare with remote"

    commits = result.stdout.strip()
    if not commits:
        return False, "Already up to date"

    count = len(commits.splitlines())
    return True, f"{count} new commit(s) available:\n{commits}"


def pull_updates(bot_dir: Path) -> tuple[bool, str]:
    """
    Pull latest code from git.
    Returns (success, message).
    """
    current = get_current_version(bot_dir)
    result = _run_git("git pull --ff-only", cwd=str(bot_dir))

    if result.returncode != 0:
        return False, (
            f"Git pull --ff-only failed (local changes?): {result.stderr[:200]}\n"
            f"Fix manually: cd {bot_dir} && git stash && git pull"
        )

    new = get_current_version(bot_dir)

    # Reinstall pip deps in case requirements changed.
    # Use list-form subprocess to avoid shell-quoting issues on paths with spaces.
    venv_pip = bot_dir / ".venv" / "bin" / "pip"
    if venv_pip.exists():
        try:
            pip_result = subprocess.run(
                [str(venv_pip), "install", "--quiet", "-r",
                 str(bot_dir / "requirements.txt")],
                capture_output=True, text=True,
                cwd=str(bot_dir), timeout=300,
            )
            if pip_result.returncode != 0:
                logger.warning(f"pip install after update had issues: {pip_result.stderr[:200]}")
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"pip install after update failed to run: {e}")

    return True, f"Updated: {current} → {new}"


_SERVICE_TARGETS = {
    "bot": {
        "linux_service": "myoldmachine",
        "macos_plist_name": "com.myoldmachine.bot.plist",
    },
    "miniapp": {
        "linux_service": "myoldmachine-miniapp",
        "macos_plist_name": "com.myoldmachine.miniapp.plist",
    },
}


def restart_service(target: str = "bot") -> tuple[bool, str]:
    """
    Restart a MyOldMachine service.

    target: "bot" (default, the Telegram bot) or "miniapp" (the dashboard server).
    Returns (success, message). Success means the restart was *scheduled*; the
    caller's process may continue running for a few seconds before being killed
    when the launchd/systemd unload step runs.
    """
    spec = _SERVICE_TARGETS.get(target)
    if spec is None:
        return False, f"Unknown restart target: {target!r}"

    password = get_sudo_password()
    system = platform.system()

    if system == "Linux":
        import re as _re
        # Allow override via env (used by tests + custom installs). The bot
        # target additionally honors the legacy SERVICE_NAME env var.
        env_key = "MOM_MINIAPP_SERVICE" if target == "miniapp" else "SERVICE_NAME"
        service_name = os.environ.get(env_key, spec["linux_service"])
        if not _re.fullmatch(r"[a-zA-Z0-9_@.-]+", service_name):
            return False, f"Invalid service name: {service_name!r}"
        cmd = ["sudo", "-S", "systemctl", "restart", service_name] if password else ["sudo", "systemctl", "restart", service_name]
        stdin_data = (password + "\n") if password else None
        result = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "Service restarting..."
        return False, f"Restart failed: {result.stderr[:200]}"

    elif system == "Darwin":
        plist_name = spec["macos_plist_name"]
        daemon_plist = Path("/Library/LaunchDaemons") / plist_name
        agent_plist = Path.home() / "Library" / "LaunchAgents" / plist_name

        if daemon_plist.exists():
            import tempfile
            sudo_pass_file = Path.home() / ".sudo_pass"
            restart_script = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', delete=False, prefix='mom_restart_'
            )
            if sudo_pass_file.exists():
                sudo_cmd = f'cat "{sudo_pass_file}" | sudo -S'
            else:
                sudo_cmd = 'sudo -n'
            restart_script.write(
                f'#!/bin/bash\n'
                f'sleep 3\n'
                f'{sudo_cmd} launchctl unload "{daemon_plist}" 2>/dev/null\n'
                f'sleep 1\n'
                f'{sudo_cmd} launchctl load -w "{daemon_plist}"\n'
                f'rm -f "{restart_script.name}"\n'
            )
            restart_script.close()
            os.chmod(restart_script.name, 0o700)
            subprocess.Popen(
                [restart_script.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "Service restarting (LaunchDaemon)..."

        if agent_plist.exists():
            import tempfile
            restart_script = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', delete=False, prefix='mom_restart_'
            )
            restart_script.write(
                f'#!/bin/bash\n'
                f'sleep 3\n'
                f'launchctl unload "{agent_plist}" 2>/dev/null\n'
                f'sleep 1\n'
                f'launchctl load -w "{agent_plist}"\n'
                f'rm -f "{restart_script.name}"\n'
            )
            restart_script.close()
            os.chmod(restart_script.name, 0o700)
            subprocess.Popen(
                [restart_script.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "Service restarting (LaunchAgent)..."

        return False, f"No LaunchDaemon or LaunchAgent plist found for {target}"

    return False, f"Unsupported OS: {system}"


def full_update(bot_dir: Path) -> str:
    """
    Update cycle: pull code + install deps. Does NOT restart automatically.
    The user must send /restart to apply — this prevents killing the bot mid-response.
    """
    lines = []

    # Check
    has_updates, check_msg = check_for_updates(bot_dir)
    if not has_updates:
        return check_msg

    lines.append(check_msg)

    # Pull
    success, pull_msg = pull_updates(bot_dir)
    lines.append(pull_msg)
    if not success:
        return "\n".join(lines)

    lines.append("")
    lines.append("Code updated. Send /restart to apply the changes.")

    return "\n".join(lines)
