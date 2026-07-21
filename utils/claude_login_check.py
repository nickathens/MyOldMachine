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

Cheap: two credential reads, no LLM call. It answers "when will a human next be
forced to log in", not "is auth working right now" — callers that need liveness
already find out by failing.

There are TWO chains on this machine, and the first version of this check
watched only one of them:

    the shared file    read by every per-user turn, because those set
                       CLAUDE_CONFIG_DIR and the CLI reads the file form for
                       any custom config dir. Every per-user workspace
                       symlinks to it, so one file covers every Telegram user.
    the keychain item  read by every turn that keeps the DEFAULT config dir:
                       the nightly reflection, the health checks, and any user
                       with no workspace of their own.

They refresh independently and hold genuinely different tokens (measured
2026-07-21: different fingerprints, refresh expiries a day apart). Watching the
file alone therefore reports "healthy" straight through an outage that has
killed every nightly job on the machine — which is the exact shape of the
2026-07-20 incident. So the status reported here is the WORSE of the two, and
the detail names the chain that is hurt.

Usage:
    python claude_login_check.py            # human-readable status
    python claude_login_check.py --json     # machine-readable
    python claude_login_check.py --warn-days 7
    python claude_login_check.py --path FILE  # that file only, skip the keychain
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent

# Days of notice before the refresh token lapses. Three nightly warnings is
# enough to catch Nick without nagging, and the chore itself takes seconds.
DEFAULT_WARN_DAYS = int(os.environ.get("CLAUDE_LOGIN_WARN_DAYS", "3"))

SHARED_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

RELOGIN_HINT = "Run `claude` in Terminal on the Mac mini to renew it (about 30 seconds)."

# How each chain is described to a human. The point of naming them is that a
# dead chain has a visible blast radius: "your chats still work but every
# nightly job is down" is a different message from "nothing works".
FILE_SOURCE = "The shared login file (used by the chats)"
KEYCHAIN_SOURCE = "The keychain login (used by the nightly jobs)"

# Worst first. The machine-wide verdict is the worst status across the chains,
# so this ordering decides whose problem gets reported. `expiring` outranks
# `unknown` because it is actionable and `unknown` is not.
_SEVERITY = ("expired", "missing", "unreadable", "expiring", "unknown", "ok")


def read_login_state(path: Path | None = None,
                     warn_days: int = DEFAULT_WARN_DAYS,
                     now: datetime | None = None,
                     source: str = "The Claude login") -> dict:
    """Inspect ONE Claude OAuth credential file.

    For the machine-wide answer use read_machine_login_state, which also
    covers the keychain chain. This function is the single-source primitive.

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

    return _state_from_oauth(oauth, source=source, warn_days=warn_days, now=now)


def _state_from_oauth(oauth: dict, *, source: str, warn_days: int,
                      now: datetime) -> dict:
    """Turn one credential's OAuth block into a status dict.

    Shared by both chains so a keychain credential and a file credential can
    never be judged by two subtly different rules.
    """
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
            f"{source} expired on {when}. Every AI job that uses it is "
            f"failing until it is renewed. {RELOGIN_HINT}")
    elif days_left <= warn_days:
        status, detail = "expiring", (
            f"{source} expires in {days_left:.1f} days ({when}). "
            f"{RELOGIN_HINT}")
    else:
        status, detail = "ok", (
            f"{source} is healthy — {days_left:.1f} days left (expires {when}).")

    return {"status": status, "days_left": days_left, "expires_at": refresh_at,
            "access_expires_at": access_at, "detail": detail}


def read_keychain_login_state(*, warn_days: int = DEFAULT_WARN_DAYS,
                              now: datetime | None = None) -> dict | None:
    """Inspect the plain macOS keychain credential, or None if there is no chain.

    Returns None — not "missing" — when no keychain item exists at all, and
    that distinction is the whole point. For a DEFAULT config dir the CLI
    reads the keychain item if one is present and otherwise falls back to the
    shared file, so an absent item is not an outage; it just means both kinds
    of turn ride the file. A PRESENT item takes precedence over the file,
    which is exactly why a present-but-dead one is an outage and has to be
    reported. Treating absent as "missing" would alert on every healthy
    Linux install and on any Mac that never exported to the keychain.
    """
    if sys.platform != "darwin":
        return None
    try:
        if str(BOT_DIR) not in sys.path:
            sys.path.insert(0, str(BOT_DIR))
        from core.claude_workspace import _read_keychain_item, _KEYCHAIN_SERVICE
        raw = _read_keychain_item(_KEYCHAIN_SERVICE)
    except Exception as e:  # import problem, missing `security`, anything
        return {"status": "unreadable", "days_left": None, "expires_at": None,
                "access_expires_at": None,
                "detail": f"Could not read the keychain login: {e}"}

    if raw is None:
        return None

    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except (TypeError, ValueError) as e:
        return {"status": "unreadable", "days_left": None, "expires_at": None,
                "access_expires_at": None,
                "detail": f"Could not read the keychain login: {e}"}

    if not oauth.get("accessToken") and not oauth.get("refreshToken"):
        # A hollow item is worse than none: it shadows a healthy file.
        return {"status": "missing", "days_left": None, "expires_at": None,
                "access_expires_at": None,
                "detail": f"{KEYCHAIN_SOURCE} is present but empty, which hides the "
                          f"shared login file from any job that uses it."}

    return _state_from_oauth(oauth, source=KEYCHAIN_SOURCE,
                             warn_days=warn_days, now=now or datetime.now())


def _severity(status: str) -> int:
    return _SEVERITY.index(status) if status in _SEVERITY else len(_SEVERITY)


def read_machine_login_state(*, warn_days: int = DEFAULT_WARN_DAYS,
                             now: datetime | None = None,
                             path: Path | None = None) -> dict:
    """The machine-wide verdict: the WORSE of the two credential chains.

    Reporting the file alone was the blind spot that let the 2026-07-20
    outage look healthy from the outside. The returned dict is the losing
    chain's state, plus a `chains` map so `--json` callers can see both.
    """
    now = now or datetime.now()

    chains = {"file": read_login_state(path=path, warn_days=warn_days, now=now,
                                       source=FILE_SOURCE)}
    keychain = read_keychain_login_state(warn_days=warn_days, now=now)
    if keychain is not None:
        chains["keychain"] = keychain

    worst = min(chains.values(), key=lambda s: _severity(s["status"]))
    combined = dict(worst)

    # A single dead chain is recoverable without a human: the healthy one gets
    # copied across on the next turn. Say so, or the alert reads like an
    # outage and sends Nick to the Terminal for nothing.
    healthy = [s for s in chains.values() if s["status"] == "ok"]
    if worst["status"] != "ok" and healthy:
        combined["detail"] += (
            " The other chain is still healthy, so the machine should repair "
            "this one by itself at the next turn — no login needed unless this "
            "warning repeats.")

    combined["chains"] = {name: {"status": s["status"], "days_left": s["days_left"],
                                 "detail": s["detail"]}
                          for name, s in chains.items()}
    return combined


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
    parser.add_argument("--file-only", action="store_true",
                        help="Skip the keychain chain and report the file alone")
    args = parser.parse_args()

    if args.file_only:
        state = read_login_state(path=args.path, warn_days=args.warn_days)
    else:
        state = read_machine_login_state(path=args.path, warn_days=args.warn_days)

    if args.json:
        print(json.dumps({
            "status": state["status"],
            "detail": state["detail"],
            "days_left": state["days_left"],
            "expires_at": state["expires_at"].isoformat() if state["expires_at"] else None,
            "access_expires_at": (state["access_expires_at"].isoformat()
                                  if state["access_expires_at"] else None),
            "chains": state.get("chains", {}),
        }, indent=2))
    else:
        print(state["detail"])
        for name, chain in state.get("chains", {}).items():
            print(f"  {name}: {chain['status']}")

    # Non-zero exit on states a human must act on, so shell callers can branch.
    return 0 if state["status"] in ("ok", "unknown") else 1


if __name__ == "__main__":
    raise SystemExit(main())
