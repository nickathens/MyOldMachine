"""
Self-Update Mechanism.

Pulls latest code from git, reinstalls pip dependencies, and restarts the service.
Triggered via /update command in Telegram.
"""

import logging
import platform
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_sudo_password():
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text().strip()
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

    # Reinstall pip deps in case requirements changed
    venv_pip = bot_dir / ".venv" / "bin" / "pip"
    if venv_pip.exists():
        pip_result = _run(
            f"{venv_pip} install --quiet -r {bot_dir / 'requirements.txt'}",
            cwd=str(bot_dir)
        )
        if pip_result.returncode != 0:
            logger.warning(f"pip install after update had issues: {pip_result.stderr[:200]}")

    return True, f"Updated: {current} → {new}"


def restart_service() -> tuple[bool, str]:
    """
    Restart the bot service.
    Returns (success, message).
    """
    password = get_sudo_password()
    system = platform.system()

    if system == "Linux":
        cmd = "sudo -S systemctl restart myoldmachine" if password else "sudo systemctl restart myoldmachine"
        stdin_data = (password + "\n") if password else None
        result = subprocess.run(cmd, shell=True, input=stdin_data, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "Service restarting..."
        return False, f"Restart failed: {result.stderr[:200]}"

    elif system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.myoldmachine.bot.plist"
        if plist.exists():
            # Try atomic restart via kickstart (macOS 10.10+)
            # kickstart -k kills and restarts in one operation — avoids the
            # unload/load race where unload kills us before load runs.
            uid_result = subprocess.run(
                ["id", "-u"], capture_output=True, text=True, timeout=5
            )
            uid = uid_result.stdout.strip()
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/com.myoldmachine.bot"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True, "Service restarting..."
            # Fallback: spawn a detached process that waits for us to die,
            # then reloads the plist.
            logger.info("kickstart failed, falling back to detached reload")
            subprocess.Popen(
                f'sleep 3 && launchctl load -w "{plist}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, timeout=10)
            return True, "Service restarting..."
        return False, "LaunchAgent plist not found"

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
