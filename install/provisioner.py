#!/usr/bin/env python3
"""
MyOldMachine OS Provisioner — System-level setup.

Handles:
- Removing unnecessary packages (full takeover)
- Installing required dependencies
- System configuration (firewall, sleep, VNC, SSH)
- Disk cleanup
"""

import argparse
import platform
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
    print(f"{BLUE}[PROV]{NC} {msg}")


def ok(msg):
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")


def get_sudo_password():
    """Read sudo password from storage."""
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text().strip()
    return None


def sudo_run(cmd, password=None, check=False):
    """Run a command with sudo."""
    if password:
        full_cmd = f"echo '{password}' | sudo -S {cmd}"
        result = subprocess.run(
            full_cmd, shell=True,
            capture_output=True, text=True, timeout=600
        )
    else:
        result = subprocess.run(
            f"sudo {cmd}", shell=True,
            capture_output=True, text=True, timeout=600
        )
    if check and result.returncode != 0:
        error(f"Command failed: {cmd}")
        error(f"stderr: {result.stderr}")
    return result


def run(cmd, check=False):
    """Run a command without sudo."""
    result = subprocess.run(
        cmd, shell=True,
        capture_output=True, text=True, timeout=600
    )
    if check and result.returncode != 0:
        error(f"Command failed: {cmd}")
    return result


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
    # Check which packages are actually installed
    result = run("dpkg --get-selections 2>/dev/null")
    installed = set()
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "install":
                installed.add(parts[0].split(":")[0])  # strip arch suffix

    to_remove = [pkg for pkg in LINUX_REMOVE_PACKAGES if pkg in installed]
    if to_remove:
        pkgs = " ".join(to_remove)
        info(f"Removing {len(to_remove)} packages...")
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
    result = sudo_run(f"apt-get install -y -qq {pkgs}", password)
    if result.returncode != 0:
        warn(f"Some packages may have failed: {result.stderr[:200]}")
    else:
        ok("System packages installed")

    # Node.js (for browser/scraper skills)
    if run("which node").returncode != 0:
        info("Installing Node.js...")
        sudo_run("curl -fsSL https://deb.nodesource.com/setup_18.x | bash -", password)
        sudo_run("apt-get install -y -qq nodejs", password)
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

    # Unattended upgrades
    info("Enabling automatic security updates...")
    sudo_run("dpkg-reconfigure -plow unattended-upgrades 2>/dev/null", password)
    ok("Automatic security updates enabled")

    # Disable sleep/suspend
    info("Disabling sleep and suspend...")
    sudo_run("systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null", password)
    ok("Sleep/suspend disabled")

    # Laptop lid close → do nothing
    logind_conf = Path("/etc/systemd/logind.conf")
    if logind_conf.exists():
        info("Configuring lid close behavior...")
        content = logind_conf.read_text()
        changes = {
            "HandleLidSwitch": "ignore",
            "HandleLidSwitchExternalPower": "ignore",
            "HandleLidSwitchDocked": "ignore",
        }
        for key, value in changes.items():
            line = f"{key}={value}"
            if f"#{key}=" in content or f"{key}=" in content:
                # Replace existing (commented or not)
                import re
                content = re.sub(rf"^#?{key}=.*$", line, content, flags=re.MULTILINE)
            else:
                content += f"\n{line}\n"
        sudo_run(f"bash -c 'cat > /etc/systemd/logind.conf << \"LOGINDEOF\"\n{content}\nLOGINDEOF'", password)
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


def provision_macos_full(password):
    """Full takeover on macOS."""
    info("Starting full macOS provisioning...")

    # Remove apps
    info("Removing unnecessary applications...")
    for app in MACOS_REMOVE_APPS:
        app_path = Path(f"/Applications/{app}")
        if app_path.exists():
            result = sudo_run(f"rm -rf '/Applications/{app}'", password)
            if result.returncode == 0:
                ok(f"Removed {app}")
            else:
                warn(f"Could not remove {app} (may require SIP disabled)")
        else:
            pass  # Not installed, skip silently

    # Install deps
    _install_macos_deps()

    # Configure system
    _configure_macos(password)

    ok("macOS provisioning complete")


def provision_macos_soft(password):
    """Soft install on macOS: deps only, no removal."""
    info("Starting soft macOS provisioning (no app removal)...")
    _install_macos_deps()
    ok("macOS provisioning complete")


def _install_macos_deps():
    """Install dependencies via Homebrew."""
    # Ensure Homebrew
    if run("which brew").returncode != 0:
        info("Installing Homebrew...")
        run('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        # Add to path
        for prefix in ["/opt/homebrew/bin", "/usr/local/bin"]:
            brew = Path(prefix) / "brew"
            if brew.exists():
                run(f'eval "$({brew} shellenv)"')
                break

    info("Installing packages via Homebrew...")
    pkgs = " ".join(MACOS_BREW_PACKAGES)
    result = run(f"brew install {pkgs}")
    if result.returncode != 0:
        warn(f"Some brew packages may have failed: {result.stderr[:200]}")
    else:
        ok("Homebrew packages installed")


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, required=True)
    parser.add_argument("--os", type=str, choices=["linux", "macos"], required=True)
    parser.add_argument("--takeover", type=str, choices=["full", "soft"], default="full")
    args = parser.parse_args()

    password = get_sudo_password()
    if not password:
        error("No sudo password found. Run the wizard first.")
        sys.exit(1)

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


if __name__ == "__main__":
    main()
