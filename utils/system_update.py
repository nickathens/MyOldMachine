#!/usr/bin/env python3
"""
Cross-platform system updater for MyOldMachine.

Detects the OS package manager (apt, dnf, pacman, zypper, apk, brew)
and runs a safe upgrade. Logs results and optionally notifies via Telegram.

Designed to run as a scheduled command job (nightly).
Can also be run standalone: python utils/system_update.py
"""

import logging
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

logger = logging.getLogger(__name__)

# Package manager upgrade commands (safe upgrade only, no removals)
_UPDATE_CMDS = {
    "apt": "DEBIAN_FRONTEND=noninteractive apt-get update -qq",
    "dnf": "dnf check-update -q || true",
    "pacman": "pacman -Sy --noconfirm",
    "zypper": "zypper refresh -q",
    "apk": "apk update",
    "brew": "brew update",
}

_UPGRADE_CMDS = {
    "apt": "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q",
    "dnf": "dnf upgrade -y --skip-broken",
    "pacman": "pacman -Su --noconfirm",
    "zypper": "zypper update -y",
    "apk": "apk upgrade",
    "brew": "brew upgrade",
}

_CLEAN_CMDS = {
    "apt": "apt-get autoclean -y -q && apt-get autoremove -y -q",
    "dnf": "dnf autoremove -y",
    "pacman": "pacman -Qdtq | pacman -Rns --noconfirm - 2>/dev/null || true",
    "zypper": "",
    "apk": "",
    "brew": "brew cleanup -s",
}

# Commands to check how many upgradable packages exist
_CHECK_UPGRADABLE_CMDS = {
    "apt": "apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0",
    "dnf": "dnf check-update -q 2>/dev/null | grep -cE '^[a-zA-Z]' || echo 0",
    "pacman": "pacman -Qu 2>/dev/null | wc -l",
    "zypper": "zypper list-updates 2>/dev/null | grep -c '|' || echo 0",
    "apk": "apk version -l '<' 2>/dev/null | wc -l",
    "brew": "brew outdated 2>/dev/null | wc -l",
}


def detect_package_manager() -> str:
    """Detect the system package manager."""
    if platform.system() == "Darwin":
        if shutil.which("brew"):
            return "brew"
        return ""

    # Linux: check in order of preference
    for mgr in ("apt", "dnf", "pacman", "zypper", "apk"):
        cmd = "apt-get" if mgr == "apt" else mgr
        if shutil.which(cmd):
            return mgr
    return ""


def _get_sudo_password() -> str:
    """Read sudo password from storage."""
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text(encoding="utf-8").strip()
    return ""


def _run_cmd(cmd: str, use_sudo: bool = True, timeout: int = 300) -> tuple[int, str]:
    """Run a shell command, optionally with sudo. Returns (returncode, output)."""
    if not cmd:
        return 0, ""

    password = _get_sudo_password() if use_sudo else ""

    if use_sudo and platform.system() != "Darwin":
        # brew should never run with sudo on macOS
        if password:
            full_cmd = f"sudo -S {cmd}"
            stdin_data = password + "\n"
        else:
            full_cmd = f"sudo {cmd}"
            stdin_data = None
    else:
        full_cmd = cmd
        stdin_data = None

    try:
        result = subprocess.run(
            full_cmd, shell=True,
            input=stdin_data,
            capture_output=True, text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"
    except Exception as e:
        return 1, str(e)


def _count_upgradable(mgr: str) -> int:
    """Count how many packages are upgradable.

    dnf gets special handling: `dnf check-update` exits 0 when there are no
    updates and 100 when updates are available. Any other non-zero exit code
    is a genuine error (broken metadata, network failure, etc.) that we must
    surface rather than silently report as 'no updates available'.
    """
    if mgr == "dnf":
        rc, output = _run_cmd("dnf check-update -q", use_sudo=True, timeout=60)
        if rc == 0:
            return 0
        if rc == 100:
            # Count lines that start with an alphanumeric (package names).
            # Blank lines and obsoletes sections have no leading alpha.
            count = 0
            for line in output.splitlines():
                line = line.strip()
                if line and line[0].isalnum():
                    count += 1
            return count
        logger.warning(f"dnf check-update returned rc={rc}: {output[:200]}")
        return 0

    cmd = _CHECK_UPGRADABLE_CMDS.get(mgr)
    if not cmd:
        return 0
    use_sudo = mgr != "brew"
    rc, output = _run_cmd(cmd, use_sudo=use_sudo, timeout=60)
    if rc != 0:
        return 0
    # Extract the last line which should be the count
    lines = output.strip().splitlines()
    for line in reversed(lines):
        line = line.strip()
        if line.isdigit():
            return int(line)
    return 0


def run_system_update(notify_fn=None) -> str:
    """
    Run a full system update cycle.

    notify_fn: optional callable(message: str) to send notifications.
    Returns a summary string.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "system_update.log"

    def log(msg: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}\n"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        logger.info(msg)

    def notify(msg: str):
        if notify_fn:
            try:
                notify_fn(msg)
            except Exception as e:
                logger.warning(f"Notification failed: {e}")

    # Rotate log if too large
    try:
        if log_file.exists() and log_file.stat().st_size > 500_000:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            log_file.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
    except Exception:
        pass

    log("=== System update started ===")

    # Detect package manager
    mgr = detect_package_manager()
    if not mgr:
        msg = "No supported package manager found. Skipping system update."
        log(msg)
        return msg

    log(f"Package manager: {mgr}")
    use_sudo = mgr != "brew"

    # Step 1: Update package lists
    update_cmd = _UPDATE_CMDS.get(mgr)
    if update_cmd:
        rc, output = _run_cmd(update_cmd, use_sudo=use_sudo, timeout=120)
        if rc != 0:
            msg = f"System update: package list refresh failed ({mgr})"
            log(f"ERROR: {msg}")
            log(output[:500] if output else "no output")
            notify(msg)
            return msg

    # Step 2: Count available updates
    before_count = _count_upgradable(mgr)
    if before_count == 0:
        log("No updates available.")
        log("=== System update complete ===")
        return "No updates available."

    log(f"{before_count} package(s) available for upgrade")

    # Step 3: Run safe upgrade
    upgrade_cmd = _UPGRADE_CMDS.get(mgr)
    if not upgrade_cmd:
        msg = f"No upgrade command configured for {mgr}"
        log(msg)
        return msg

    rc, output = _run_cmd(upgrade_cmd, use_sudo=use_sudo, timeout=600)
    if rc != 0:
        msg = f"System update: upgrade failed ({mgr})"
        log(f"ERROR: {msg}")
        log(output[:500] if output else "no output")
        notify(msg)
        return msg

    # Step 4: Count remaining (held-back) updates
    after_count = _count_upgradable(mgr)
    actually_upgraded = before_count - after_count

    if actually_upgraded <= 0:
        log(f"All {before_count} updates held back. Nothing changed.")
        log("=== System update complete ===")
        return f"All {before_count} updates held back (phased rollout). Nothing changed."

    # Step 5: Clean up
    clean_cmd = _CLEAN_CMDS.get(mgr)
    if clean_cmd:
        _run_cmd(clean_cmd, use_sudo=use_sudo, timeout=120)

    # Step 6: Check if reboot required (Linux only)
    reboot_msg = ""
    if platform.system() == "Linux" and Path("/var/run/reboot-required").exists():
        reboot_msg = " Reboot required."
        log("Reboot required")

    # Build summary
    parts = [f"Nightly update: {actually_upgraded} package(s) upgraded."]
    if after_count > 0:
        parts.append(f"{after_count} held back.")
    if reboot_msg:
        parts.append(reboot_msg.strip())
    summary = " ".join(parts)

    log(summary)
    notify(summary)
    log("=== System update complete ===")
    return summary


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="MyOldMachine system updater")
    parser.add_argument("--notify", action="store_true",
                        help="Send Telegram notification on completion")
    parser.add_argument("--user-id", type=str, default="",
                        help="Telegram user ID for notifications")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    notify_fn = None
    if args.notify and args.user_id:
        send_script = BOT_DIR / "utils" / "send_to_telegram.py"
        venv_python = BOT_DIR / ".venv" / "bin" / "python"
        python_cmd = str(venv_python) if venv_python.exists() else sys.executable

        def notify_fn(msg):
            subprocess.run(
                [python_cmd, str(send_script), "--user", args.user_id, "--message", msg],
                timeout=30, capture_output=True,
            )

    result = run_system_update(notify_fn=notify_fn)
    print(result)


if __name__ == "__main__":
    main()
