#!/usr/bin/env python3
"""Mini App guard: keep the Telegram App button pointed at the live public URL
and prove, from the outside, that the app actually loads.

Why this exists: the chat menu button is registered once at install time
(install/miniapp_setup.py) and Telegram keeps serving that address forever.
When the machine's public URL changes, most commonly a Tailscale account or
tailnet switch minting a new *.ts.net hostname, the button quietly strands
every user on the dead address and nothing alerts anyone. This module gives
the bot two reflexes:

  1. At startup (bot.py post_init): detect the current public URL and
     re-point every registered user's button if it drifted. The fix is
     recorded in data/miniapp.json and mentioned in the next morning report.
  2. In the morning report (utils/nightly_report.py): load the public URL
     over the real internet exactly as a phone would, require the app shell
     to actually render (content marker, so an error page cannot pass as
     healthy), re-check the buttons, and self-fix any drift found overnight.
     Failures are reported plainly instead of staying quiet.

Public-address resolution order:
  1. live Tailscale Funnel status (the truth whenever the tunnel is up)
  2. the recorded URL in data/miniapp.json (Cloudflare named tunnels and
     manual setups have stable hostnames, so the record is authoritative)

LAN-only installs are verified against the recorded URL from this machine;
a DHCP-drifted LAN IP shows up as a load failure, deliberately not auto-fixed
(re-pointing the button at a guessed private IP could make things worse).

Stdlib only. Safe to import from the bot process (wrapped in a thread) and
from standalone scripts (nightly_report runs as its own subprocess).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from utils.safe_json import load_json, save_json  # noqa: E402

# data/miniapp.json: the durable record of the public URL plus the outcome of
# the last startup / report checks. Before this module existed the ONLY copy
# of the URL lived inside Telegram's button, which is exactly why a drifted
# button could not be detected.
STATE_FILENAME = "miniapp.json"

# The served app shell loads the Telegram WebApp SDK from this script tag
# (miniapp/static/index.html). A tunnel error page, a placeholder, or a
# wrong service answering 200 will not contain it.
APP_MARKER = "telegram-web-app.js"

# CLI location of the macOS Tailscale GUI app (App Store / standalone
# install), which does not put `tailscale` on PATH.
MACOS_TAILSCALE_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

BUTTON_TEXT = "App"


# ─── State ───────────────────────────────────────────────────────────


def _state_path(repo_dir: Path) -> Path:
    return repo_dir / "data" / STATE_FILENAME


def load_state(repo_dir: Path = REPO_DIR) -> dict:
    data = load_json(_state_path(repo_dir), default={})
    return data if isinstance(data, dict) else {}


def save_state(repo_dir: Path, state: dict) -> None:
    save_json(_state_path(repo_dir), state)


def record_setup(repo_dir: Path, public_url: str, tunnel: str) -> None:
    """Called by install/miniapp_setup.py after a successful install, so the
    guard starts life with a trustworthy record instead of self-seeding."""
    state = load_state(repo_dir)
    state["public_url"] = public_url.rstrip("/")
    state["tunnel"] = tunnel
    state["updated_at"] = _now()
    save_state(repo_dir, state)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─── Public-address detection ────────────────────────────────────────


def _tailscale_bin() -> str | None:
    path = shutil.which("tailscale")
    if path:
        return path
    if os.path.exists(MACOS_TAILSCALE_CLI):
        return MACOS_TAILSCALE_CLI
    return None


def funnel_url(timeout: int = 10) -> str | None:
    """The URL Tailscale Funnel is serving right now, or None.

    Same parse as install/miniapp_setup.py: first https://*.ts.net token in
    `tailscale funnel status`. Extended with the macOS GUI-app CLI path,
    which `which` cannot see.
    """
    binary = _tailscale_bin()
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "funnel", "status"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("https://") and ".ts.net" in line:
            return line.split()[0].rstrip("/")
    return None


def detect_public_url(repo_dir: Path = REPO_DIR, state: dict | None = None) -> tuple[str | None, str]:
    """(url, source). Live funnel beats the record: when the tunnel is up it
    IS the current public address, even after an account switch."""
    state = load_state(repo_dir) if state is None else state
    live = funnel_url()
    if live:
        return live, "tailscale-funnel"
    recorded = (state.get("public_url") or "").strip().rstrip("/")
    if recorded:
        return recorded, "recorded"
    return None, "none"


# ─── Outside-in load check ───────────────────────────────────────────


def check_public_load(url: str, timeout: int = 20) -> tuple[bool, str]:
    """GET the public URL the way a phone does and require the app shell to
    actually render. 200 alone is not health: a tunnel error page answers 200."""
    req = urllib.request.Request(
        url, method="GET", headers={"User-Agent": "MyOldMachine-guard/1.0"},
    )
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.status
                body = resp.read(262144).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            code, body = e.code, ""
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"unreachable ({e.__class__.__name__})"
    if code != 200:
        return False, f"HTTP {code}"
    if APP_MARKER not in body:
        return False, "HTTP 200 but the app shell did not render (marker missing)"
    return True, "HTTP 200, app content confirmed"


# ─── Telegram button ─────────────────────────────────────────────────


def resolve_token(repo_dir: Path = REPO_DIR) -> str:
    """Bot token from the environment, else parsed straight from .env, so the
    guard works identically inside the bot process and in subprocesses."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    env_path = repo_dir / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def registered_users(repo_dir: Path = REPO_DIR) -> list[str]:
    """Digit keys of data/users.json (same shape install/miniapp_setup.py reads)."""
    data = load_json(repo_dir / "data" / "users.json", default={})
    if not isinstance(data, dict):
        return []
    return [tid for tid in data.keys() if isinstance(tid, str) and tid.isdigit()]


def _bot_api(token: str, method: str, payload: dict, timeout: int = 10) -> tuple[bool, object]:
    api_base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    req = urllib.request.Request(
        f"{api_base}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False, None
    if not isinstance(body, dict) or not body.get("ok"):
        return False, None
    return True, body.get("result")


def get_button_url(token: str, chat_id: str) -> tuple[bool, str | None]:
    """(readable, url). url is None for the default/commands button."""
    ok, result = _bot_api(token, "getChatMenuButton", {"chat_id": int(chat_id)})
    if not ok:
        return False, None
    url = ""
    if isinstance(result, dict):
        url = (result.get("web_app") or {}).get("url") or ""
    return True, url or None


def set_button(token: str, chat_id: str, url: str) -> bool:
    """Same payload install/miniapp_setup.py registers at install time."""
    ok, _ = _bot_api(token, "setChatMenuButton", {
        "chat_id": int(chat_id),
        "menu_button": {
            "type": "web_app",
            "text": BUTTON_TEXT,
            "web_app": {"url": url},
        },
    })
    return ok


def _normalize(url: str) -> str:
    return (url or "").strip().rstrip("/")


def ensure_buttons(token: str, users: list[str], target_url: str, fix: bool = True) -> dict:
    """Compare every user's button against the live address; re-point drift.

    Returns {"ok": [...], "fixed": [...], "stale": [...], "failed": [...],
    "unreadable": [...]} of user ids. A user with no web_app button at all
    counts as stale and gets one, which also heals installs where the initial
    registration failed for that user.
    """
    summary = {"ok": [], "fixed": [], "stale": [], "failed": [], "unreadable": []}
    target = _normalize(target_url)
    for tid in users:
        readable, current = get_button_url(token, tid)
        if not readable:
            summary["unreadable"].append(tid)
            continue
        if current and _normalize(current) == target:
            summary["ok"].append(tid)
            continue
        if not fix:
            summary["stale"].append(tid)
            continue
        if set_button(token, tid, target_url):
            summary["fixed"].append(tid)
        else:
            summary["failed"].append(tid)
    return summary


# ─── Orchestration ───────────────────────────────────────────────────


def run_startup_guard(repo_dir: Path = REPO_DIR) -> dict:
    """Bot-start reflex: detect the current public address and re-point any
    drifted button. No public load check here: a tunnel that boots alongside
    the bot may not be up yet, and a false alarm at every boot would teach
    people to ignore the real one. The morning report is the strong net."""
    state = load_state(repo_dir)
    url, source = detect_public_url(repo_dir, state)
    outcome = {"at": _now(), "url": url, "source": source,
               "fixed": [], "failed": [], "ok": 0, "detail": ""}

    if not url:
        outcome["detail"] = "no public URL detectable (no live tunnel, nothing recorded)"
    else:
        token = resolve_token(repo_dir)
        users = registered_users(repo_dir)
        if not token:
            outcome["detail"] = "bot token unavailable, buttons unverified"
        elif not users:
            outcome["detail"] = "no registered users"
        else:
            res = ensure_buttons(token, users, url, fix=True)
            outcome["fixed"] = res["fixed"]
            outcome["failed"] = res["failed"] + res["unreadable"]
            outcome["ok"] = len(res["ok"])
            if res["fixed"]:
                outcome["detail"] = f"re-pointed {len(res['fixed'])} button(s) to {url}"
            elif outcome["failed"]:
                outcome["detail"] = f"{len(outcome['failed'])} button(s) could not be verified"
            else:
                outcome["detail"] = f"all {outcome['ok']} button(s) already point at {url}"

    if url and source == "tailscale-funnel" and _normalize(state.get("public_url", "")) != _normalize(url):
        state["public_url"] = url
        state["updated_at"] = _now()
    state["last_startup"] = outcome
    save_state(repo_dir, state)
    return outcome


def build_report_section(repo_dir: Path = REPO_DIR) -> list[str]:
    """The morning report's Mini App block. Empty list when the Mini App is
    not installed, so plain installs see no noise."""
    state = load_state(repo_dir)
    if not state:
        try:
            from install.miniapp_setup import is_miniapp_configured
            if not is_miniapp_configured(repo_dir):
                return []
        except Exception:
            return []

    lines = ["Mini App"]
    url, source = detect_public_url(repo_dir, state)
    if not url:
        lines.append("  Public URL unknown (no live tunnel, nothing recorded); button unverified")
        return lines

    load_ok, load_detail = check_public_load(url)
    lines.append(f"  Public URL: {url}")
    if url.startswith("http://"):
        load_detail += " (LAN URL, checked from this machine)"
    lines.append(f"  Loads from the internet: {'OK' if load_ok else 'FAILED'} ({load_detail})")

    token = resolve_token(repo_dir)
    users = registered_users(repo_dir)
    buttons: dict | None = None
    if not token:
        lines.append("  App button: unverified (no bot token available)")
    elif not users:
        lines.append("  App button: no registered users to check")
    else:
        buttons = ensure_buttons(token, users, url, fix=True)
        if buttons["fixed"]:
            lines.append(f"  App button: was stale for {len(buttons['fixed'])} user(s), re-pointed now")
        if buttons["failed"] or buttons["unreadable"]:
            bad = len(buttons["failed"]) + len(buttons["unreadable"])
            lines.append(f"  App button: FAILED to verify or re-point for {bad} user(s)")
        if buttons["ok"] and not buttons["fixed"] and not buttons["failed"] and not buttons["unreadable"]:
            lines.append(f"  App button: OK for {len(buttons['ok'])} user(s) (points at the live address)")

    startup = state.get("last_startup") or {}
    if startup.get("fixed") and not startup.get("reported"):
        lines.append(
            f"  Startup fix: button re-pointed for {len(startup['fixed'])} user(s) "
            f"at boot ({startup.get('at', 'unknown time')})"
        )
        startup["reported"] = True
        state["last_startup"] = startup

    if source == "tailscale-funnel" and _normalize(state.get("public_url", "")) != _normalize(url):
        state["public_url"] = url
        state["updated_at"] = _now()
    state["last_report"] = {
        "at": _now(), "url": url, "load_ok": load_ok, "load_detail": load_detail,
        "fixed": (buttons or {}).get("fixed", []),
        "failed": (buttons or {}).get("failed", []) + (buttons or {}).get("unreadable", []),
    }
    save_state(repo_dir, state)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini App button guard and outside-in health check")
    parser.add_argument("--startup", action="store_true",
                        help="Run the startup reflex (detect + re-point, no load check) and print JSON")
    args = parser.parse_args()

    if args.startup:
        print(json.dumps(run_startup_guard(), indent=2))
        return 0
    lines = build_report_section()
    if not lines:
        print("Mini App: not configured on this machine")
        return 0
    print("\n".join(lines))
    return 1 if any("FAILED" in line for line in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
