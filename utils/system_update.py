#!/usr/bin/env python3
"""
Cross-platform system updater for MyOldMachine.

Detects the OS package manager (apt, dnf, pacman, zypper, apk, brew)
and runs a safe upgrade. Logs results and optionally notifies via Telegram.

Designed to run as a scheduled command job (nightly).
Can also be run standalone: python utils/system_update.py
"""

import json
import logging
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

# The nightly job runs this file directly (bot.py schedules
# `python utils/system_update.py`), which puts utils/ on sys.path rather than
# the repo root, so intra-repo imports need the root added first.
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from utils.app_updates import AppCheckResult, run_app_update_check  # noqa: E402

# Remembers which app updates the user has already been told about, so the
# nightly reminder repeats weekly instead of every single night.
PENDING_APPS_STATE = DATA_DIR / "pending_app_updates.json"
REMIND_AFTER_DAYS = 7

# Cap on how many cask names go into one summary line before it turns into
# "and N more" — the summary is a Telegram message, not a report.
_MAX_NAMED_CASKS = 6

# Shape of a Homebrew cask token: lowercase, no spaces, no punctuation beyond
# the separators brew itself uses (`font-fira-code`, `python@3.12`,
# `homebrew/cask-versions/firefox-beta`). Used to reject anything brew prints
# that is not an app name — see _outdated_casks.
_CASK_TOKEN = re.compile(r"[a-z0-9][a-z0-9@._+/-]*")

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
    # Formulae only: cask upgrades swap .app bundles, which hangs forever in a
    # headless launchd session (no GUI to mediate the trash/replace step) and a
    # timeout kill then strands the cask half-installed (Blender, 2026-07-15..18).
    # Outdated casks are reported in the summary instead — upgrade them
    # interactively.
    "brew": "brew upgrade --formula",
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
    "brew": "brew outdated --formula 2>/dev/null | wc -l",
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
    """Read sudo password from storage. Delegates to install.sudo."""
    if str(BOT_DIR) not in sys.path:
        sys.path.insert(0, str(BOT_DIR))
    from install.sudo import get_sudo_password
    return get_sudo_password() or ""


def _run_cmd(
    cmd: str,
    use_sudo: bool = True,
    timeout: int = 300,
    force_sudo_on_darwin: bool = False,
) -> tuple[int, str]:
    """Run a shell command, optionally with sudo. Returns (returncode, output).

    On Darwin, sudo is suppressed by default (so brew never runs as root even
    if a caller forgets to pass use_sudo=False). Set force_sudo_on_darwin=True
    for tools that genuinely require root on macOS — currently only the
    `softwareupdate` install path.
    """
    if not cmd:
        return 0, ""

    password = _get_sudo_password() if use_sudo else ""
    is_darwin = platform.system() == "Darwin"
    sudo_active = use_sudo and (not is_darwin or force_sudo_on_darwin)

    if sudo_active:
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


def _outdated_casks() -> tuple[str, ...]:
    """Names of casks with a pending upgrade, or () when none.

    Deliberately NOT --greedy. Greedy also lists casks marked `auto_updates`,
    which ship their own updater: those apps upgrade themselves on disk while
    Homebrew's Caskroom metadata stays pinned to the old version. Reporting
    them would mean nagging the user about updates that already happened
    (ProtonVPN was live on 6.5.1 while brew still recorded 6.5.0, 2026-07-25).

    stderr is discarded and every line is checked against _CASK_TOKEN because
    _run_cmd merges stdout and stderr. brew writes progress chatter to stderr
    with rc == 0 when it refreshes its API cache — and since the nightly job
    runs 24h apart, it hits that refresh almost every time:

        $ brew outdated --cask --quiet     # stdout empty, stderr:
        ✔︎ JSON API packages.arm64_tahoe.jws.json

    Untreated that line became a phantom pending "app", and because the text
    varies between nights the app set kept changing, so _should_remind_pending
    saw news every night and REMIND_AFTER_DAYS could never throttle it.
    """
    rc, output = _run_cmd(
        "brew outdated --cask --quiet 2>/dev/null", use_sudo=False, timeout=60
    )
    if rc != 0:
        return ()
    names = [
        line.strip() for line in output.splitlines()
        if _CASK_TOKEN.fullmatch(line.strip())
    ]
    return tuple(sorted(names))


def _brew_cask_note(casks: tuple[str, ...]) -> str:
    """One-line note about outdated casks, or "" when none.

    Casks are excluded from the nightly upgrade (see _UPGRADE_CMDS) so pending
    app updates would otherwise go silent. Names them rather than counting them:
    "libreoffice is waiting" is actionable, "1 app update pending" is not.
    """
    if not casks:
        return ""
    shown = list(casks[:_MAX_NAMED_CASKS])
    if len(casks) > _MAX_NAMED_CASKS:
        shown.append(f"and {len(casks) - _MAX_NAMED_CASKS} more")
    return (
        f"{len(casks)} app update(s) waiting on you, not installed "
        f"automatically: {', '.join(shown)}. Ask me to update them."
    )


def _load_pending_state() -> dict:
    """Read the pending-apps reminder state. Returns {} if missing or unreadable."""
    try:
        if not PENDING_APPS_STATE.exists():
            return {}
        data = json.loads(PENDING_APPS_STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"pending-apps state unreadable: {e}")
        return {}


def _record_pending_notice(casks: tuple[str, ...], key: str = "apps") -> None:
    """Remember that the user has just been told about exactly these apps.

    key namespaces one waiting-list from another inside the same state file.
    Casks and out-of-band apps (Resolve, CLIs) are found by different checks
    and move on different clocks, so sharing one timestamp would let a new
    cask silently reset the reminder for a Resolve that has been waiting weeks.
    """
    try:
        state = _load_pending_state()
        state[key] = list(casks)
        state[f"{key}_last_notified"] = datetime.now().strftime("%Y-%m-%d")
        # Legacy shape: the cask list used to sit under "apps" with a bare
        # "last_notified" beside it. Keep writing that so a downgrade of this
        # file still reads its own state instead of re-nagging from scratch.
        if key == "apps":
            state["last_notified"] = state[f"{key}_last_notified"]
        PENDING_APPS_STATE.parent.mkdir(parents=True, exist_ok=True)
        PENDING_APPS_STATE.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        logger.warning(f"pending-apps state not saved: {e}")


def _should_remind_pending(casks: tuple[str, ...], key: str = "apps") -> bool:
    """True when a quiet night should still ping the user about waiting apps.

    Reminds when the set of waiting apps changed (something new appeared) or
    when the last reminder is at least REMIND_AFTER_DAYS old. Any unparseable
    state fails toward reminding: a silent pending update is the bug being
    fixed here, a duplicate reminder is merely noise.
    """
    if not casks:
        return False
    state = _load_pending_state()
    if set(state.get(key) or []) != set(casks):
        return True
    last = state.get(f"{key}_last_notified")
    if not isinstance(last, str) and key == "apps":
        last = state.get("last_notified")  # state written before keys existed
    if not isinstance(last, str):
        return True
    try:
        last_date = datetime.strptime(last, "%Y-%m-%d").date()
    except ValueError:
        return True
    return (datetime.now().date() - last_date).days >= REMIND_AFTER_DAYS


def _count_softwareupdate_available() -> int:
    """Count how many Apple updates `softwareupdate -l` reports as available.

    Returns 0 if no updates, the parser fails, or this is not macOS.
    softwareupdate -l does NOT require admin auth, so no sudo here.
    """
    if platform.system() != "Darwin":
        return 0
    rc, output = _run_cmd("softwareupdate -l 2>&1", use_sudo=False, timeout=120)
    # rc is non-zero in some macOS versions even on success; rely on output text.
    if "No new software available" in output or "No updates" in output:
        return 0
    # Lines describing updates start with "* Label:" (newer) or "   * " (older).
    # Counting either is more reliable than a single regex across macOS versions.
    count = 0
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("* "):
            count += 1
    return count


def _run_macos_softwareupdate(
    auto_restart: bool,
    log_fn,
    notify_fn,
) -> str:
    """Install pending Apple updates via `softwareupdate -i -a`.

    Always opt-in — caller must check the maintenance config flag first.
    Returns a one-line summary suitable for appending to the nightly digest.
    """
    if platform.system() != "Darwin":
        return ""

    available = _count_softwareupdate_available()
    if available == 0:
        log_fn("softwareupdate: no Apple updates available")
        return ""

    log_fn(f"softwareupdate: {available} Apple update(s) available")

    # Install. --agree-to-license suppresses the per-update license prompt that
    # would otherwise hang the unattended job. --restart only on explicit opt-in.
    install_cmd = "softwareupdate -i -a --agree-to-license"
    if auto_restart:
        install_cmd += " --restart"

    rc, output = _run_cmd(install_cmd, use_sudo=True, force_sudo_on_darwin=True, timeout=3600)
    if rc != 0:
        msg = f"softwareupdate: install failed (rc={rc})"
        log_fn(f"ERROR: {msg}")
        log_fn(output[-500:] if output else "no output")
        notify_fn(msg)
        return msg

    after = _count_softwareupdate_available()
    installed = max(0, available - after)
    summary = f"softwareupdate: {installed} Apple update(s) installed."
    if after > 0:
        summary += f" {after} still pending."
    if auto_restart:
        # We can't reliably tell from the output whether a reboot was actually
        # triggered (substring matching on "restart" false-positives on lines
        # like "no restart required"). Just note the user's opt-in.
        summary += " (auto-restart enabled)"
    log_fn(summary)
    return summary


class PkgUpdateResult(NamedTuple):
    """Outcome of one package-manager cycle.

    summary: one-line digest, "" when there was nothing to say.
    errored: True when notify_fn already fired with a failure message.
    pending_apps: apps that need the user's go-ahead (never auto-installed).
    """
    summary: str
    errored: bool
    pending_apps: tuple[str, ...] = ()


def _run_pkg_manager_update(log_fn, notify_fn) -> PkgUpdateResult:
    """Run the OS package manager update cycle (apt/dnf/pacman/zypper/apk/brew).

    When errored=True, notify_fn has already been called with the failure
    message — the caller should not notify again. Empty summary means 'no
    package manager available or nothing to do'.
    """
    mgr = detect_package_manager()
    if not mgr:
        log_fn("No supported package manager found.")
        return PkgUpdateResult("", False)

    log_fn(f"Package manager: {mgr}")
    use_sudo = mgr != "brew"

    # Step 1: Update package lists
    update_cmd = _UPDATE_CMDS.get(mgr)
    if update_cmd:
        rc, output = _run_cmd(update_cmd, use_sudo=use_sudo, timeout=120)
        if rc != 0:
            msg = f"System update: package list refresh failed ({mgr})"
            log_fn(f"ERROR: {msg}")
            log_fn(output[-500:] if output else "no output")
            notify_fn(msg)
            return PkgUpdateResult(msg, True)

    # Step 2: Count available updates
    before_count = _count_upgradable(mgr)
    pending_apps = _outdated_casks() if mgr == "brew" else ()
    cask_note = _brew_cask_note(pending_apps)
    if cask_note:
        log_fn(cask_note)
    if before_count == 0:
        log_fn("No updates available.")
        return PkgUpdateResult(cask_note, False, pending_apps)

    log_fn(f"{before_count} package(s) available for upgrade")

    # Step 3: Run safe upgrade
    upgrade_cmd = _UPGRADE_CMDS.get(mgr)
    if not upgrade_cmd:
        msg = f"No upgrade command configured for {mgr}"
        log_fn(msg)
        return PkgUpdateResult(msg, False, pending_apps)

    # 30 min: big cask downloads (e.g. Blender ~400MB) can exceed 10 min, and a
    # timeout kill mid-upgrade strands the cask half-installed (2026-07-15).
    rc, output = _run_cmd(upgrade_cmd, use_sudo=use_sudo, timeout=1800)
    if rc != 0:
        msg = f"System update: upgrade failed ({mgr})"
        log_fn(f"ERROR: {msg}")
        log_fn(output[-500:] if output else "no output")
        notify_fn(msg)
        return PkgUpdateResult(msg, True, pending_apps)

    # Step 4: Count remaining (held-back) updates
    after_count = _count_upgradable(mgr)
    actually_upgraded = before_count - after_count

    if actually_upgraded <= 0:
        log_fn(f"All {before_count} updates held back. Nothing changed.")
        held = f"All {before_count} updates held back (phased rollout). Nothing changed."
        if cask_note:
            held = f"{held} {cask_note}"
        return PkgUpdateResult(held, False, pending_apps)

    # Step 5: Clean up
    clean_cmd = _CLEAN_CMDS.get(mgr)
    if clean_cmd:
        _run_cmd(clean_cmd, use_sudo=use_sudo, timeout=120)

    # Step 6: Check if reboot required (Linux only)
    reboot_msg = ""
    if platform.system() == "Linux" and Path("/var/run/reboot-required").exists():
        reboot_msg = " Reboot required."
        log_fn("Reboot required")

    # Build summary
    parts = [f"Nightly update: {actually_upgraded} package(s) upgraded."]
    if after_count > 0:
        parts.append(f"{after_count} held back.")
    if reboot_msg:
        parts.append(reboot_msg.strip())
    if cask_note:
        parts.append(cask_note)
    summary = " ".join(parts)
    log_fn(summary)
    return PkgUpdateResult(summary, False, pending_apps)


def _maybe_run_macos_softwareupdate(log_fn, notify_fn) -> str:
    """Lazily load maintenance config and run softwareupdate if opted in.

    Always returns "" on non-Darwin or when the flag is off. Errors loading
    the config are logged but never propagated — softwareupdate is purely
    additive to the package-manager flow.
    """
    if platform.system() != "Darwin":
        return ""
    try:
        if str(BOT_DIR) not in sys.path:
            sys.path.insert(0, str(BOT_DIR))
        from utils.maintenance import load_config as _load_maintenance
        cfg = _load_maintenance()
    except Exception as e:
        log_fn(f"softwareupdate skipped: maintenance config load failed: {e}")
        return ""
    if not cfg.get("macos_system_updates"):
        return ""
    return _run_macos_softwareupdate(
        auto_restart=bool(cfg.get("macos_system_updates_restart", False)),
        log_fn=log_fn,
        notify_fn=notify_fn,
    )


def _maybe_run_app_update_check(log_fn):
    """Check the apps no package manager tracks, and install the safe ones.

    Resolve, Claude Code and the global npm CLIs the skills install are all
    outside apt/brew, so before this ran nothing on the machine had ever looked
    at their versions — the nightly job reported "no updates available" on a
    box carrying four stale CLIs and a stale Resolve (2026-08-04).

    Never propagates: a version check is additive, and a flaky download feed
    must not fail the night's package upgrade.
    """
    try:
        # sys.path already carries the repo root: the module-level import of
        # app_updates would have failed at import time otherwise.
        from utils.maintenance import load_config as _load_maintenance
        cfg = _load_maintenance()
    except Exception as e:
        log_fn(f"app update check skipped: maintenance config load failed: {e}")
        return AppCheckResult()
    if not cfg.get("app_update_checks", True):
        return AppCheckResult()
    try:
        return run_app_update_check(
            auto_update=bool(cfg.get("app_auto_update", True)),
            log_fn=log_fn,
        )
    except Exception as e:
        log_fn(f"app update check failed: {e}")
        return AppCheckResult()


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

    pkg = _run_pkg_manager_update(log_fn=log, notify_fn=notify)

    # Apple updates run independently of the package manager flow, but only
    # after a clean (non-error) outcome — a failing brew shouldn't trigger
    # softwareupdate that night.
    sw_summary = ""
    apps = AppCheckResult()
    if not pkg.errored:
        sw_summary = _maybe_run_macos_softwareupdate(log_fn=log, notify_fn=notify)
        # Runs even when the package manager had nothing to do: the whole point
        # is that these apps are invisible to it, so a quiet brew night says
        # nothing at all about them.
        apps = _maybe_run_app_update_check(log_fn=log)

    log("=== System update complete ===")

    if pkg.errored:
        # notify() already fired inside the helper.
        return pkg.summary

    parts = [s for s in (pkg.summary, sw_summary, apps.summary) if s.strip()]
    if not parts:
        return "No updates available."

    full = " ".join(parts)

    # Notify when something material happened. Phrases like "No updates
    # available." or "All N updates held back" are status-only; "upgraded",
    # "installed", or an app that actually moved mean the user should know.
    material = (
        "upgraded" in pkg.summary
        or "installed" in sw_summary
        or apps.news
    )

    # ...but an app waiting on the user's go-ahead also has to reach them. It is
    # never auto-installed, so on a quiet night the old contract logged it and
    # told nobody, and it could sit unseen for days (LibreOffice, 2026-07-25).
    # Throttled so a standing update reminds weekly, not nightly. Casks and
    # out-of-band apps are throttled apart: Resolve can wait weeks behind a
    # registration form, and a new cask appearing must not reset its clock.
    remind_casks = _should_remind_pending(pkg.pending_apps)
    remind_apps = _should_remind_pending(apps.waiting, key="out_of_band")
    remind = not material and (remind_casks or remind_apps)

    if material or remind:
        notify(full)
        # Record even on a material night: the digest carries the pending lines
        # too, so that ping starts the clock for both next reminders.
        _record_pending_notice(pkg.pending_apps)
        _record_pending_notice(apps.waiting, key="out_of_band")

    return full


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
