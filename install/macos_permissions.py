#!/usr/bin/env python3
"""The macOS permissions that let the bot drive other applications.

Three separate TCC services get spoken about as if they were one, and they are
not. They are granted independently, they fail with different errors, and on
this machine on 11 Sep 2026 one of them was already on while the other two were
off:

    Automation        send an Apple event to another app at all
    Accessibility     read another app's windows, click its buttons, type
    Screen Recording  see the screen: screencapture, window images

So a script that lists running processes proves nothing about whether clicking
will work, and "the bot can talk to System Events" is not an answer to "can the
bot press Install".

Every probe here works by doing the thing and reading what came back. None of
them looks for a file. That is deliberate: a marker on disk is not a state,
because a permission granted and then revoked in System Settings leaves every
file exactly where it was.

There is one more trap, and it is why `--check` exists at all. macOS attributes
a grant to the *responsible* process, which for this bot is its own Python
interpreter, not the `osascript` it shells out to. That interpreter is usually
an ad-hoc signed Homebrew build at a path with a version number in it. Upgrade
Python and the path, the code hash and the signing identifier all change, so
the grant silently stops applying while still looking present in System
Settings. This machine runs package updates on a nightly timer, so that is not
hypothetical.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_DIR / "data" / "macos_permissions.json"

GRANTED = "granted"
DENIED = "denied"
UNKNOWN = "unknown"

# System Settings deep links. These pane ids have survived the System
# Preferences to System Settings rename and are the same on macOS 13 to 26.
PANES = {
    "accessibility":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "screen_recording":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "automation":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
}


def _run(cmd: list[str], timeout: int = 25) -> tuple[int, str]:
    """Run a command, return (returncode, stdout + stderr). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _osascript(script: str, timeout: int = 25) -> tuple[int, str]:
    return _run(["osascript", "-e", script], timeout=timeout)


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------


def probe_automation() -> str:
    """Can this process send an Apple event to System Events at all?

    Everything else here goes through System Events, so when this is denied the
    other probes cannot report anything and have to say so rather than guess.
    """
    rc, out = _osascript(
        'tell application "System Events" to return name of first process')
    if rc == 0 and out.strip():
        return GRANTED
    low = out.lower()
    if "-1743" in out or "not allowed" in low or "not authori" in low:
        return DENIED
    return UNKNOWN


def probe_accessibility() -> str:
    """Two instruments, because one of them is a flag and one of them is the job.

    `UI elements enabled` answers true or false instead of raising, which is
    the cleaner read, but it is one property and one property can be wrong.
    The second probe performs an actual accessibility read of another
    application's window list, which is the capability being asked about. They
    have to agree before this returns granted.
    """
    if probe_automation() == DENIED:
        return UNKNOWN

    rc, out = _osascript(
        'tell application "System Events" to return UI elements enabled')
    flag = out.strip().lower() if rc == 0 else ""

    rc2, out2 = _osascript(
        'tell application "System Events" to return '
        "count of windows of first application process")
    # -25211 is kAXErrorAPIDisabled, rendered as "not allowed assistive access".
    read_denied = "-25211" in out2 or "assistive access" in out2.lower()
    read_ok = rc2 == 0 and out2.strip().lstrip("-").isdigit()

    if read_ok and flag != "false":
        return GRANTED
    if read_denied or flag == "false":
        return DENIED
    return UNKNOWN


def probe_screen_recording() -> str:
    """Capture one pixel, not the screen.

    A full grab would put a picture of whatever the account holder has open
    into a temp file, and this is a probe, not a screenshot tool. One pixel is
    enough, because the permission is refused before anything is rendered: a
    1x1 rectangle fails exactly the way a whole display does.
    """
    with tempfile.TemporaryDirectory() as td:
        shot = Path(td) / "probe.png"
        rc, out = _run(["screencapture", "-x", "-R", "0,0,1,1", str(shot)])
        if rc == 0 and shot.exists() and shot.stat().st_size > 0:
            return GRANTED
        if "could not create image" in out.lower():
            return DENIED
    return UNKNOWN


PERMISSIONS = [
    {
        "key": "accessibility",
        "label": "Accessibility",
        "unlocks": "click buttons, type, and read other apps' windows",
        "probe": probe_accessibility,
    },
    {
        "key": "screen_recording",
        "label": "Screen Recording",
        "unlocks": "see the screen, so it can check its own work",
        "probe": probe_screen_recording,
    },
    {
        "key": "automation",
        "label": "Automation (Apple events)",
        "unlocks": "send a command to another app; macOS asks on first use",
        "probe": probe_automation,
    },
]


def probe_all() -> dict[str, str]:
    return {p["key"]: p["probe"]() for p in PERMISSIONS}


# ---------------------------------------------------------------------------
# which program the grant has to name
# ---------------------------------------------------------------------------


def framework_app(binary: Path) -> Path | None:
    """The Python.app a framework build re-execs itself through.

    `.venv/bin/python` is a chain of symlinks ending at
    `.../Python.framework/Versions/3.12/bin/python3.12`, and that is not what
    runs. A framework build hands off to `Resources/Python.app` so it can reach
    the window server, and that bundle is what appears in `ps` and what System
    Settings has to be given. Adding the obvious `bin/python3.12` instead
    produces a grant that never applies to anything, with no error to say so.
    """
    try:
        parents = list(binary.resolve().parents)
    except OSError:
        return None
    for parent in parents:
        candidate = parent / "Resources" / "Python.app"
        if candidate.is_dir():
            return candidate
        if parent.name == "Versions":
            break
    return None


# The launch agent starts the bot as
#   /bin/bash -c "set -a; source .env; ... exec .../python .../bot.py"
# so the process table contains at least one line that mentions bot.py and is
# not the interpreter: granting the permission to /bin/bash would be both
# useless and alarming. A `grep bot.py` typed in a terminal is the same trap.
_NOT_THE_BOT = {"bash", "sh", "zsh", "dash", "env", "sudo", "grep", "tail", "open"}


def _live_bot_executable() -> Path | None:
    """The executable of the running bot, read from the process table.

    `pgrep -f bot.py` is not used: on macOS it does not match a process that is
    an ancestor of the caller, and inside this bot every command is a
    descendant of exactly that process, so pgrep returns nothing while the bot
    is plainly running. Measured 11 Sep 2026, with the bot up and visible in
    `ps`, `pgrep -f bot.py` exited 1.
    """
    rc, out = _run(["ps", "ax", "-o", "pid=,args="], timeout=15)
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        args = parts[1]
        if "/bot.py" not in args or not args.startswith("/"):
            continue
        exe = _executable_from(args)
        if exe.name.lower() in _NOT_THE_BOT:
            continue
        return exe
    return None


def _executable_from(args: str) -> Path:
    """The program a ps line ran, when its path may contain a space.

    Taking everything before the first space is wrong for a checkout at, say,
    `/Users/j/My Old Machine`: it yields `/Users/j/My`, which names nothing,
    and `grant_target` would then put that on the clipboard for somebody to
    paste into System Settings. Widen the first token until it names a real
    file. Nothing on disk matches under a hermetic test, so the first token
    stays the answer there.
    """
    tokens = args.split(" ")
    for i in range(1, len(tokens) + 1):
        candidate = Path(" ".join(tokens[:i]))
        if candidate.is_file():
            return candidate
    return Path(tokens[0])


def grant_target(repo_dir: Path | None = None) -> Path | None:
    """The exact thing to add in System Settings.

    Resolved from the virtualenv first because that answer exists at install
    time, when no bot is running yet, and because the launch agent runs the bot
    from that same virtualenv. A live bot is consulted only to override it, for
    the case where someone started the bot from a different interpreter than
    the one the installer built.
    """
    target = None
    venv_python = (repo_dir or REPO_DIR) / ".venv" / "bin" / "python"
    if venv_python.exists():
        resolved = venv_python.resolve()
        target = framework_app(resolved) or resolved

    live = _live_bot_executable()
    if live is not None:
        if "/Contents/MacOS/" in str(live):
            live = Path(str(live).split("/Contents/MacOS/")[0])
        else:
            live = framework_app(live) or live
        return live
    return target


def code_identity(target: Path) -> str | None:
    """The signing identifier TCC keys the grant to.

    A Homebrew Python is ad-hoc signed, so this is derived from the binary
    itself and changes on every rebuild. Recording it is what later lets
    `regressions()` tell "you revoked it" apart from "a Python upgrade moved
    it", which are the same symptom and different instructions.
    """
    rc, out = _run(["codesign", "-dv", str(target)], timeout=20)
    if rc != 0:
        return None
    m = re.search(r"^Identifier=(.+)$", out, re.MULTILINE)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


def load_state(path: Path | None = None) -> dict:
    try:
        return json.loads((path or STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    f = path or STATE_FILE
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, indent=2) + "\n")
    except OSError:
        pass


def record_grants(states: dict[str, str], target: Path | None,
                  path: Path | None = None) -> dict:
    """Remember only what was observed working.

    Recording an intention, "the account holder said yes", would recreate the
    exact failure this module exists to avoid. Nothing is written for a
    permission whose probe did not come back granted.
    """
    state = load_state(path)
    granted = state.get("granted") or {}
    identity = code_identity(target) if target else None
    for key, value in states.items():
        if value == GRANTED:
            granted[key] = {
                "target": str(target) if target else None,
                "identity": identity,
            }
    state["granted"] = granted
    save_state(state, path)
    return state


def regressions(path: Path | None = None) -> list[str]:
    """Permissions observed working once and refused now.

    Silent when nothing was ever granted. An installation that never wanted
    screen control must never be nagged about not having it, which is the
    difference between a useful alert and one people learn to ignore.
    """
    if platform.system() != "Darwin":
        return []
    granted = load_state(path).get("granted") or {}
    if not granted:
        return []

    target = grant_target()
    identity_now = code_identity(target) if target else None
    live = probe_all()
    out = []
    for key, record in granted.items():
        if live.get(key) != DENIED:
            continue
        label = next((p["label"] for p in PERMISSIONS if p["key"] == key), key)
        was = (record or {}).get("identity")
        if was and identity_now and was != identity_now:
            out.append(
                f"{label} has stopped applying: the interpreter it was granted "
                f"to was replaced, most likely by a Homebrew Python upgrade. "
                f"Re-add {target} in System Settings."
            )
        else:
            out.append(f"{label} was granted once and is refused now.")
    return out


def is_configured(path: Path | None = None) -> bool:
    """True once a grant has been observed working at least once.

    Deliberately does not re-probe. This decides whether the installer offers
    the step again on resume, and a permission somebody revoked on purpose
    should not come back as a question every time. `regressions()` is what
    notices a grant that stopped working.
    """
    return bool(load_state(path).get("granted") or {})


# ---------------------------------------------------------------------------
# the interactive grant
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
DIM = "\033[2m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
NC = "\033[0m"

MARK = {
    GRANTED: f"{GREEN}granted{NC}",
    DENIED: f"{RED}not granted{NC}",
    UNKNOWN: f"{YELLOW}could not tell{NC}",
}


def copy_to_clipboard(text: str) -> bool:
    try:
        p = subprocess.run(["pbcopy"], input=text, text=True,
                           timeout=10, check=False)
        return p.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def open_pane(key: str) -> bool:
    url = PANES.get(key)
    if not url:
        return False
    rc, _ = _run(["open", url], timeout=15)
    return rc == 0


def print_status(states: dict[str, str]) -> None:
    for perm in PERMISSIONS:
        print(f"    {perm['label']:<26} {MARK[states[perm['key']]]}")


def run_macos_permissions_step(config: dict, ask=input) -> None:
    """Offer the grant.

    This never grants anything. macOS does not allow a process to grant itself
    these, and it should not: the point of the permission is that a human
    decided. All this does is name the right entry, put it somewhere it can be
    pasted, open the right pane, and then check whether it actually took.
    """
    if platform.system() != "Darwin":
        return

    print(f"\n{BOLD}Screen control permissions (macOS){NC}")
    print(
        "  Without these the assistant can run commands but cannot touch a\n"
        "  window. Some jobs have no other route: clicking Install in an app\n"
        "  store window, or driving Photoshop and After Effects, which have no\n"
        "  command line for anything except rendering."
    )
    print(f"\n  {BOLD}Read this before saying yes.{NC} Accessibility is not scoped")
    print("  to one app. It lets whatever holds it control every application on")
    print("  this machine for as long as it holds it, and it is the same")
    print("  permission a keylogger would want. That can be a reasonable trade")
    print("  for an assistant you run yourself. It is not a detail to wave")
    print("  through, and nothing here needs it to work at all.")

    states = probe_all()
    print("\n  Right now:")
    print_status(states)

    if all(states[p["key"]] == GRANTED for p in PERMISSIONS):
        print(f"\n  {GREEN}Nothing to do.{NC}")
        record_grants(states, grant_target())
        return

    answer = (ask("\n  Set these up now? [y/N]: ") or "").strip().lower()
    if answer not in ("y", "yes"):
        print("  Skipped, and nothing is worse off. Run this whenever you like:")
        print(f"    {DIM}python install/macos_permissions.py --grant{NC}")
        return

    target = grant_target()
    if target is None:
        print(f"  {RED}Could not work out which program to grant.{NC} "
              "Is the virtualenv built?")
        return

    print(f"\n  {BOLD}The entry to add is:{NC}")
    print(f"    {target}")
    if copy_to_clipboard(str(target)):
        print(f"  {DIM}(copied to the clipboard){NC}")
    print(
        "\n  It is not the app you would guess, and guessing is the usual way\n"
        "  this goes wrong. macOS gives the permission to the program\n"
        "  responsible for the request, which here is the assistant's own\n"
        "  Python, not the small script it uses to send the click."
    )

    for perm in PERMISSIONS:
        if perm["key"] == "automation" or states[perm["key"]] == GRANTED:
            continue
        print(f"\n  {BOLD}{perm['label']}{NC} lets it {perm['unlocks']}.")
        print("    1. The System Settings pane is opening now.")
        print("    2. Click the + button under the list.")
        print("    3. Press Shift-Command-G, paste, press Return, click Open.")
        print("    4. Check the new row's switch is on.")
        open_pane(perm["key"])
        ask("    Press Return here when that is done: ")

    after = probe_all()
    print(f"\n  {BOLD}Checked again:{NC}")
    print_status(after)
    record_grants(after, target)

    missing = [p["label"] for p in PERMISSIONS if after[p["key"]] != GRANTED]
    if missing:
        print(f"\n  {YELLOW}Still missing: {', '.join(missing)}.{NC}")
        print("  A permission added while a program is already running often")
        print("  only takes effect once it restarts. Restart the bot, then:")
        print(f"    {DIM}python install/macos_permissions.py --check{NC}")
    else:
        print(f"\n  {GREEN}All set.{NC}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report or grant the macOS permissions the bot needs to "
                    "drive other applications.")
    ap.add_argument("--check", action="store_true",
                    help="report the current state and exit (the default)")
    ap.add_argument("--grant", action="store_true",
                    help="walk through granting whatever is missing")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output for --check")
    args = ap.parse_args(argv)

    if platform.system() != "Darwin":
        print("These permissions are macOS only.", file=sys.stderr)
        return 1

    if args.grant:
        run_macos_permissions_step({}, ask=input)
        return 0

    states = probe_all()
    target = grant_target()
    if args.json:
        print(json.dumps({
            "states": states,
            "target": str(target) if target else None,
            "regressions": regressions(),
        }, indent=2))
        return 0

    print(f"{BOLD}Screen control permissions{NC}")
    print_status(states)
    print(f"\n  granted to: {target}")
    for line in regressions():
        print(f"  {YELLOW}{line}{NC}")
    if any(v != GRANTED for v in states.values()):
        print(f"\n  {DIM}python install/macos_permissions.py --grant{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
