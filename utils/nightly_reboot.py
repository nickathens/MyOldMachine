#!/usr/bin/env python3
"""
Cross-platform nightly reboot for MyOldMachine.

Opt-in, OFF by default. When the admin enables it (`/maintenance reboot on`),
the bot reboots the machine once a night at a fixed time (default 05:00),
AFTER the whole nightly maintenance chain has finished: backup 02:00,
reflection 03:00, cleanup 03:30, system update 04:00, probe/check/report
04:30-04:45. Applying updates that need a restart is the point; a nightly
reboot also clears the slow resource creep that builds up on an always-on box.

Runs as a scheduled command job. Safe by construction -- four gates, in order:

  1. Config gate. Does nothing unless the admin has opted in.
  2. Misfire-window guard. Only fires inside its intended early-morning window.
     A job missed while the bot was down (APScheduler keeps a 24h misfire grace
     with coalesce) can therefore never reboot the machine hours late, mid-work.
  3. Boot-recency guard. Skips if the machine booted in the last hour. This is
     an anti-loop: a missed job that fires right after a fresh boot, or a second
     reboot on top of one an update already triggered, is redundant and skipped.
  4. Stranding guard. Refuses to reboot -- and alerts the admin -- unless it can
     confirm the bot comes back automatically after boot: an ENABLED systemd
     service on Linux, or a boot-launched LaunchDaemon / auto-login on macOS.
     This is the guard that prevents a reboot leaving the machine at a login
     screen with the bot dead and no way back in.

Standalone use:
  python utils/nightly_reboot.py --dry-run   # log the decision, never reboot
  python utils/nightly_reboot.py --force     # skip the misfire-window guard
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).parent.parent
# The scheduler runs this as `python utils/nightly_reboot.py` with cwd=$HOME, so
# the repo root is not on sys.path by default. Put it there so `utils.*` and
# `install.*` imports resolve whether run as a job, standalone, or under tests.
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

DATA_DIR = BOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "nightly_reboot.log"
STATE_FILE = DATA_DIR / "nightly_reboot_state.json"

# The systemd unit / launchd label the installer registers (install/service.py).
BOT_SERVICE = "myoldmachine"
BOT_LABEL = "com.myoldmachine.bot"

# Misfire window: fire only from WINDOW_BEFORE min before the target time to
# WINDOW_AFTER min after it. Wide enough to absorb a slow update run finishing,
# tight enough that a late misfire never reboots hours into the working day.
WINDOW_BEFORE_MIN = 5
WINDOW_AFTER_MIN = 30

# A machine with less than this uptime already applied any restart-needing
# update on that boot, so a reboot now is redundant.
MIN_UPTIME_SECONDS = 3600

# Do not re-alert the admin about the same actionable failure more than once in
# this many days. The job runs nightly, so an unthrottled refusal would spam.
ALERT_THROTTLE_DAYS = 7

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a read-only check command (no shell, no sudo). Returns (rc, output).

    Never raises: a missing binary or timeout returns a non-zero code so the
    caller treats the check as "could not confirm" rather than crashing.
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _log(line: str) -> None:
    """Append a timestamped line to the reboot log (best-effort)."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} | {line}\n")
    except OSError as e:
        logger.warning(f"nightly_reboot: could not write log: {e}")
    logger.info(f"nightly_reboot: {line}")


def _uptime_seconds() -> float | None:
    """Seconds since boot, cross-platform. None if it cannot be determined."""
    system = platform.system()
    if system == "Linux":
        try:
            return float(Path("/proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None
    if system == "Darwin":
        # `sysctl -n kern.boottime` -> "{ sec = 1699999999, usec = 12345 } ..."
        rc, out = _run(["sysctl", "-n", "kern.boottime"])
        if rc != 0:
            return None
        try:
            sec_token = out.split("sec")[1].split("=")[1].split(",")[0].strip()
            return max(0.0, datetime.now().timestamp() - float(sec_token))
        except (IndexError, ValueError):
            return None
    return None


def _within_window(now: datetime, hour: int, minute: int) -> bool:
    """True if `now` is inside the reboot window around the target time."""
    target = hour * 60 + minute
    now_min = now.hour * 60 + now.minute
    return (target - WINDOW_BEFORE_MIN) <= now_min <= (target + WINDOW_AFTER_MIN)


def _bot_returns_after_reboot() -> tuple[bool, str]:
    """Can we confirm the bot restarts automatically after a reboot?

    Returns (safe, reason). Fails CLOSED: if we cannot positively confirm the
    bot comes back, we return False so the caller refuses to reboot. Stranding
    the machine is far worse than skipping a night's reboot.
    """
    system = platform.system()

    if system == "Linux":
        # A system service is only guaranteed to start on boot when it is
        # "enabled" (wanted by a boot target). "enabled-runtime", "static",
        # "disabled", "masked" or a missing unit do NOT survive a reboot.
        rc, out = _run(["systemctl", "is-enabled", BOT_SERVICE])
        state = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if rc == 0 and state == "enabled":
            return True, f"systemd service '{BOT_SERVICE}' is enabled"
        return False, (
            f"systemd service '{BOT_SERVICE}' is not enabled "
            f"(is-enabled: {state or 'not found'}); it would not start on boot"
        )

    if system == "Darwin":
        # A LaunchDaemon in /Library/LaunchDaemons starts at boot regardless of
        # login. A LaunchAgent (how the installer registers the bot) only loads
        # inside a GUI login session, so it survives a reboot only when
        # auto-login is on -- otherwise the box lands at the login screen with
        # the bot dead.
        if Path(f"/Library/LaunchDaemons/{BOT_LABEL}.plist").exists():
            return True, "LaunchDaemon present (starts at boot regardless of login)"
        rc, out = _run(
            ["defaults", "read",
             "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"]
        )
        user = out.strip() if rc == 0 else ""
        if user:
            return True, f"auto-login enabled for '{user}' (LaunchAgent reloads on boot)"
        return False, (
            "macOS LaunchAgent with no auto-login: after a reboot the bot would "
            "not restart until someone logs in at the machine"
        )

    return False, f"unsupported platform '{system}': cannot confirm the bot restarts"


def _reboot_reason() -> str:
    """Human-readable reason for the reboot, for the log only (never a gate)."""
    if platform.system() == "Linux":
        flag = Path("/var/run/reboot-required")
        if flag.exists():
            try:
                pkgs = Path("/var/run/reboot-required.pkgs").read_text().split()
                extra = f" (pkgs: {' '.join(sorted(set(pkgs)))})" if pkgs else ""
            except OSError:
                extra = ""
            return f"REQUIRED: reboot-required flag set{extra}"
        running = platform.release()
        newest = _newest_installed_kernel()
        if newest and running != newest:
            return f"REQUIRED: kernel changed (running {running}, installed {newest})"
        return "not strictly required (nightly stability reboot)"
    # macOS exposes no reliable "reboot required" signal; the nightly cadence
    # is the reason.
    return "nightly stability reboot"


def _newest_installed_kernel() -> str:
    """Newest vmlinuz-* under /boot, or '' if none/unreadable (Linux only)."""
    try:
        names = [p.name[len("vmlinuz-"):] for p in Path("/boot").glob("vmlinuz-*")]
    except OSError:
        return ""
    if not names:
        return ""
    # Sort by dotted-numeric version where possible, lexically otherwise.
    def key(v: str):
        parts = []
        for chunk in v.replace("-", ".").split("."):
            parts.append((1, int(chunk)) if chunk.isdigit() else (0, chunk))
        return parts
    return sorted(names, key=key)[-1]


def _reboot_command() -> str:
    """The shell command that reboots this platform (run under sudo)."""
    if platform.system() == "Darwin":
        return "shutdown -r now"
    return "systemctl reboot"


def _do_reboot() -> subprocess.CompletedProcess:
    """Actually reboot, via the shared sudo helper. Overridable in tests."""
    if str(BOT_DIR) not in sys.path:
        sys.path.insert(0, str(BOT_DIR))
    from install.sudo import get_sudo_password, sudo_run
    if platform.system() == "Linux":
        subprocess.run(["sync"], check=False)  # flush before the box goes down
    return sudo_run(_reboot_command(), get_sudo_password(), timeout=30)


# --------------------------------------------------------------------------- #
# alert throttling
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _should_alert(reason_key: str, now: datetime) -> bool:
    """True if the admin has not been alerted about `reason_key` recently.

    Records `now` as the last-alert time for that key when it returns True, so
    a persistent misconfiguration pings at most once per ALERT_THROTTLE_DAYS
    instead of every single night.
    """
    state = _load_state()
    last = state.get(f"alert_{reason_key}")
    if last:
        try:
            if datetime.fromisoformat(last) > now - timedelta(days=ALERT_THROTTLE_DAYS):
                return False
        except ValueError:
            pass
    state[f"alert_{reason_key}"] = now.isoformat()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"nightly_reboot: could not write state: {e}")
    return True


def _alert(user_id: int, message: str) -> None:
    """Best-effort Telegram alert to the admin via send_to_telegram.py."""
    if not user_id:
        return
    script = BOT_DIR / "utils" / "send_to_telegram.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--user", str(user_id), "--message", message],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"nightly_reboot: alert failed: {e}")


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def run(dry_run: bool = False, force: bool = False, user_id: int = 0,
        now: datetime | None = None, reboot_fn=None) -> str:
    """Decide whether to reboot and act. Returns a short decision code.

    Codes: disabled, misfire, recent-boot, stranded, dry-run, rebooted,
    reboot-failed. The seams (now, reboot_fn) and the module-level guards keep
    this fully testable without ever rebooting a real machine.
    """
    from utils.maintenance import load_config

    now = now or datetime.now()
    reboot_fn = reboot_fn or _do_reboot
    config = load_config()

    # Gate 1: config. Silent -- being off is not a problem to report.
    if not config.get("nightly_reboot", False):
        _log("SKIPPED: nightly reboot disabled in maintenance config")
        return "disabled"

    hour = int(config.get("nightly_reboot_hour", 5))
    minute = int(config.get("nightly_reboot_minute", 0))

    # Gate 2: misfire window (bypassed by --force for manual runs).
    if not dry_run and not force and not _within_window(now, hour, minute):
        _log(f"SKIPPED: fired {now:%H:%M} outside window around "
             f"{hour:02d}:{minute:02d} (misfire), no reboot")
        return "misfire"

    # Gate 3: boot recency (fails open -- reboots -- if uptime is unreadable).
    if not dry_run:
        up = _uptime_seconds()
        if up is not None and up < MIN_UPTIME_SECONDS:
            _log(f"SKIPPED: booted {int(up)}s ago (under "
                 f"{MIN_UPTIME_SECONDS // 60} min), reboot redundant")
            return "recent-boot"

    # Gate 4: stranding guard. This one alerts, because it is actionable.
    safe, why = _bot_returns_after_reboot()
    if not safe:
        _log(f"REFUSED: would strand the machine -- {why}")
        if not dry_run and _should_alert("stranded", now):
            _alert(user_id,
                   "Nightly reboot was skipped to avoid stranding this machine: "
                   f"{why}. The reboot will stay disabled in practice until the "
                   "bot is set to start automatically after boot.")
        return "stranded"

    reason = _reboot_reason()
    up = _uptime_seconds()
    up_str = f"up {int(up)}s" if up is not None else "up ?"
    line = f"{up_str} | return-safe: {why} | {reason}"

    if dry_run:
        _log(f"[DRY-RUN] would reboot -- {line} | cmd: sudo {_reboot_command()}")
        return "dry-run"

    _log(f"REBOOTING -- {line}")
    result = reboot_fn()
    if result is not None and getattr(result, "returncode", 0) != 0:
        err = (getattr(result, "stderr", "") or "").strip()[:200]
        _log(f"FAILED: reboot command returned {result.returncode}: {err}")
        if _should_alert("reboot-failed", now):
            _alert(user_id,
                   "Nightly reboot FAILED: the reboot command could not run "
                   f"({err or 'see nightly_reboot.log'}). The machine was not "
                   "rebooted. Check that the bot can sudo reboot on this box.")
        return "reboot-failed"
    return "rebooted"


def main() -> int:
    parser = argparse.ArgumentParser(description="MyOldMachine nightly reboot")
    parser.add_argument("--dry-run", action="store_true",
                        help="log the decision but never reboot")
    parser.add_argument("--force", action="store_true",
                        help="skip the misfire-window guard (manual runs)")
    parser.add_argument("--user-id", type=int, default=0,
                        help="admin Telegram id for failure/refusal alerts")
    args = parser.parse_args()
    code = run(dry_run=args.dry_run, force=args.force, user_id=args.user_id)
    print(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
