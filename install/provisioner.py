#!/usr/bin/env python3
"""
MyOldMachine OS Provisioner — System-level setup.

Handles:
- Removing unnecessary packages (full takeover)
- Installing required dependencies
- System configuration (firewall, sleep, VNC, SSH)
- Disk cleanup

Supports --dry-run to preview all actions before executing.
"""

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

# Action log — records everything done for recovery/debugging
_action_log = []
_dry_run = False


def info(msg):
    print(f"{BLUE}[PROV]{NC} {msg}")


def ok(msg):
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")


def log_action(action, detail=""):
    """Record an action for the log file."""
    entry = {"time": datetime.now().isoformat(), "action": action, "detail": detail}
    _action_log.append(entry)


def save_action_log(repo_dir):
    """Save action log to file for debugging/recovery."""
    log_dir = Path(repo_dir) / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"provision_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_file.write_text(json.dumps(_action_log, indent=2) + "\n")
    ok(f"Action log saved to {log_file}")


def get_sudo_password():
    """Read sudo password from storage."""
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text().strip()
    return None


def sudo_run(cmd, password=None, check=False):
    """Run a command with sudo, passing password safely via stdin (not shell echo)."""
    if _dry_run:
        info(f"[DRY RUN] sudo: {cmd}")
        log_action("dry_run_sudo", cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    result = subprocess.run(
        full_cmd, shell=True,
        input=stdin_data,
        capture_output=True, text=True, timeout=600
    )
    log_action(f"sudo: {cmd}", f"rc={result.returncode}")
    if check and result.returncode != 0:
        error(f"Command failed: {cmd}")
        error(f"stderr: {result.stderr}")
    return result


def run(cmd, check=False):
    """Run a command without sudo."""
    if _dry_run:
        info(f"[DRY RUN] run: {cmd}")
        log_action("dry_run", cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    result = subprocess.run(
        cmd, shell=True,
        capture_output=True, text=True, timeout=600
    )
    if check and result.returncode != 0:
        error(f"Command failed: {cmd}")
    return result


def sudo_write_file(path, content, password=None):
    """Safely write a file as root by writing to a temp file first, then sudo cp."""
    if _dry_run:
        info(f"[DRY RUN] Would write {len(content)} bytes to {path}")
        log_action("dry_run_write", path)
        return

    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        f.write(content)
        tmp_path = f.name
    sudo_run(f"cp {tmp_path} {path}", password)
    os.unlink(tmp_path)
    log_action(f"write_file: {path}", f"{len(content)} bytes")


# --- Linux provisioning ---

LINUX_REMOVE_PACKAGES = [
    # Desktop environments
    "gnome-shell", "gnome-session", "gnome-desktop3-data", "gnome-control-center",
    "gdm3", "lightdm", "sddm",
    "ubuntu-desktop", "ubuntu-desktop-minimal",
    "kde-plasma-desktop", "plasma-desktop",
    "xfce4", "lxde", "cinnamon-desktop-environment",
    # Browsers
    "firefox", "firefox-esr", "chromium-browser", "chromium",
    # Office
    "libreoffice-core", "libreoffice-common",
    "thunderbird",
    # Media & entertainment
    "totem", "rhythmbox", "shotwell", "cheese",
    "gnome-music", "gnome-photos", "gnome-maps", "gnome-weather",
    "gnome-calendar", "gnome-contacts", "gnome-clocks",
    "aisleriot", "gnome-mines", "gnome-sudoku", "gnome-mahjongg",
    # Snap
    "snapd",
    # Other bloat
    "ubuntu-report", "popularity-contest",
    "gnome-software", "gnome-software-plugin-snap",
    "update-manager", "update-notifier",
    "yelp",
]

LINUX_INSTALL_PACKAGES = [
    "python3-pip", "python3-venv",
    "git", "curl", "wget", "jq",
    "ffmpeg", "sox",
    "htop", "tmux",
    "openssh-server",
    "ufw", "fail2ban",
    "unattended-upgrades",
]


def provision_linux_full(password):
    """Full takeover: strip desktop, install deps, configure system."""
    info("Starting full Linux provisioning...")

    # Step 1: Remove bloat
    info("Removing unnecessary packages...")
    # Check which packages are actually installed (always check even in dry-run)
    result = subprocess.run(
        "dpkg --get-selections 2>/dev/null", shell=True,
        capture_output=True, text=True, timeout=60
    )
    installed = set()
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "install":
                installed.add(parts[0].split(":")[0])  # strip arch suffix

    to_remove = [pkg for pkg in LINUX_REMOVE_PACKAGES if pkg in installed]
    if to_remove:
        pkgs = " ".join(to_remove)
        info(f"Removing {len(to_remove)} packages: {', '.join(to_remove[:10])}{'...' if len(to_remove) > 10 else ''}")
        log_action("remove_packages", ", ".join(to_remove))
        sudo_run(f"apt-get remove -y --purge {pkgs} 2>/dev/null", password)
        sudo_run("apt-get autoremove -y 2>/dev/null", password)
        ok(f"Removed {len(to_remove)} packages")
    else:
        ok("No bloat packages found to remove")

    # Clean up snap remnants
    if "snapd" in installed:
        sudo_run("rm -rf /snap /var/snap /var/lib/snapd 2>/dev/null", password)

    # Step 2: Install dependencies
    _install_linux_deps(password)

    # Step 3: Configure system
    _configure_linux(password)

    ok("Linux provisioning complete")


def provision_linux_soft(password):
    """Soft install: just install deps, skip removal."""
    info("Starting soft Linux provisioning (no removal)...")
    _install_linux_deps(password)
    _configure_linux(password)
    ok("Linux provisioning complete")


def _install_linux_deps(password):
    """Install required packages on Linux."""
    info("Updating package lists...")
    sudo_run("apt-get update -qq", password)

    info("Installing dependencies...")
    pkgs = " ".join(LINUX_INSTALL_PACKAGES)
    log_action("install_packages", pkgs)
    result = sudo_run(f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkgs}", password)
    if result.returncode != 0:
        warn(f"Some packages may have failed: {result.stderr[:200]}")
    else:
        ok("System packages installed")

    # Node.js (for browser/scraper skills) — use Node 22 LTS
    node_check = subprocess.run("which node", shell=True, capture_output=True, timeout=10)
    if node_check.returncode != 0:
        info("Installing Node.js 22 LTS...")
        sudo_run("curl -fsSL https://deb.nodesource.com/setup_22.x | bash -", password)
        sudo_run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs", password)
        ok("Node.js installed")
    else:
        ok("Node.js already installed")


def _configure_linux(password):
    """Configure Linux system settings."""
    # Firewall
    info("Configuring firewall...")
    sudo_run("ufw default deny incoming", password)
    sudo_run("ufw default allow outgoing", password)
    sudo_run("ufw allow ssh", password)
    sudo_run("ufw --force enable", password)
    ok("Firewall configured (SSH + outbound only)")

    # Fail2ban
    info("Enabling fail2ban...")
    sudo_run("systemctl enable fail2ban 2>/dev/null", password)
    sudo_run("systemctl start fail2ban 2>/dev/null", password)
    ok("fail2ban enabled")

    # Unattended upgrades — non-interactive
    info("Enabling automatic security updates...")
    sudo_run("DEBIAN_FRONTEND=noninteractive dpkg-reconfigure -plow unattended-upgrades 2>/dev/null", password)
    ok("Automatic security updates enabled")

    # Disable sleep/suspend
    info("Disabling sleep and suspend...")
    sudo_run("systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null", password)
    ok("Sleep/suspend disabled")

    # Laptop lid close → do nothing
    logind_conf = Path("/etc/systemd/logind.conf")
    if logind_conf.exists():
        info("Configuring lid close behavior...")
        try:
            content = logind_conf.read_text()
        except PermissionError:
            # Need sudo to read
            result = subprocess.run(
                "sudo cat /etc/systemd/logind.conf", shell=True,
                input=(password + "\n") if password else None,
                capture_output=True, text=True, timeout=10
            )
            content = result.stdout if result.returncode == 0 else ""

        if content:
            changes = {
                "HandleLidSwitch": "ignore",
                "HandleLidSwitchExternalPower": "ignore",
                "HandleLidSwitchDocked": "ignore",
            }
            for key, value in changes.items():
                line = f"{key}={value}"
                if f"#{key}=" in content or f"{key}=" in content:
                    content = re.sub(rf"^#?{key}=.*$", line, content, flags=re.MULTILINE)
                else:
                    content += f"\n{line}\n"
            sudo_write_file("/etc/systemd/logind.conf", content, password)
            sudo_run("systemctl restart systemd-logind 2>/dev/null", password)
            ok("Lid close configured (do nothing)")

    # Enable SSH
    info("Enabling SSH server...")
    sudo_run("systemctl enable ssh 2>/dev/null", password)
    sudo_run("systemctl start ssh 2>/dev/null", password)
    ok("SSH server enabled")


# --- macOS provisioning ---

MACOS_REMOVE_APPS = [
    "GarageBand.app",
    "iMovie.app",
    "Keynote.app",
    "Numbers.app",
    "Pages.app",
    "Chess.app",
]

MACOS_BREW_PACKAGES = [
    "python@3.12", "ffmpeg", "sox", "git", "jq", "htop", "tmux", "node",
]


def _find_brew():
    """Find brew binary, checking both Apple Silicon and Intel paths."""
    for path in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]:
        if Path(path).exists():
            return path
    # Check PATH
    result = subprocess.run("which brew", shell=True, capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def provision_macos_full(password):
    """Full takeover on macOS."""
    info("Starting full macOS provisioning...")

    # Remove apps
    info("Removing unnecessary applications...")
    for app in MACOS_REMOVE_APPS:
        app_path = Path(f"/Applications/{app}")
        if app_path.exists():
            log_action("remove_app", app)
            result = sudo_run(f"rm -rf '/Applications/{app}'", password)
            if result.returncode == 0:
                ok(f"Removed {app}")
            else:
                warn(f"Could not remove {app} (may require SIP disabled)")

    # Install deps
    _install_macos_deps(password)

    # Configure system
    _configure_macos(password)

    ok("macOS provisioning complete")


def provision_macos_soft(password):
    """Soft install on macOS: deps only, no removal, but still configure system."""
    info("Starting soft macOS provisioning (no app removal)...")
    _install_macos_deps(password)
    _configure_macos(password)
    ok("macOS provisioning complete")


def _install_macos_deps(password=None):
    """Install dependencies via Homebrew."""
    brew = _find_brew()

    # Ensure Homebrew
    if not brew:
        info("Installing Homebrew...")
        if _dry_run:
            info("[DRY RUN] Would install Homebrew")
        else:
            # Homebrew install is interactive — need to handle it carefully
            result = subprocess.run(
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                shell=True, capture_output=True, text=True, timeout=600
            )
            log_action("install_homebrew", f"rc={result.returncode}")

        brew = _find_brew()
        if not brew:
            warn("Homebrew installation may have failed — brew not found in PATH")
            warn("You may need to run: eval \"$(/opt/homebrew/bin/brew shellenv)\" and re-run the provisioner")
            return

        # Add brew to PATH for this session and permanently
        brew_dir = str(Path(brew).parent)
        os.environ["PATH"] = f"{brew_dir}:{os.environ.get('PATH', '')}"
        # Write to shell profile so it persists
        _add_brew_to_profile(brew)

    info("Installing packages via Homebrew...")
    pkgs = " ".join(MACOS_BREW_PACKAGES)
    log_action("brew_install", pkgs)
    result = run(f"{brew} install {pkgs}")
    if result.returncode != 0:
        warn(f"Some brew packages may have failed: {result.stderr[:200]}")
    else:
        ok("Homebrew packages installed")


def _add_brew_to_profile(brew_path):
    """Ensure brew shellenv is in the user's shell profile."""
    if _dry_run:
        info("[DRY RUN] Would add brew to shell profile")
        return

    brew_parent = str(Path(brew_path).parent.parent)
    shellenv_line = f'eval "$({brew_path} shellenv)"'

    # Try .zprofile first (default macOS shell is zsh), then .bash_profile
    for profile in [Path.home() / ".zprofile", Path.home() / ".bash_profile"]:
        if profile.exists():
            content = profile.read_text()
            if shellenv_line not in content:
                with open(profile, "a") as f:
                    f.write(f"\n# Added by MyOldMachine\n{shellenv_line}\n")
                log_action("add_brew_profile", str(profile))
            return

    # Neither exists — create .zprofile
    profile = Path.home() / ".zprofile"
    profile.write_text(f"# Added by MyOldMachine\n{shellenv_line}\n")
    log_action("create_zprofile", str(profile))


def _configure_macos(password):
    """Configure macOS system settings."""
    # Disable sleep
    info("Disabling sleep...")
    sudo_run("pmset -a sleep 0", password)
    sudo_run("pmset -a displaysleep 0", password)
    sudo_run("pmset -a disksleep 0", password)
    ok("Sleep disabled")

    # Disable screen saver
    run("defaults write com.apple.screensaver idleTime 0")
    ok("Screen saver disabled")

    # Enable Screen Sharing (VNC)
    info("Enabling Screen Sharing...")
    ard_path = "/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart"
    if Path(ard_path).exists():
        sudo_run(
            f"{ard_path} -activate -configure -access -on -restart -agent -privs -all",
            password
        )
        ok("Screen Sharing enabled")
    else:
        warn("ARD kickstart not found — enable Screen Sharing manually in System Settings")

    # Lid close: prevent sleep (best effort)
    sudo_run("pmset -a lidwake 0", password)
    ok("Lid wake disabled")


def main():
    global _dry_run

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, required=True)
    parser.add_argument("--os", type=str, choices=["linux", "macos"], required=True)
    parser.add_argument("--takeover", type=str, choices=["full", "soft"], default="full")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview all actions without making changes")
    args = parser.parse_args()

    _dry_run = args.dry_run

    password = get_sudo_password()
    if not password and not _dry_run:
        error("No sudo password found. Run the wizard first.")
        sys.exit(1)

    if _dry_run:
        print(f"\n{BOLD}=== OS Provisioning (DRY RUN — no changes will be made) ==={NC}\n")
    else:
        print(f"\n{BOLD}=== OS Provisioning ==={NC}\n")

    if args.os == "linux":
        if args.takeover == "full":
            provision_linux_full(password)
        else:
            provision_linux_soft(password)
    else:
        if args.takeover == "full":
            provision_macos_full(password)
        else:
            provision_macos_soft(password)

    # Save action log
    save_action_log(args.repo_dir)


if __name__ == "__main__":
    main()
