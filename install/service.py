#!/usr/bin/env python3
"""
MyOldMachine Service Installer — Register as system service.

Creates and enables a systemd unit (Linux) or launchd plist (macOS)
so the bot starts on boot and restarts on crash.

Uses OSInfo from os_detect.py for version-aware service setup.
"""

import argparse
import getpass
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from install.os_detect import detect as detect_os

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def info(msg):
    print(f"{BLUE}[SVC]{NC} {msg}")


def ok(msg):
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")


def get_sudo_password():
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text().strip()
    return None


def sudo_run(cmd, password=None, timeout=30):
    """Run a command with sudo, passing password safely via stdin."""
    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    try:
        return subprocess.run(
            full_cmd, shell=True,
            input=stdin_data,
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        warn(f"Command timed out: {cmd}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "Timed out"})()
    except Exception as e:
        warn(f"Command failed: {cmd}: {e}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()


def setup_linux_service(repo_dir: Path, orchestrator_user: str | None = None) -> bool:
    """Create and enable systemd service. Returns True on success.

    When orchestrator_user is set, the service runs as that user (multi-user
    mode). Otherwise it runs as the current user (single-user mode).
    """
    password = get_sudo_password()
    username = orchestrator_user if orchestrator_user else getpass.getuser()
    venv_python = repo_dir / ".venv" / "bin" / "python"

    if not venv_python.exists():
        error(f"Virtual environment not found at {venv_python}")
        warn("Run the installer again to create it")
        return False

    template_path = repo_dir / "install" / "templates" / "myoldmachine.service"
    if not template_path.exists():
        error(f"Service template not found: {template_path}")
        return False

    content = template_path.read_text()
    content = content.replace("{{USER}}", username)
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))

    # In multi-user mode the orchestrator user has its home at data/orchestrator
    # but no real shell. Set HOME explicitly so libraries that depend on it
    # (telethon session files, openai cache, etc.) write to the right place.
    if orchestrator_user:
        home_path = repo_dir / "data" / "orchestrator"
        # Inject HOME into Environment= line. The template has the literal
        # `Environment=PYTHONUNBUFFERED=1`; append HOME alongside it so we
        # don't need to rewrite the template structure.
        content = content.replace(
            "Environment=PYTHONUNBUFFERED=1",
            f"Environment=PYTHONUNBUFFERED=1\nEnvironment=HOME={home_path}",
        )

    # Ensure log directory exists
    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # Write service file via temp file + sudo cp
    service_path = "/etc/systemd/system/myoldmachine.service"
    import shlex
    fd, tmp_name = tempfile.mkstemp(suffix=".service", prefix="myoldmachine_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        result = sudo_run(f"cp {shlex.quote(tmp_name)} {service_path}", password)
        if result.returncode != 0:
            error(f"Failed to install service file: {result.stderr}")
            return False
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    # Enable and start
    info("Enabling systemd service...")
    sudo_run("systemctl daemon-reload", password)
    sudo_run("systemctl enable myoldmachine", password)
    result = sudo_run("systemctl start myoldmachine", password)

    if result.returncode != 0:
        warn(f"Service may not have started: {result.stderr[:200]}")
        warn("Check: sudo systemctl status myoldmachine")
    else:
        # Verify
        check = sudo_run("systemctl is-active myoldmachine", password)
        if "active" in check.stdout:
            ok("Service is running")
        else:
            warn("Service registered but may not be active yet")
            warn("Check: sudo systemctl status myoldmachine")

    ok(f"Systemd service installed at {service_path}")
    return True


def _setup_macos_launch_agent(repo_dir: Path, os_info=None) -> bool:
    """Install a per-user LaunchAgent (single-user mode). Returns True on success."""
    venv_python = repo_dir / ".venv" / "bin" / "python"

    if not venv_python.exists():
        error(f"Virtual environment not found at {venv_python}")
        warn("Run the installer again to create it")
        return False

    template_path = repo_dir / "install" / "templates" / "com.myoldmachine.bot.plist"
    if not template_path.exists():
        error(f"Plist template not found: {template_path}")
        return False

    content = template_path.read_text()
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{BOT_PY}}", str(repo_dir / "bot.py"))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))
    content = content.replace("{{ENV_FILE}}", str(repo_dir / ".env"))
    content = content.replace("{{VENV_BIN}}", str(repo_dir / ".venv" / "bin"))
    content = content.replace("{{HOME}}", str(Path.home()))

    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.myoldmachine.bot.plist"

    try:
        plist_path.write_text(content)
    except Exception as e:
        error(f"Failed to write plist: {e}")
        return False

    _launchctl_load(plist_path, os_info, system_wide=False)

    ok(f"LaunchAgent installed at {plist_path}")
    ok("Service will start on boot and restart on crash")
    return True


def _setup_macos_launch_daemon(repo_dir: Path, orchestrator_user: str,
                                os_info=None) -> bool:
    """Install a system-wide LaunchDaemon (multi-user mode). Returns True on success.

    The daemon runs as the orchestrator user and lives in /Library/LaunchDaemons/
    so it starts at boot regardless of which user is logged in (or if nobody is).
    Installation requires root (sudo).
    """
    password = get_sudo_password()
    venv_python = repo_dir / ".venv" / "bin" / "python"

    if not venv_python.exists():
        error(f"Virtual environment not found at {venv_python}")
        warn("Run the installer again to create it")
        return False

    template_path = repo_dir / "install" / "templates" / "com.myoldmachine.daemon.plist"
    if not template_path.exists():
        error(f"Daemon plist template not found: {template_path}")
        return False

    orchestrator_home = repo_dir / "data" / "orchestrator"

    content = template_path.read_text()
    content = content.replace("{{ORCHESTRATOR_USER}}", orchestrator_user)
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{BOT_PY}}", str(repo_dir / "bot.py"))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))
    content = content.replace("{{ENV_FILE}}", str(repo_dir / ".env"))
    content = content.replace("{{VENV_BIN}}", str(repo_dir / ".venv" / "bin"))
    content = content.replace("{{HOME}}", str(orchestrator_home))

    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # If a single-user LaunchAgent exists from a prior install, unload and
    # remove it. Both use the same label (com.myoldmachine.bot) and launchd
    # would try to load both, causing a conflict.
    old_agent = Path.home() / "Library" / "LaunchAgents" / "com.myoldmachine.bot.plist"
    if old_agent.exists():
        info("Removing old single-user LaunchAgent to avoid label conflict...")
        try:
            subprocess.run(
                ["launchctl", "unload", str(old_agent)],
                capture_output=True, timeout=10
            )
        except Exception:
            pass
        try:
            old_agent.unlink()
            ok("Old LaunchAgent removed")
        except OSError as e:
            warn(f"Could not remove old LaunchAgent at {old_agent}: {e}")

    daemon_path = "/Library/LaunchDaemons/com.myoldmachine.bot.plist"
    import shlex
    fd, tmp_name = tempfile.mkstemp(suffix=".plist", prefix="myoldmachine_daemon_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        result = sudo_run(f"cp {shlex.quote(tmp_name)} {daemon_path}", password)
        if result.returncode != 0:
            error(f"Failed to install daemon plist: {result.stderr}")
            return False
        sudo_run(f"chmod 644 {daemon_path}", password)
        sudo_run(f"chown root:wheel {daemon_path}", password)
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    _launchctl_load(Path(daemon_path), os_info, system_wide=True, password=password)

    ok(f"LaunchDaemon installed at {daemon_path}")
    ok(f"Service runs as {orchestrator_user}, starts at boot, restarts on crash")
    return True


def _launchctl_load(plist_path: Path, os_info=None, *,
                    system_wide: bool = False, password: str | None = None):
    """Load a plist via launchctl. Handles legacy load and modern bootstrap."""
    # Unload first if already loaded
    if system_wide:
        sudo_run(f"launchctl unload {plist_path}", password, timeout=10)
    else:
        try:
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True, timeout=10
            )
        except Exception:
            pass

    info("Loading launchd service...")
    if system_wide:
        if os_info and os_info._mac_version_gte(13):
            result = sudo_run(
                f"launchctl bootstrap system {plist_path}", password, timeout=10
            )
            if result.returncode == 0:
                ok("Service loaded via bootstrap (system domain)")
                return
            warn(f"launchctl bootstrap warning: {result.stderr.strip()[:200]}")

        result = sudo_run(f"launchctl load -w {plist_path}", password, timeout=10)
        if result.returncode != 0:
            warn(f"launchctl load warning: {result.stderr.strip()[:200]}")
        else:
            ok("Service loaded (system domain)")
    else:
        try:
            result = subprocess.run(
                ["launchctl", "load", "-w", str(plist_path)],
                capture_output=True, text=True, timeout=10
            )
        except subprocess.TimeoutExpired:
            warn("launchctl load timed out")
            return
        except Exception as e:
            warn(f"launchctl load failed: {e}")
            return

        if result.returncode != 0:
            if os_info and os_info._mac_version_gte(13):
                info("Trying modern launchctl bootstrap syntax...")
                try:
                    uid_result = subprocess.run(
                        ["id", "-u"], capture_output=True, text=True, timeout=5
                    )
                    uid = uid_result.stdout.strip()
                    result2 = subprocess.run(
                        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                        capture_output=True, text=True, timeout=10
                    )
                    if result2.returncode != 0:
                        warn(f"launchctl bootstrap warning: {result2.stderr}")
                    else:
                        ok("Service loaded via bootstrap")
                except Exception as e:
                    warn(f"Bootstrap attempt failed: {e}")
            else:
                warn(f"launchctl load warning: {result.stderr}")


def setup_macos_service(repo_dir: Path, os_info=None,
                        orchestrator_user: str | None = None) -> bool:
    """Create and load launchd plist (version-aware). Returns True on success.

    Single-user: installs a LaunchAgent in ~/Library/LaunchAgents/.
    Multi-user: installs a LaunchDaemon in /Library/LaunchDaemons/ that runs
    as the orchestrator user, independent of any login session.
    """
    if orchestrator_user:
        return _setup_macos_launch_daemon(repo_dir, orchestrator_user, os_info)
    return _setup_macos_launch_agent(repo_dir, os_info)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, required=True)
    parser.add_argument("--os", type=str, choices=["linux", "macos"],
                        help="Override OS detection (optional)")
    parser.add_argument("--orchestrator-user", type=str, default=None,
                        help="System user to run the bot as (multi-user mode). "
                             "If not given, runs as the current user.")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)

    os_info = detect_os()
    os_type = args.os if args.os else os_info.os_type

    print(f"\n{BOLD}=== Service Setup ==={NC}\n")
    if args.orchestrator_user:
        info(f"Setting up service for {os_info.display_name} (multi-user, "
             f"running as {args.orchestrator_user})")
    else:
        info(f"Setting up service for {os_info.display_name}")

    success = False
    if os_type == "linux":
        success = setup_linux_service(repo_dir, orchestrator_user=args.orchestrator_user)
    elif os_type == "macos":
        success = setup_macos_service(repo_dir, os_info,
                                      orchestrator_user=args.orchestrator_user)
    else:
        error(f"Unsupported OS: {os_type}")

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
