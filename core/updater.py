"""
Self-Update Mechanism.

Pulls latest code from git, reinstalls pip dependencies, and restarts the service.
Triggered via /update command in Telegram.
"""

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_sudo_password():
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text(encoding="utf-8").strip()
    return None


def _run(cmd: str, cwd: str = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=cwd, timeout=timeout
    )


def get_current_version(bot_dir: Path) -> str:
    """Get the current git commit hash (short)."""
    result = _run("git rev-parse --short HEAD", cwd=str(bot_dir))
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def get_current_branch(bot_dir: Path) -> str:
    """Get the current git branch."""
    result = _run("git rev-parse --abbrev-ref HEAD", cwd=str(bot_dir))
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def check_for_updates(bot_dir: Path) -> tuple[bool, str]:
    """
    Check if there are updates available.
    Returns (has_updates, description).
    """
    result = _run("git fetch origin", cwd=str(bot_dir))
    if result.returncode != 0:
        return False, f"Failed to check: {result.stderr[:100]}"

    branch = get_current_branch(bot_dir)
    result = _run(f"git log HEAD..origin/{branch} --oneline", cwd=str(bot_dir))
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
    result = _run("git pull --ff-only", cwd=str(bot_dir))

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


def restart_service() -> tuple[bool, str]:
    """
    Restart the bot service.
    Returns (success, message).
    """
    password = get_sudo_password()
    system = platform.system()

    if system == "Linux":
        import re as _re
        service_name = os.environ.get("SERVICE_NAME", "myoldmachine")
        if not _re.fullmatch(r"[a-zA-Z0-9_@.-]+", service_name):
            return False, f"Invalid SERVICE_NAME: {service_name!r}"
        cmd = ["sudo", "-S", "systemctl", "restart", service_name] if password else ["sudo", "systemctl", "restart", service_name]
        stdin_data = (password + "\n") if password else None
        result = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "Service restarting..."
        return False, f"Restart failed: {result.stderr[:200]}"

    elif system == "Darwin":
        daemon_plist = Path("/Library/LaunchDaemons/com.myoldmachine.bot.plist")
        agent_plist = Path.home() / "Library" / "LaunchAgents" / "com.myoldmachine.bot.plist"

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

        return False, "No LaunchDaemon or LaunchAgent plist found"

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
