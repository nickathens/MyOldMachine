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


def provision_linux_full(os_info: OSInfo, password):
    """Full takeover: strip desktop, install deps, configure system."""
    info(f"Starting full Linux provisioning on {os_info.display_name}...")

    # Step 1: Remove bloat
    info("Removing unnecessary packages...")
    result = subprocess.run(
        "dpkg --get-selections 2>/dev/null", shell=True,
        capture_output=True, text=True, timeout=60
    )
    installed = set()
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "install":
                installed.add(parts[0].split(":")[0])

    to_remove = [pkg for pkg in LINUX_REMOVE_PACKAGES if pkg in installed]
    if to_remove:
        info(f"Removing {len(to_remove)} packages: {', '.join(to_remove[:10])}{'...' if len(to_remove) > 10 else ''}")
        log_action("remove_packages", ", ".join(to_remove))
        # Remove one by one so a single failure doesn't block everything
        removed_count = 0
        for pkg in to_remove:
            result = sudo_run(f"apt-get remove -y --purge {pkg} 2>/dev/null", password)
            if result.returncode == 0:
                removed_count += 1
            else:
                warn(f"Failed to remove {pkg}")
        sudo_run("apt-get autoremove -y 2>/dev/null", password)
        ok(f"Removed {removed_count}/{len(to_remove)} packages")
    else:
        ok("No bloat packages found to remove")

    # Clean up snap remnants
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


def _install_linux_deps(os_info: OSInfo, password):
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
    import shutil as _shutil
    if not _shutil.which("node"):
        info("Installing Node.js 22 LTS...")
        # Download setup script first, then execute (don't pipe curl to sudo)
        dl_result = run("curl -fsSL -o /tmp/nodesource_setup.sh https://deb.nodesource.com/setup_22.x")
        if dl_result.returncode == 0:
            sudo_run("bash /tmp/nodesource_setup.sh", password)
            sudo_run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs", password)
            run("rm -f /tmp/nodesource_setup.sh")
            ok("Node.js installed")
        else:
            warn("Failed to download Node.js setup script")
    else:
        ok("Node.js already installed")


def _configure_linux(os_info: OSInfo, password):
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

    if failed:
        warn(f"Failed to install: {', '.join(failed)}")
    ok(f"Homebrew packages: {installed_count}/{len(MACOS_BREW_PACKAGES)} installed")


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

    if _dry_run:
        print(f"\n{BOLD}=== OS Provisioning (DRY RUN — no changes will be made) ==={NC}")
    else:
        print(f"\n{BOLD}=== OS Provisioning ==={NC}")
    print(f"    Target: {os_info.display_name}")
    print(f"    Mode: {takeover}\n")

    try:
        if os_info.os_type == "linux":
            if takeover == "full":
                provision_linux_full(os_info, password)
            else:
                provision_linux_soft(os_info, password)
        elif os_info.os_type == "macos":
            if takeover == "full":
                provision_macos_full(os_info, password)
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
    parser.add_argument("--takeover", type=str, choices=["full", "soft"], default="full")
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
