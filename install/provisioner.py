#!/usr/bin/env python3
"""
MyOldMachine OS Provisioner — System-level setup.

Handles:
- Removing unnecessary packages (full takeover)
- Installing required dependencies
- System configuration (firewall, sleep, VNC, SSH)
- Disk cleanup

Uses OSInfo from os_detect.py for version-aware provisioning.
Every command checks OS version before running — no blind execution.

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

# Ensure install package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))
from install.os_detect import OSInfo, detect as detect_os

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


def sudo_run(cmd, password=None, check=False, timeout=600):
    """Run a command with sudo, passing password safely via stdin (not shell echo)."""
    if _dry_run:
        info(f"[DRY RUN] sudo: {cmd}")
        log_action("dry_run_sudo", cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    try:
        result = subprocess.run(
            full_cmd, shell=True,
            input=stdin_data,
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        warn(f"Command timed out after {timeout}s: {cmd}")
        log_action(f"sudo_timeout: {cmd}", f"timeout={timeout}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"Timed out after {timeout}s"})()
    except Exception as e:
        warn(f"Command error: {cmd}: {e}")
        log_action(f"sudo_error: {cmd}", str(e))
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()
    log_action(f"sudo: {cmd}", f"rc={result.returncode}")
    if check and result.returncode != 0:
        warn(f"Command failed (rc={result.returncode}): {cmd}")
        if result.stderr:
            warn(f"  stderr: {result.stderr[:200]}")
    return result


def run(cmd, check=False, timeout=600):
    """Run a command without sudo."""
    if _dry_run:
        info(f"[DRY RUN] run: {cmd}")
        log_action("dry_run", cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        warn(f"Command timed out after {timeout}s: {cmd}")
        log_action(f"timeout: {cmd}", f"timeout={timeout}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"Timed out after {timeout}s"})()
    except Exception as e:
        warn(f"Command error: {cmd}: {e}")
        log_action(f"error: {cmd}", str(e))
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()
    if check and result.returncode != 0:
        warn(f"Command failed (rc={result.returncode}): {cmd}")
    return result


def run_streaming(cmd, label=""):
    """Run a command with real-time output streaming. Used for long-running tasks like brew compile.
    Returns a result-like object with returncode, stdout, stderr. No timeout — runs until done."""
    if _dry_run:
        info(f"[DRY RUN] run: {cmd}")
        log_action("dry_run", cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    if label:
        info(f"{label}...")

    try:
        process = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
    except Exception as e:
        warn(f"Failed to start: {cmd}: {e}")
        log_action(f"popen_error: {cmd}", str(e))
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()

    stdout_lines = []
    try:
        for line in process.stdout:
            line = line.rstrip('\n')
            stdout_lines.append(line)
            print(f"    {line}")
        process.wait()
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        raise
    except Exception as e:
        warn(f"Error reading output from: {cmd}: {e}")
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass
        log_action(f"stream_error: {cmd}", str(e))
        return type("R", (), {"returncode": 1, "stdout": "\n".join(stdout_lines), "stderr": str(e)})()

    result = type("R", (), {
        "returncode": process.returncode,
        "stdout": "\n".join(stdout_lines),
        "stderr": ""
    })()
    log_action(f"run_streaming: {cmd}", f"rc={process.returncode}")
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
    import shlex
    sudo_run(f"cp {shlex.quote(tmp_path)} {shlex.quote(str(path))}", password)
    os.unlink(tmp_path)
    log_action(f"write_file: {path}", f"{len(content)} bytes")


# --- Linux provisioning ---

# Package names vary by package manager. Keys are apt names.
# Each dict maps: apt_name -> {mgr: pkg_name, ...}. None means not available.
LINUX_REMOVE_PACKAGES = [
    # Desktop environments
    "gnome-shell", "gnome-session", "gnome-desktop3-data", "gnome-control-center",
    "gdm3", "gdm",  # gdm3 = Debian/Ubuntu, gdm = Fedora/Arch/SUSE
    "lightdm", "sddm",
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

# Package lists per package manager. Each list contains packages for that manager.
# We install what's available — individual failures are tolerated.
LINUX_INSTALL_PACKAGES = {
    "apt": [
        "python3-pip", "python3-venv",
        "git", "curl", "wget", "jq",
        "ffmpeg", "sox",
        "htop", "tmux",
        "openssh-server",
        "ufw", "fail2ban",
        "unattended-upgrades",
    ],
    "dnf": [
        "python3-pip", "python3-virtualenv",
        "git", "curl", "wget", "jq",
        "ffmpeg-free", "sox",
        "htop", "tmux",
        "openssh-server",
        "firewalld", "fail2ban",
    ],
    "yum": [
        "python3-pip",
        "git", "curl", "wget", "jq",
        "sox",
        "htop", "tmux",
        "openssh-server",
        "firewalld", "fail2ban",
    ],
    "pacman": [
        "python-pip", "python-virtualenv",
        "git", "curl", "wget", "jq",
        "ffmpeg", "sox",
        "htop", "tmux",
        "openssh",
        "ufw", "fail2ban",
    ],
    "zypper": [
        "python3-pip", "python3-virtualenv",
        "git", "curl", "wget", "jq",
        "ffmpeg", "sox",
        "htop", "tmux",
        "openssh",
        "firewalld", "fail2ban",
    ],
    "apk": [
        "py3-pip", "py3-virtualenv",
        "git", "curl", "wget", "jq",
        "ffmpeg", "sox",
        "htop", "tmux",
        "openssh",
    ],
}

# Commands to install packages per package manager
_PKG_INSTALL_CMDS = {
    "apt": "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkgs}",
    "dnf": "dnf install -y {pkgs}",
    "yum": "yum install -y {pkgs}",
    "pacman": "pacman -S --noconfirm --needed {pkgs}",
    "zypper": "zypper install -y {pkgs}",
    "apk": "apk add {pkgs}",
}

# Commands to update package lists per package manager
_PKG_UPDATE_CMDS = {
    "apt": "apt-get update -qq",
    "dnf": "dnf check-update -q || true",
    "yum": "yum check-update -q || true",
    "pacman": "pacman -Sy",
    "zypper": "zypper refresh -q",
    "apk": "apk update",
}

# Commands to remove packages per package manager
_PKG_REMOVE_CMDS = {
    "apt": "apt-get remove -y --purge {pkg} 2>/dev/null",
    "dnf": "dnf remove -y {pkg} 2>/dev/null",
    "yum": "yum remove -y {pkg} 2>/dev/null",
    "pacman": "pacman -Rns --noconfirm {pkg} 2>/dev/null",
    "zypper": "zypper remove -y {pkg} 2>/dev/null",
    "apk": "apk del {pkg} 2>/dev/null",
}

# Commands to clean up after removal per package manager
_PKG_AUTOREMOVE_CMDS = {
    "apt": "apt-get autoremove -y 2>/dev/null",
    "dnf": "dnf autoremove -y 2>/dev/null",
    "yum": "yum autoremove -y 2>/dev/null",
    "pacman": "pacman -Qdtq | pacman -Rns --noconfirm - 2>/dev/null || true",
    "zypper": "",
    "apk": "",
}


def _get_installed_packages(os_info: OSInfo) -> set:
    """Get list of installed packages using the appropriate package manager."""
    mgr = os_info.package_manager
    if mgr == "apt":
        result = subprocess.run(
            "dpkg --get-selections 2>/dev/null", shell=True,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            pkgs = set()
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "install":
                    pkgs.add(parts[0].split(":")[0])
            return pkgs
    elif mgr == "dnf" or mgr == "yum":
        result = subprocess.run(
            "rpm -qa --qf '%{NAME}\n' 2>/dev/null", shell=True,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return set(result.stdout.strip().splitlines())
    elif mgr == "pacman":
        result = subprocess.run(
            "pacman -Qq 2>/dev/null", shell=True,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return set(result.stdout.strip().splitlines())
    elif mgr == "zypper":
        result = subprocess.run(
            "rpm -qa --qf '%{NAME}\n' 2>/dev/null", shell=True,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return set(result.stdout.strip().splitlines())
    elif mgr == "apk":
        result = subprocess.run(
            "apk info -q 2>/dev/null", shell=True,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return set(result.stdout.strip().splitlines())
    return set()


def provision_linux_full(os_info: OSInfo, password):
    """Full takeover: strip desktop, install deps, configure system."""
    info(f"Starting full Linux provisioning on {os_info.display_name}...")
    mgr = os_info.package_manager

    if not mgr:
        warn("No package manager detected — skipping package removal")
    else:
        # Step 1: Remove bloat
        info("Removing unnecessary packages...")
        installed = _get_installed_packages(os_info)
        remove_cmd_tpl = _PKG_REMOVE_CMDS.get(mgr, "")
        autoremove_cmd = _PKG_AUTOREMOVE_CMDS.get(mgr, "")

        to_remove = [pkg for pkg in LINUX_REMOVE_PACKAGES if pkg in installed]
        if to_remove and remove_cmd_tpl:
            info(f"Removing {len(to_remove)} packages: {', '.join(to_remove[:10])}{'...' if len(to_remove) > 10 else ''}")
            log_action("remove_packages", ", ".join(to_remove))
            removed_count = 0
            for pkg in to_remove:
                result = sudo_run(remove_cmd_tpl.format(pkg=pkg), password)
                if result.returncode == 0:
                    removed_count += 1
                else:
                    warn(f"Failed to remove {pkg}")
            if autoremove_cmd:
                sudo_run(autoremove_cmd, password)
            ok(f"Removed {removed_count}/{len(to_remove)} packages")
        else:
            ok("No bloat packages found to remove")

        # Clean up snap remnants (Debian/Ubuntu specific)
        if "snapd" in installed:
            sudo_run("rm -rf /snap /var/snap /var/lib/snapd 2>/dev/null", password)

    # Step 2: Install dependencies
    _install_linux_deps(os_info, password)

    # Step 3: Configure system
    _configure_linux(os_info, password)

    ok("Linux provisioning complete")


def provision_linux_soft(os_info: OSInfo, password):
    """Soft install: just install deps, skip removal."""
    info(f"Starting soft Linux provisioning on {os_info.display_name}...")
    _install_linux_deps(os_info, password)
    _configure_linux(os_info, password)
    ok("Linux provisioning complete")



def provision_linux_workstation(os_info: OSInfo, password):
    """Workstation mode: install deps + desktop apps, keep desktop intact."""
    info(f"Starting workstation Linux provisioning on {os_info.display_name}...")

    # Step 1: Install base dependencies (same as soft install)
    _install_linux_deps(os_info, password)

    # Step 2: Install workstation/desktop apps via smart compatibility layer
    _install_workstation_apps_smart(os_info, password)

    # Step 3: Configure system (lid close, SSH, firewall — but keep desktop)
    _configure_linux(os_info, password)

    ok("Linux workstation provisioning complete")


# Workstation packages that go through the smart compatibility installer.
# These are the keys from install/compat.py PACKAGES dict.
WORKSTATION_SMART_PACKAGES = [
    "blender", "gimp", "inkscape", "libreoffice",
    "imagemagick", "chromium", "rclone",
]


def _install_workstation_apps_smart(os_info: OSInfo, password):
    """Install workstation apps using the compatibility-aware installer.

    Tries: system package manager → Flatpak → reports what failed with
    actionable guidance for the user/bot to fix later.
    """
    from install.compat import smart_install_batch, print_install_summary

    mgr = os_info.package_manager
    if not mgr:
        warn("No package manager detected — skipping workstation app installation")
        return

    install_cmd_tpl = _PKG_INSTALL_CMDS.get(mgr, "")

    info("Installing workstation apps (compatibility-aware)...")
    info("Strategy: system packages → Flatpak fallback → report")
    info("(This may take a while for large packages like Blender and LibreOffice)")

    results = smart_install_batch(
        WORKSTATION_SMART_PACKAGES,
        package_manager=mgr,
        password=password,
        install_cmd_tpl=install_cmd_tpl,
        dry_run=_dry_run,
    )
    print_install_summary(results)

    # Log results
    for r in results:
        if r.installed:
            log_action(f"workstation_install: {r.package}", f"method={r.method} version={r.version}")
        else:
            log_action(f"workstation_install_failed: {r.package}", r.skipped_reason[:200])


def provision_macos_workstation(os_info: OSInfo, password):
    """Workstation mode on macOS: install deps + desktop apps, keep everything."""
    info(f"Starting workstation macOS provisioning on {os_info.display_name}...")

    # Step 1: Install base dependencies via Homebrew
    _install_macos_deps(os_info, password)

    # Step 2: Install workstation apps via smart compatibility layer
    _install_workstation_apps_smart(os_info, password)

    # Step 3: Configure system — but skip removing apps
    _configure_macos(os_info, password)

    ok("macOS workstation provisioning complete")


def _install_linux_deps(os_info: OSInfo, password):
    """Install required packages on Linux using the detected package manager."""
    mgr = os_info.package_manager

    if not mgr:
        warn("No package manager detected. Installing what we can via binary fallbacks...")
        _install_linux_deps_fallback(os_info, password)
        return

    # Update package lists
    update_cmd = _PKG_UPDATE_CMDS.get(mgr, "")
    if update_cmd:
        info(f"Updating package lists ({mgr})...")
        sudo_run(update_cmd, password)

    # Get the package list for this manager
    packages = LINUX_INSTALL_PACKAGES.get(mgr, LINUX_INSTALL_PACKAGES["apt"])
    install_cmd_tpl = _PKG_INSTALL_CMDS.get(mgr, "")

    if not install_cmd_tpl:
        warn(f"No install command template for '{mgr}'")
        return

    info(f"Installing dependencies via {mgr}...")
    pkgs = " ".join(packages)
    log_action("install_packages", f"{mgr}: {pkgs}")
    result = sudo_run(install_cmd_tpl.format(pkgs=pkgs), password)
    if result.returncode != 0:
        # Try one by one — some packages may not exist on this distro
        warn(f"Batch install had issues. Installing packages individually...")
        installed_count = 0
        for pkg in packages:
            r = sudo_run(install_cmd_tpl.format(pkgs=pkg), password)
            if r.returncode == 0:
                installed_count += 1
            else:
                warn(f"  {pkg} — not available or failed")
        ok(f"Installed {installed_count}/{len(packages)} packages")
    else:
        ok("System packages installed")

    # For dnf/yum: ffmpeg may need RPM Fusion. Try installing, note if missing.
    import shutil as _shutil
    if mgr in ("dnf", "yum") and not _shutil.which("ffmpeg"):
        warn("ffmpeg not available via default repos. On Fedora/RHEL, enable RPM Fusion:")
        warn("  sudo dnf install https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm")
        warn("  sudo dnf install ffmpeg")
        warn("The bot can help you with this after install.")

    # Node.js — needed for Claude CLI and browser skill
    if not _shutil.which("node"):
        info("Installing Node.js...")
        if mgr == "apt":
            dl_result = run("curl -fsSL -o /tmp/nodesource_setup.sh https://deb.nodesource.com/setup_22.x")
            if dl_result.returncode == 0:
                sudo_run("bash /tmp/nodesource_setup.sh", password)
                sudo_run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs", password)
                run("rm -f /tmp/nodesource_setup.sh")
        elif mgr == "dnf":
            sudo_run("dnf module enable nodejs:22 -y 2>/dev/null || true", password)
            sudo_run("dnf install -y nodejs npm", password)
        elif mgr == "yum":
            dl_result = run("curl -fsSL -o /tmp/nodesource_setup.sh https://rpm.nodesource.com/setup_22.x")
            if dl_result.returncode == 0:
                sudo_run("bash /tmp/nodesource_setup.sh", password)
                sudo_run("yum install -y nodejs", password)
                run("rm -f /tmp/nodesource_setup.sh")
        elif mgr == "pacman":
            sudo_run("pacman -S --noconfirm --needed nodejs npm", password)
        elif mgr == "zypper":
            sudo_run("zypper install -y nodejs22 npm22 || zypper install -y nodejs npm", password)
        elif mgr == "apk":
            sudo_run("apk add nodejs npm", password)

        if _shutil.which("node"):
            ok("Node.js installed")
        else:
            warn("Node.js could not be installed. Claude CLI and browser skill won't work.")
            warn("The bot can help install Node.js after setup.")
    else:
        ok("Node.js already installed")


def _install_linux_deps_fallback(os_info: OSInfo, password):
    """Last-resort binary checks when no package manager is available."""
    import shutil as _shutil
    critical = {"git": "git", "curl": "curl", "python3": "python3"}
    for name, binary in critical.items():
        if _shutil.which(binary):
            ok(f"{name} available")
        else:
            warn(f"{name} not found — install manually")


def _configure_linux(os_info: OSInfo, password):
    """Configure Linux system settings — works across distros."""
    mgr = os_info.package_manager
    import shutil as _shutil

    # Firewall — use ufw (Debian/Arch) or firewalld (RHEL/SUSE)
    if _shutil.which("ufw"):
        info("Configuring firewall (ufw)...")
        sudo_run("ufw default deny incoming", password)
        sudo_run("ufw default allow outgoing", password)
        sudo_run("ufw allow ssh", password)
        sudo_run("ufw --force enable", password)
        ok("Firewall configured (SSH + outbound only)")
    elif _shutil.which("firewall-cmd"):
        info("Configuring firewall (firewalld)...")
        sudo_run("systemctl enable firewalld 2>/dev/null", password)
        sudo_run("systemctl start firewalld 2>/dev/null", password)
        sudo_run("firewall-cmd --permanent --add-service=ssh 2>/dev/null", password)
        sudo_run("firewall-cmd --reload 2>/dev/null", password)
        ok("Firewall configured (SSH only)")
    else:
        warn("No firewall tool found (ufw or firewalld). Configure manually.")

    # Fail2ban — works on all distros with systemd
    if _shutil.which("fail2ban-server") or _shutil.which("fail2ban-client"):
        info("Enabling fail2ban...")
        sudo_run("systemctl enable fail2ban 2>/dev/null", password)
        sudo_run("systemctl start fail2ban 2>/dev/null", password)
        ok("fail2ban enabled")

    # Automatic security updates
    if mgr == "apt" and _shutil.which("unattended-upgrades"):
        info("Enabling automatic security updates...")
        sudo_run("DEBIAN_FRONTEND=noninteractive dpkg-reconfigure -plow unattended-upgrades 2>/dev/null", password)
        ok("Automatic security updates enabled")
    elif mgr == "dnf" and _shutil.which("dnf-automatic"):
        info("Enabling dnf-automatic...")
        sudo_run("systemctl enable dnf-automatic-install.timer 2>/dev/null", password)
        sudo_run("systemctl start dnf-automatic-install.timer 2>/dev/null", password)
        ok("Automatic security updates enabled")
    else:
        info("Automatic security updates: configure manually for your distro")

    # Disable sleep/suspend (works on all systemd distros)
    if _shutil.which("systemctl"):
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

    # Enable SSH — service name varies by distro
    info("Enabling SSH server...")
    sudo_run("systemctl enable sshd 2>/dev/null || systemctl enable ssh 2>/dev/null", password)
    sudo_run("systemctl start sshd 2>/dev/null || systemctl start ssh 2>/dev/null", password)
    ok("SSH server enabled")


# --- macOS provisioning ---

# Apps to remove in full takeover (all versions)
MACOS_REMOVE_APPS_COMMON = [
    "GarageBand.app",
    "iMovie.app",
    "Keynote.app",
    "Numbers.app",
    "Pages.app",
    "Chess.app",
]

# Apps only present on specific macOS versions
MACOS_REMOVE_APPS_BY_VERSION = {
    # News and Stocks appear from Mojave (10.14) onward
    "News.app": (10, 14),
    "Stocks.app": (10, 14),
    # Freeform from Ventura (13) onward
    "Freeform.app": (13, 0),
}

MACOS_BREW_PACKAGES = [
    "python@3.12", "ffmpeg", "sox", "git", "jq", "htop", "tmux", "node",
]


def _find_brew():
    """Find brew binary, checking both Apple Silicon and Intel paths."""
    for path in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]:
        if Path(path).exists():
            return path
    result = subprocess.run("which brew", shell=True, capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def provision_macos_full(os_info: OSInfo, password):
    """Full takeover on macOS — version-aware."""
    info(f"Starting full macOS provisioning on {os_info.display_name}...")

    # Remove apps (SIP-aware)
    _remove_macos_apps(os_info, password)

    # Install deps (via Homebrew)
    _install_macos_deps(os_info, password)

    # Configure system (version-aware)
    _configure_macos(os_info, password)

    ok("macOS provisioning complete")


def provision_macos_soft(os_info: OSInfo, password):
    """Soft install on macOS: deps only, no removal, but still configure system."""
    info(f"Starting soft macOS provisioning on {os_info.display_name}...")
    _install_macos_deps(os_info, password)
    _configure_macos(os_info, password)
    ok("macOS provisioning complete")


def _remove_macos_apps(os_info: OSInfo, password):
    """Remove unnecessary apps, respecting SIP and version differences."""
    info("Removing unnecessary applications...")

    # Check SIP status — if enabled, some apps in /Applications may not be removable
    sip_enabled = _check_sip_status()
    if sip_enabled:
        info("SIP is enabled (normal). Some system apps may not be removable.")

    apps_to_remove = list(MACOS_REMOVE_APPS_COMMON)

    # Add version-specific apps
    for app, (min_major, min_minor) in MACOS_REMOVE_APPS_BY_VERSION.items():
        if os_info._mac_version_gte(min_major, min_minor):
            apps_to_remove.append(app)

    removed = 0
    skipped = 0
    for app in apps_to_remove:
        app_path = Path(f"/Applications/{app}")
        if not app_path.exists():
            continue

        log_action("remove_app", app)
        result = sudo_run(f"rm -rf '/Applications/{app}'", password)
        if result.returncode == 0:
            # Verify it's actually gone (SIP might silently prevent removal)
            if not app_path.exists():
                ok(f"Removed {app}")
                removed += 1
            else:
                warn(f"Could not remove {app} (protected by SIP)")
                skipped += 1
        else:
            warn(f"Could not remove {app} (may require SIP disabled)")
            skipped += 1

    if removed:
        ok(f"Removed {removed} app(s)")
    if skipped:
        warn(f"Skipped {skipped} protected app(s) (SIP)")
    if not removed and not skipped:
        ok("No removable apps found")


def _check_sip_status() -> bool:
    """Check if System Integrity Protection is enabled."""
    try:
        result = subprocess.run(
            ["csrutil", "status"],
            capture_output=True, text=True, timeout=5
        )
        return "enabled" in result.stdout.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True  # Assume enabled if we can't check


def _install_macos_deps(os_info: OSInfo, password=None):
    """Install dependencies via Homebrew — version-aware."""
    brew = _find_brew() or os_info.brew_path

    if not brew:
        if not os_info.has_homebrew_support:
            error(f"macOS {os_info.version} ({os_info.version_name}) does not support Homebrew.")
            error("Install dependencies manually or upgrade macOS.")
            return

        info("Installing Homebrew...")
        if _dry_run:
            info("[DRY RUN] Would install Homebrew")
        else:
            # Homebrew install needs NONINTERACTIVE=1 to avoid prompts
            os.environ["NONINTERACTIVE"] = "1"
            result = run_streaming(
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                label="Installing Homebrew"
            )
            log_action("install_homebrew", f"rc={result.returncode}")
            if result.returncode != 0:
                warn(f"Homebrew install may have had issues (check output above)")

        brew = _find_brew()
        if not brew:
            warn("Homebrew installation may have failed — brew not found in PATH")
            warn("You may need to run: eval \"$(/opt/homebrew/bin/brew shellenv)\" and re-run the provisioner")
            return

        # Add brew to PATH for this session and permanently
        brew_dir = str(Path(brew).parent)
        os.environ["PATH"] = f"{brew_dir}:{os.environ.get('PATH', '')}"
        _add_brew_to_profile(os_info, brew)

    info("Installing packages via Homebrew...")
    info("(On older macOS, Homebrew compiles from source — this can take a while)")

    # Map brew package names to the binary they provide (for PATH-based fallback check).
    # On old macOS (Catalina etc.), brew install often fails for packages that are
    # already present via older brew installs or Xcode CLT. If the binary works,
    # skip the brew install entirely — don't break what's working.
    import shutil as _shutil
    _pkg_to_binary = {
        "python@3.12": "python3",
        "ffmpeg": "ffmpeg",
        "sox": "sox",
        "git": "git",
        "jq": "jq",
        "htop": "htop",
        "tmux": "tmux",
        "node": "node",
    }

    # Install packages one by one so a single failure doesn't block everything.
    # On old macOS (e.g. Catalina), brew may compile from source and return non-zero
    # even though the package installed fine ("post-install step did not complete").
    # So we verify success by checking if the package is actually present afterward.
    #
    # We use run_streaming() instead of run() for brew install — this streams compiler
    # output to the terminal in real time so the user can see progress instead of
    # staring at a blank screen for 30+ minutes while ffmpeg compiles.
    installed_count = 0
    failed = []
    for i, pkg in enumerate(MACOS_BREW_PACKAGES, 1):
        # Check if already installed via brew
        already = run(f"{brew} list {pkg} 2>/dev/null", timeout=30)
        if already.returncode == 0:
            ok(f"[{i}/{len(MACOS_BREW_PACKAGES)}] {pkg} already installed")
            installed_count += 1
            continue

        # Fallback: check if the binary is available in PATH (e.g. from Xcode CLT
        # or an older brew install that's still functional). On old macOS, brew
        # frequently fails to reinstall/upgrade packages that are already working.
        binary = _pkg_to_binary.get(pkg)
        if binary and _shutil.which(binary):
            ok(f"[{i}/{len(MACOS_BREW_PACKAGES)}] {pkg} available ({_shutil.which(binary)})")
            installed_count += 1
            continue

        log_action("brew_install", pkg)
        result = run_streaming(
            f"{brew} install {pkg} 2>&1",
            label=f"[{i}/{len(MACOS_BREW_PACKAGES)}] Installing {pkg}"
        )
        combined_output = (result.stdout or "") + (result.stderr or "")

        if result.returncode == 0:
            installed_count += 1
            ok(f"{pkg} installed")
        elif "already installed" in combined_output:
            installed_count += 1
            ok(f"{pkg} already installed")
        else:
            # Non-zero exit — but did the package actually install?
            # Check with brew list (more reliable than exit codes on old macOS)
            verify = run(f"{brew} list {pkg} 2>/dev/null", timeout=30)
            if verify.returncode == 0:
                # Package is there despite the error — post-install warning, etc.
                warn(f"{pkg}: brew returned an error but package is installed (likely a post-install warning)")
                installed_count += 1
            elif binary and _shutil.which(binary):
                # Binary appeared in PATH even though brew claims failure
                ok(f"[{i}/{len(MACOS_BREW_PACKAGES)}] {pkg} available after install ({_shutil.which(binary)})")
                installed_count += 1
            else:
                warn(f"Failed to install {pkg}: {combined_output[:150]}")
                failed.append(pkg)

    # Try to link all installed packages (ensures binaries are in PATH)
    run(f"{brew} link --overwrite python@3.12 2>/dev/null")

    # For packages that failed via brew, try direct binary install as fallback.
    # On old macOS (Catalina), Homebrew's dependency chains are broken beyond repair.
    if failed:
        still_failed = []
        for pkg in failed:
            binary = _pkg_to_binary.get(pkg)
            if pkg == "node" and not _shutil.which("node"):
                info("Homebrew failed for node — trying direct binary install...")
                if _install_node_direct(os_info):
                    installed_count += 1
                    continue
            if pkg == "ffmpeg" and not _shutil.which("ffmpeg"):
                info("Homebrew failed for ffmpeg — trying static binary install...")
                if _install_ffmpeg_direct(os_info):
                    installed_count += 1
                    continue
            # For other packages, check if they appeared in PATH after all
            if binary and _shutil.which(binary):
                ok(f"{pkg} available ({_shutil.which(binary)})")
                installed_count += 1
            else:
                still_failed.append(pkg)
        failed = still_failed

    if failed:
        warn(f"Failed to install: {', '.join(failed)}")
    ok(f"Homebrew packages: {installed_count}/{len(MACOS_BREW_PACKAGES)} installed")


def _install_node_direct(os_info: OSInfo) -> bool:
    """Install Node.js from official binary tarball when Homebrew fails.

    Downloads from nodejs.org and extracts to /usr/local (Intel) or /opt/homebrew (AS).
    Node 20 LTS supports macOS 10.15+ (Catalina). This bypasses Homebrew's broken
    dependency chains on old macOS.
    """
    import shutil as _shutil

    # Node 20 LTS — last major version to support macOS 10.15 (Catalina).
    # Node 22+ requires macOS 11+, Node 24+ requires macOS 13.5+.
    node_version = "20.20.1"
    arch = platform.machine()
    if arch == "x86_64":
        node_arch = "x64"
    elif arch == "arm64":
        node_arch = "arm64"
    else:
        warn(f"Unsupported architecture for Node.js direct install: {arch}")
        return False

    tarball = f"node-v{node_version}-darwin-{node_arch}.tar.gz"
    url = f"https://nodejs.org/dist/v{node_version}/{tarball}"
    tmp_dir = Path(tempfile.mkdtemp(prefix="myoldmachine_node_"))
    tmp_tar = tmp_dir / tarball

    info(f"Downloading Node.js {node_version} ({node_arch})...")
    dl_result = run(f"curl -fsSL -o '{tmp_tar}' '{url}'", timeout=120)
    if dl_result.returncode != 0:
        warn(f"Failed to download Node.js: {dl_result.stderr[:200]}")
        return False

    # Extract to /usr/local so node/npm are in a standard PATH location
    install_prefix = "/usr/local"
    info(f"Installing Node.js to {install_prefix}...")
    password = get_sudo_password()

    # Extract with --strip-components=1 to put bin/lib/etc directly into prefix
    extract_result = sudo_run(
        f"tar -xzf '{tmp_tar}' -C '{install_prefix}' --strip-components=1",
        password, timeout=60
    )

    # Clean up temp
    try:
        import shutil
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
    except Exception:
        pass

    if extract_result.returncode != 0:
        warn(f"Failed to extract Node.js: {extract_result.stderr[:200]}")
        return False

    # Verify node and npm are now available
    # Update PATH for this process in case /usr/local/bin isn't in it
    if "/usr/local/bin" not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"/usr/local/bin:{os.environ.get('PATH', '')}"

    node_path = _shutil.which("node")
    npm_path = _shutil.which("npm")
    if node_path and npm_path:
        # Verify it actually runs
        verify = run(f"'{node_path}' --version", timeout=10)
        if verify.returncode == 0:
            ok(f"Node.js installed: {verify.stdout.strip()} ({node_path})")
            log_action("install_node_direct", f"v{node_version} {node_arch}")
            return True

    warn("Node.js binary was extracted but 'node' not found in PATH")
    return False


def _install_ffmpeg_direct(os_info: OSInfo) -> bool:
    """Install ffmpeg from evermeet.cx static builds when Homebrew fails.

    evermeet.cx provides static ffmpeg builds for macOS that have no dependencies.
    This is the standard fallback used by many macOS tools when Homebrew can't build ffmpeg.
    """
    import shutil as _shutil

    arch = platform.machine()
    # evermeet.cx only provides x86_64 builds (Intel). For ARM, the binary runs via Rosetta.
    # On truly old Macs (all Intel), this works directly.
    info("Downloading ffmpeg static build from evermeet.cx...")
    tmp_dir = Path(tempfile.mkdtemp(prefix="myoldmachine_ffmpeg_"))
    zip_path = tmp_dir / "ffmpeg.zip"

    # Download the latest ffmpeg binary
    dl_result = run(
        f"curl -fsSL -o '{zip_path}' 'https://evermeet.cx/ffmpeg/getrelease/zip'",
        timeout=120
    )
    if dl_result.returncode != 0:
        warn(f"Failed to download ffmpeg: {dl_result.stderr[:200]}")
        # Cleanup
        try:
            import shutil
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
        except Exception:
            pass
        return False

    # Extract and install to /usr/local/bin
    password = get_sudo_password()
    run(f"unzip -o '{zip_path}' -d '{tmp_dir}'", timeout=30)
    ffmpeg_bin = tmp_dir / "ffmpeg"

    if not ffmpeg_bin.exists():
        # Try finding it in any subdirectory
        for f in tmp_dir.rglob("ffmpeg"):
            if f.is_file():
                ffmpeg_bin = f
                break

    if ffmpeg_bin.exists():
        import shlex
        sudo_run(f"cp {shlex.quote(str(ffmpeg_bin))} /usr/local/bin/ffmpeg", password)
        sudo_run("chmod +x /usr/local/bin/ffmpeg", password)

        # Also download ffprobe (needed by many audio/video skills)
        probe_zip = tmp_dir / "ffprobe.zip"
        dl2 = run(
            f"curl -fsSL -o '{probe_zip}' 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip'",
            timeout=120
        )
        if dl2.returncode == 0:
            run(f"unzip -o '{probe_zip}' -d '{tmp_dir}'", timeout=30)
            probe_bin = tmp_dir / "ffprobe"
            if not probe_bin.exists():
                for f in tmp_dir.rglob("ffprobe"):
                    if f.is_file():
                        probe_bin = f
                        break
            if probe_bin.exists():
                sudo_run(f"cp {shlex.quote(str(probe_bin))} /usr/local/bin/ffprobe", password)
                sudo_run("chmod +x /usr/local/bin/ffprobe", password)

    # Cleanup
    try:
        import shutil
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
    except Exception:
        pass

    # Ensure /usr/local/bin is in PATH
    if "/usr/local/bin" not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"/usr/local/bin:{os.environ.get('PATH', '')}"

    if _shutil.which("ffmpeg"):
        verify = run("ffmpeg -version", timeout=10)
        if verify.returncode == 0:
            version_line = verify.stdout.split("\n")[0] if verify.stdout else "unknown"
            ok(f"ffmpeg installed: {version_line}")
            log_action("install_ffmpeg_direct", version_line)
            return True

    warn("ffmpeg binary was installed but could not be verified")
    return False


def _add_brew_to_profile(os_info: OSInfo, brew_path):
    """Ensure brew shellenv is in the user's shell profile."""
    if _dry_run:
        info("[DRY RUN] Would add brew to shell profile")
        return

    shellenv_line = f'eval "$({brew_path} shellenv)"'

    # Determine the right profile file based on default shell
    if os_info.has_zsh_default:
        # macOS 10.15+ defaults to zsh
        profile_candidates = [Path.home() / ".zprofile", Path.home() / ".zshrc"]
    else:
        # Pre-Catalina defaults to bash
        profile_candidates = [Path.home() / ".bash_profile", Path.home() / ".profile"]

    for profile in profile_candidates:
        if profile.exists():
            content = profile.read_text()
            if shellenv_line not in content:
                with open(profile, "a") as f:
                    f.write(f"\n# Added by MyOldMachine\n{shellenv_line}\n")
                log_action("add_brew_profile", str(profile))
            return

    # No profile exists — create the right one for this OS version
    if os_info.has_zsh_default:
        profile = Path.home() / ".zprofile"
    else:
        profile = Path.home() / ".bash_profile"
    profile.write_text(f"# Added by MyOldMachine\n{shellenv_line}\n")
    log_action("create_profile", str(profile))


def _configure_macos(os_info: OSInfo, password):
    """Configure macOS system settings — all commands are version-aware."""

    # --- Disable sleep ---
    info("Disabling sleep...")
    # pmset works on all supported macOS versions (10.14+)
    sudo_run("pmset -a sleep 0", password)
    sudo_run("pmset -a displaysleep 0", password)
    sudo_run("pmset -a disksleep 0", password)

    # On portables (MacBooks), also prevent sleep on lid close
    # Check if this is a portable
    hw_model = _get_hw_model()
    is_portable = "book" in hw_model.lower() if hw_model else False

    if is_portable:
        info("MacBook detected — configuring lid behavior...")
        # Disable lid wake (works on all versions)
        sudo_run("pmset -a lidwake 0", password)

        # destroysleepimage prevents writing sleep image to disk (saves space + prevents sleep)
        sudo_run("pmset -a hibernatemode 0", password)
        sudo_run("pmset -a standby 0", password)
        if os_info._mac_version_gte(10, 15):
            # Catalina+ has standbydelayhigh/low
            sudo_run("pmset -a standbydelaylow 0", password)
            sudo_run("pmset -a standbydelayhigh 0", password)
        else:
            sudo_run("pmset -a standbydelay 86400", password)

        # Remove sleep image to free disk space
        sudo_run("rm -f /var/vm/sleepimage 2>/dev/null", password)
        ok("Lid close sleep prevention configured")
    else:
        ok("Desktop Mac — no lid configuration needed")

    ok("Sleep disabled")

    # --- Disable screen saver ---
    run("defaults write com.apple.screensaver idleTime 0")
    ok("Screen saver disabled")

    # --- Enable Screen Sharing (VNC) ---
    _configure_screen_sharing(os_info, password)

    # --- Disable Gatekeeper quarantine for downloaded apps (reduces friction) ---
    info("Disabling Gatekeeper quarantine warnings...")
    sudo_run("defaults write com.apple.LaunchServices LSQuarantine -bool false", password)
    ok("Gatekeeper quarantine warnings disabled")

    # --- Disable automatic macOS updates popping up ---
    info("Disabling automatic update prompts...")
    if os_info._mac_version_gte(13):
        # Ventura+: new plist domain
        sudo_run("defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool false", password)
        sudo_run("defaults write /Library/Preferences/com.apple.SoftwareUpdate CriticalUpdateInstall -bool true", password)
    else:
        sudo_run("defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled -bool true", password)
        sudo_run("defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool false", password)
        sudo_run("defaults write /Library/Preferences/com.apple.SoftwareUpdate CriticalUpdateInstall -bool true", password)
    # Keep security updates on, just disable the upgrade nag
    ok("Update prompts disabled (security updates still active)")

    # --- Disable Notification Center banners (less noise on headless machine) ---
    info("Reducing notification noise...")
    run("defaults write com.apple.notificationcenterui bannerTime 3")
    ok("Notification banners reduced")


def _get_hw_model() -> str:
    """Get hardware model identifier (e.g. 'MacBookPro11,3')."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.model"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _configure_screen_sharing(os_info: OSInfo, password):
    """
    Enable Screen Sharing / VNC — version-aware.

    - macOS 10.14 (Mojave) to 12 (Monterey): ARDAgent kickstart works reliably
    - macOS 13 (Ventura)+: kickstart still exists but Apple is phasing it out.
      TCC/privacy controls may block it. We try kickstart first, fall back to
      telling the user to enable it manually in System Settings.
    - macOS 14 (Sonoma)+: kickstart may require Full Disk Access (FDA) for
      the terminal app. We detect and warn.
    """
    info("Enabling Screen Sharing...")

    ard_kickstart = "/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart"

    if not Path(ard_kickstart).exists():
        warn("ARD kickstart not found. Enable Screen Sharing manually:")
        if os_info.has_system_settings:
            warn("  System Settings → General → Sharing → Screen Sharing → On")
        else:
            warn("  System Preferences → Sharing → Screen Sharing → On")
        return

    # Try kickstart — works on all versions but may fail on 13+ due to TCC
    result = sudo_run(
        f"{ard_kickstart} -activate -configure -access -on "
        f"-restart -agent -privs -all",
        password
    )

    if result.returncode == 0:
        ok("Screen Sharing enabled via ARD kickstart")
    else:
        stderr = result.stderr.strip()
        log_action("ard_kickstart_failed", stderr[:200])

        if os_info._mac_version_gte(13):
            # Ventura+ — TCC likely blocking it
            warn("ARD kickstart failed (likely TCC privacy restriction).")
            warn("On macOS 13+ you may need to enable Screen Sharing manually:")
            warn("  System Settings → General → Sharing → Screen Sharing → On")
            warn("Or grant Full Disk Access to Terminal in:")
            warn("  System Settings → Privacy & Security → Full Disk Access")
        else:
            warn(f"ARD kickstart failed: {stderr[:100]}")
            warn("Enable Screen Sharing manually:")
            warn("  System Preferences → Sharing → Screen Sharing")

    # Enable VNC access (works alongside Screen Sharing)
    info("Configuring VNC access...")
    sudo_run(
        "defaults write /Library/Preferences/com.apple.RemoteManagement VNCAlwaysStartOnConsole -bool true",
        password
    )
    ok("VNC configured")
    warn("Note: No VNC password has been set. Set one in Screen Sharing preferences for security.")


def provision(os_info: OSInfo, takeover: str) -> bool:
    """Main entry point — dispatches to the right provisioner based on OSInfo. Returns True on success."""
    password = get_sudo_password()
    if not password and not _dry_run:
        error("No sudo password found. Run the wizard first.")
        return False

    # Map legacy names to new names
    mode = takeover
    if mode == "full":
        mode = "headless"
    elif mode == "soft":
        mode = "minimal"

    if _dry_run:
        print(f"\n{BOLD}=== OS Provisioning (DRY RUN — no changes will be made) ==={NC}")
    else:
        print(f"\n{BOLD}=== OS Provisioning ==={NC}")
    print(f"    Target: {os_info.display_name}")
    print(f"    Mode: {mode}\n")

    try:
        if os_info.os_type == "linux":
            if mode == "headless":
                provision_linux_full(os_info, password)
            elif mode == "workstation":
                provision_linux_workstation(os_info, password)
            else:
                provision_linux_soft(os_info, password)
        elif os_info.os_type == "macos":
            if mode == "headless":
                provision_macos_full(os_info, password)
            elif mode == "workstation":
                provision_macos_workstation(os_info, password)
            else:
                provision_macos_soft(os_info, password)
        else:
            error(f"Unsupported OS type: {os_info.os_type}")
            return False
    except KeyboardInterrupt:
        warn("Provisioning interrupted by user")
        return False
    except Exception as e:
        error(f"Provisioning failed: {e}")
        return False

    return True


def main():
    """CLI entry point — detects OS and runs provisioning."""
    global _dry_run

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, required=True)
    parser.add_argument("--os", type=str, choices=["linux", "macos"],
                        help="Override OS detection (optional)")
    parser.add_argument("--takeover", type=str, choices=["workstation", "minimal", "headless", "full", "soft"], default="workstation")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview all actions without making changes")
    args = parser.parse_args()

    _dry_run = args.dry_run

    # Use os_detect for full version-aware detection
    os_info = detect_os()

    # Allow --os override but warn if it doesn't match detection
    if args.os and args.os != os_info.os_type:
        warn(f"Detected {os_info.os_type} but --os {args.os} was specified. Using detected OS.")

    # Check for blockers
    if os_info.blockers:
        for b in os_info.blockers:
            error(b)
        sys.exit(1)

    # Show warnings
    for w in os_info.warnings:
        warn(w)

    success = provision(os_info, args.takeover)

    # Save action log
    save_action_log(args.repo_dir)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
