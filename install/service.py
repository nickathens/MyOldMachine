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


def setup_linux_service(repo_dir: Path) -> bool:
    """Create and enable systemd service. Returns True on success."""
    password = get_sudo_password()
    username = getpass.getuser()
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


def setup_macos_service(repo_dir: Path, os_info=None) -> bool:
    """Create and load launchd plist — version-aware. Returns True on success."""
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

    # Ensure log directory exists
    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # Write plist
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.myoldmachine.bot.plist"

    try:
        plist_path.write_text(content)
    except Exception as e:
        error(f"Failed to write plist: {e}")
        return False

    # Unload if already loaded
    try:
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

    # Load the service
    info("Loading launchd service...")
    try:
        result = subprocess.run(
            ["launchctl", "load", "-w", str(plist_path)],
            capture_output=True, text=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        warn("launchctl load timed out")
        result = type("R", (), {"returncode": 1, "stderr": "Timed out"})()
    except Exception as e:
        warn(f"launchctl load failed: {e}")
        result = type("R", (), {"returncode": 1, "stderr": str(e)})()

    if result.returncode != 0:
        # On Ventura+ try modern bootstrap syntax
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

    ok(f"LaunchAgent installed at {plist_path}")
    ok("Service will start on boot and restart on crash")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, required=True)
    parser.add_argument("--os", type=str, choices=["linux", "macos"],
                        help="Override OS detection (optional)")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)

    os_info = detect_os()
    os_type = args.os if args.os else os_info.os_type

    print(f"\n{BOLD}=== Service Setup ==={NC}\n")
    info(f"Setting up service for {os_info.display_name}")

    success = False
    if os_type == "linux":
        success = setup_linux_service(repo_dir)
    elif os_type == "macos":
        success = setup_macos_service(repo_dir, os_info)
    else:
        error(f"Unsupported OS: {os_type}")

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
