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
     period. Simplest, cross-platform, via the bot's own scheduler:
       /schedule every 2 minutes | <venv-python> <repo>/utils/heartbeat.py
     Because the bot's scheduler stops firing when the bot stops, the pings stop
     with it: that is the dead-man's-switch. A systemd timer alternative that
     survives a bot crash is in docs/heartbeat.md.

Usage:
  python utils/heartbeat.py            # ping HEARTBEAT_URL from the environment
  python utils/heartbeat.py --url URL  # ping an explicit URL (overrides env)
  python utils/heartbeat.py --timeout 10
"""
import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BOT_DIR / ".env")

sys.path.insert(0, str(BOT_DIR))
from core.config import get_heartbeat_url  # noqa: E402


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
    args = parser.parse_args(argv)

    url = (args.url or get_heartbeat_url() or "").strip()
    if not url:
        # Opt-in: nothing configured, nothing to do. This is not an error.
        print("heartbeat: HEARTBEAT_URL not set; heartbeat disabled (no-op).")
        return 0

    ok = send_ping(url, timeout=args.timeout)
    print(f"heartbeat: {'ok' if ok else 'ping failed'}")
    # Always exit 0: the monitor detects missing pings, so a single failed ping
    # is not a reason to mark a scheduled job failed and spam the operator.
    return 0


if __name__ == "__main__":
    sys.exit(main())
