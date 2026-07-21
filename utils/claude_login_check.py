#!/usr/bin/env python3
"""
Claude login expiry check — turn a silent outage into a scheduled chore.

The Claude CLI login is an OAuth session with two independent clocks, and only
one of them can strand the machine:

    expiresAt              access token. Short-lived (hours). Refreshes itself
                           silently in the background. NOT the thing that breaks.
    refreshTokenExpiresAt  refresh token. Long-lived (~a month). Once this
                           lapses, nothing local can mint a new access token —
                           the only cure is a human running `claude`
                           interactively on the machine.

Every LLM-backed job (nightly reflection, health checks, the bot's own turns)
fails closed when the refresh token dies, so before this check the first signal
was a total outage that waited for someone to notice. Reading the refresh
expiry ahead of time converts that into a 30-second scheduled chore.

Deliberately cheap: a pure file read, no subprocess and no LLM call. It answers
"when will a human next be forced to log in", not "is auth working right now" —
callers that need liveness already find out by failing.

All per-user Claude workspaces symlink .credentials.json to one shared file, so
checking the shared file covers every user on the machine.

Usage:
    python claude_login_check.py            # human-readable status
    python claude_login_check.py --json     # machine-readable
    python claude_login_check.py --warn-days 7
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

# Days of notice before the refresh token lapses. Three nightly warnings is
# enough to catch Nick without nagging, and the chore itself takes seconds.
DEFAULT_WARN_DAYS = int(os.environ.get("CLAUDE_LOGIN_WARN_DAYS", "3"))

SHARED_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

RELOGIN_HINT = "Run `claude` in Terminal on the Mac mini to renew it (about 30 seconds)."


def read_login_state(path: Path | None = None,
                     warn_days: int = DEFAULT_WARN_DAYS,
                     now: datetime | None = None) -> dict:
    """Inspect the Claude OAuth credential file.

    Returns a dict with:
        status        one of: ok | expiring | expired | unknown | missing | unreadable
        detail        one-line human explanation
        days_left     float days until the refresh token lapses (None if unknown)
        expires_at    datetime the refresh token lapses (None if unknown)
        access_expires_at  datetime the access token lapses (None if absent)

    `status` keys off the REFRESH token, because that is the clock that forces a
    human back to the machine. A stale access token is normal and self-healing.
    """
    path = path or SHARED_CREDENTIALS
    now = now or datetime.now()

    if not path.exists():
        return {"status": "missing", "days_left": None, "expires_at": None,
                "access_expires_at": None,
                "detail": f"No Claude login found at {path}. The machine is not logged in."}

    try:
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError) as e:
        return {"status": "unreadable", "days_left": None, "expires_at": None,
                "access_expires_at": None,
                "detail": f"Could not read the Claude login file: {e}"}

    def _ts(key):
        raw = oauth.get(key)
        if not isinstance(raw, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(raw / 1000)
        except (OverflowError, OSError, ValueError):
            return None

    access_at = _ts("expiresAt")
    refresh_at = _ts("refreshTokenExpiresAt")

    if refresh_at is None:
        # Some credential shapes omit the refresh expiry. Say so rather than
        # inventing a deadline — a wrong "all clear" is worse than "unknown".
        return {"status": "unknown", "days_left": None, "expires_at": None,
                "access_expires_at": access_at,
                "detail": "Claude login found, but it does not record when it expires, "
                          "so the next forced login cannot be predicted."}

    days_left = (refresh_at - now).total_seconds() / 86400
    when = f"{refresh_at:%a %d %b %Y, %H:%M}"

    if days_left <= 0:
        status, detail = "expired", (
            f"The Claude login expired on {when}. Every AI job on this machine is "
            f"failing until it is renewed. {RELOGIN_HINT}")
    elif days_left <= warn_days:
        status, detail = "expiring", (
            f"The Claude login expires in {days_left:.1f} days ({when}). "
            f"{RELOGIN_HINT}")
    else:
        status, detail = "ok", (
            f"Claude login is healthy — {days_left:.1f} days left (expires {when}).")

    return {"status": status, "days_left": days_left, "expires_at": refresh_at,
            "access_expires_at": access_at, "detail": detail}


def warning_message(state: dict) -> str | None:
    """Admin-facing alert text, or None when nothing needs saying.

    Only the states a human must act on produce a message; `ok` stays quiet so
    the nightly job does not become noise that gets tuned out.
    """
    if state["status"] in ("ok", "unknown"):
        return None
    prefix = {
        "expired": "Claude login EXPIRED",
        "expiring": "Claude login expiring soon",
        "missing": "Claude login missing",
        "unreadable": "Claude login unreadable",
    }.get(state["status"], "Claude login")
    return f"{prefix}\n\n{state['detail']}"


def main():
    parser = argparse.ArgumentParser(description="Check when the Claude login expires")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--warn-days", type=int, default=DEFAULT_WARN_DAYS,
                        help=f"Days of notice before expiry (default: {DEFAULT_WARN_DAYS})")
    parser.add_argument("--path", type=Path, default=None,
                        help="Credential file to inspect (default: shared Claude login)")
    args = parser.parse_args()

    state = read_login_state(path=args.path, warn_days=args.warn_days)

    if args.json:
        print(json.dumps({
            "status": state["status"],
            "detail": state["detail"],
            "days_left": state["days_left"],
            "expires_at": state["expires_at"].isoformat() if state["expires_at"] else None,
            "access_expires_at": (state["access_expires_at"].isoformat()
                                  if state["access_expires_at"] else None),
        }, indent=2))
    else:
        print(state["detail"])

    # Non-zero exit on states a human must act on, so shell callers can branch.
    return 0 if state["status"] in ("ok", "unknown") else 1


if __name__ == "__main__":
    raise SystemExit(main())
