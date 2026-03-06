#!/usr/bin/env python3
"""
MyOldMachine Service Installer — Register as system service.

Creates and enables a systemd unit (Linux) or launchd plist (macOS)
so the bot starts on boot and restarts on crash.
"""

import argparse
import getpass
import subprocess
import sys
from pathlib import Path

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


def sudo_run(cmd, password=None):
    if password:
        full_cmd = f"echo '{password}' | sudo -S {cmd}"
    else:
        full_cmd = f"sudo {cmd}"
    return subprocess.run(
        full_cmd, shell=True,
        capture_output=True, text=True, timeout=30
    )


def setup_linux_service(repo_dir: Path):
    """Create and enable systemd service."""
    password = get_sudo_password()
    username = getpass.getuser()
    venv_python = repo_dir / ".venv" / "bin" / "python"

    # Read template
    template_path = repo_dir / "install" / "templates" / "myoldmachine.service"
    if not template_path.exists():
        error(f"Service template not found: {template_path}")
        sys.exit(1)

    content = template_path.read_text()
    content = content.replace("{{USER}}", username)
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))

    # Ensure log directory exists
    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # Write service file
    service_path = "/etc/systemd/system/myoldmachine.service"
    tmp_path = Path("/tmp/myoldmachine.service")
    tmp_path.write_text(content)
    sudo_run(f"cp /tmp/myoldmachine.service {service_path}", password)
    tmp_path.unlink()

    # Enable and start
    info("Enabling systemd service...")
    sudo_run("systemctl daemon-reload", password)
    sudo_run("systemctl enable myoldmachine", password)
    sudo_run("systemctl start myoldmachine", password)

    # Verify
    result = sudo_run("systemctl is-active myoldmachine", password)
    if "active" in result.stdout:
        ok("Service is running")
    else:
        warn("Service may not have started. Check: sudo systemctl status myoldmachine")

    ok(f"Systemd service installed at {service_path}")


def setup_macos_service(repo_dir: Path):
    """Create and load launchd plist."""
    username = getpass.getuser()
    venv_python = repo_dir / ".venv" / "bin" / "python"

    # Read template
    template_path = repo_dir / "install" / "templates" / "com.myoldmachine.bot.plist"
    if not template_path.exists():
        error(f"Plist template not found: {template_path}")
        sys.exit(1)

    content = template_path.read_text()
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{BOT_PY}}", str(repo_dir / "bot.py"))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))
    content = content.replace("{{ENV_FILE}}", str(repo_dir / ".env"))

    # Ensure log directory exists
    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # Write plist
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.myoldmachine.bot.plist"
    plist_path.write_text(content)

    # Unload if already loaded
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True, timeout=10
    )

    # Load
    info("Loading launchd service...")
    result = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        warn(f"launchctl load warning: {result.stderr}")

    ok(f"LaunchAgent installed at {plist_path}")
    ok("Service will start on boot and restart on crash")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, required=True)
    parser.add_argument("--os", type=str, choices=["linux", "macos"], required=True)
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)
    print(f"\n{BOLD}=== Service Setup ==={NC}\n")

    if args.os == "linux":
        setup_linux_service(repo_dir)
    else:
        setup_macos_service(repo_dir)


if __name__ == "__main__":
    main()
