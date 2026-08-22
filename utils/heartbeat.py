#!/usr/bin/env python3
"""External heartbeat ping for MyOldMachine (opt-in dead-man's-switch).

An always-on assistant that goes dark should not do so silently. This script
sends a short "I am alive" ping to a URL you configure (HEARTBEAT_URL, for
example a free healthchecks.io check). Schedule it to run on an interval: as
long as it keeps pinging, the external monitor stays quiet, and the moment the
pings stop (bot frozen, machine down, network gone) the monitor alerts you.

Why external: the in-machine hardware watchdog can reboot a frozen box, but it
cannot tell you anything happened. This closes that gap. The two are
complementary, not alternatives.

Opt-in and safe by default: with HEARTBEAT_URL unset this script does nothing
and exits 0, so it is harmless to ship disabled. It uses only the standard
library, sends no payload beyond the ping, and never raises. A failed ping is
logged and swallowed so a scheduled run never marks itself failed on a transient
network blip; detecting missing pings is the monitor's job, not this script's.

Enable it (see docs/heartbeat.md for the full guide):
  1. Create a check on your monitor and copy its ping URL.
  2. Add HEARTBEAT_URL=<that url> to your .env.
  3. Schedule this script on an interval shorter than the monitor's grace
     period. Re-run the installer and answer yes to "Down alert (external
     heartbeat)" and it installs the schedule for you (a systemd timer on
     Linux, a launchd agent on macOS), gated with --require-service. The
     manual equivalents are in docs/heartbeat.md.

Usage:
  python utils/heartbeat.py            # ping HEARTBEAT_URL from the environment
  python utils/heartbeat.py --url URL  # ping an explicit URL (overrides env)
  python utils/heartbeat.py --timeout 10
  python utils/heartbeat.py --require-service myoldmachine.service
"""
import argparse
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BOT_DIR / ".env")

sys.path.insert(0, str(BOT_DIR))
from core.config import get_heartbeat_url  # noqa: E402


# ─── Optional gate: only ping while the bot itself is up ─────────────
#
# An ungated timer keeps pinging while the bot is dead, so the monitor stays
# green and only ever catches "machine or network down". Gating the ping on
# the bot's own service turns the same monitor into a bot-down alert too.
#
# The gate lives here, in the script, rather than in the unit file on purpose.
# systemd's Requisite=/BindsTo= would make the ping unit FAIL every time the
# bot is down, filling `systemctl --failed` with noise during exactly the
# outage you are trying to hear about, and launchd has no equivalent at all.
# A skip that exits 0 is silent on both platforms.


def _systemd_unit_active(unit: str) -> Optional[bool]:
    """True when systemd reports `unit` active, False when not, None if unknown."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            capture_output=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        # No systemd on this host (or it did not answer): cannot tell.
        return None
    # `is-active --quiet` exits 0 for active and non-zero for every other
    # state (inactive, failed, activating, unknown unit).
    return result.returncode == 0


def _launchd_job_running(label: str) -> Optional[bool]:
    """True when launchd reports a live PID for `label`, False when not.

    `launchctl list <label>` exits non-zero when the job is not loaded at all,
    and on success prints a plist-ish dict that carries a `"PID" = <n>;` line
    only while the job actually has a process. A loaded job with no PID is a
    crashed or throttled job, which is "not running" for our purposes.
    """
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return False  # not loaded
    return '"PID"' in (result.stdout or "")


def bot_is_running(service: str) -> Optional[bool]:
    """Is the bot's service up? True / False / None when it cannot be told.

    None is deliberately distinct from False: an unknown state must not be
    reported as "down". See main() for how the caller resolves it.
    """
    if not service:
        return None
    system = platform.system()
    if system == "Darwin":
        return _launchd_job_running(service)
    if system == "Linux":
        return _systemd_unit_active(service)
    return None


def send_ping(url: str, timeout: float = 10.0) -> bool:
    """GET the ping URL. Return True on a 2xx response, False on any failure.

    Never raises: any network, URL, or socket error is logged and reported as a
    failed ping so the caller can exit cleanly.
    """
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "mom-heartbeat/1"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            return code is not None and 200 <= code < 300
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"heartbeat: ping failed: {e}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Send an opt-in external heartbeat ping."
    )
    parser.add_argument(
        "--url", default=None, help="Ping URL (overrides HEARTBEAT_URL)."
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="Request timeout in seconds."
    )
    parser.add_argument(
        "--require-service",
        default=None,
        metavar="NAME",
        help=(
            "Only ping while this service is running, so a dead bot stops the "
            "pings and the monitor alerts. NAME is a systemd unit on Linux "
            "(myoldmachine.service) or a launchd label on macOS "
            "(com.myoldmachine.bot)."
        ),
    )
    args = parser.parse_args(argv)

    url = (args.url or get_heartbeat_url() or "").strip()
    if not url:
        # Opt-in: nothing configured, nothing to do. This is not an error.
        print("heartbeat: HEARTBEAT_URL not set; heartbeat disabled (no-op).")
        return 0

    if args.require_service:
        running = bot_is_running(args.require_service)
        if running is False:
            # The dead-man's-switch doing its job: stay quiet and let the
            # monitor notice the missing pings.
            print(
                f"heartbeat: {args.require_service} is not running; "
                "skipping ping (this is the alert)."
            )
            return 0
        if running is None:
            # Fail open. Pinging anyway degrades to machine-and-network
            # monitoring, which is what an ungated schedule gives you. Failing
            # closed would page the operator every interval on a host whose
            # service manager we simply cannot read, and an alerting system
            # that cries wolf gets muted.
            print(
                f"heartbeat: cannot determine whether {args.require_service} "
                "is running; pinging anyway.",
                file=sys.stderr,
            )

    ok = send_ping(url, timeout=args.timeout)
    print(f"heartbeat: {'ok' if ok else 'ping failed'}")
    # Always exit 0: the monitor detects missing pings, so a single failed ping
    # is not a reason to mark a scheduled job failed and spam the operator.
    return 0


if __name__ == "__main__":
    sys.exit(main())
