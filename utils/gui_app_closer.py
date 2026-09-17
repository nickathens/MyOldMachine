#!/usr/bin/env python3
"""Close heavyweight GUI applications that nobody has used for an hour.

The sibling of the headless sweep in `process_reaper.reap_idle_apps`, and
deliberately NOT the same mechanism. That one closes orphaned batch helpers
(soffice --headless, gimp -i -b) and is allowed to signal them, because its
safety latch proves no human ever opened them. Photoshop, Illustrator, After
Effects and DaVinci Resolve are the opposite case: they are always opened as
real GUI apps holding real documents, so the latch can never match them and a
signal could destroy unsaved work. They need their own track with their own
rules, which is this module.

Why they matter: Photoshop alone holds ~3 GB resident on this machine and is
routinely left open for hours after a job finishes. On a 24 GB box that plus
the voice engines is most of the reason memory pressure warns at all.

Four gates, ALL of which must hold before an app is asked to quit:

  1. Nobody is at the machine. macOS reports seconds since the last keyboard
     or mouse event (HIDIdleTime); it must exceed the same TTL. While a human
     is present their apps are never touched, even an app they are not using.
  2. The app has done no real work for the whole TTL. This is measured, not
     assumed: each sweep reads the process's cumulative CPU time and derives a
     rate. An idle Photoshop with a document open still burns a steady 5-6% of
     one core (measured on this machine over six 30s samples: 5.1, 5.1, 5.1,
     5.2, 5.2, 6.1), so "near zero CPU" would never fire and BUSY_CPU_PERCENT
     sits above that band instead. Anything driving the app -- a filter, an
     export, a render, a script step -- spikes far above it and resets the
     clock, which is what stops a mid-job app from being closed between steps.
  3. Nothing is unsaved. Each app family is asked directly. An app that cannot
     be asked, or answers anything unexpected, counts as unsaved and is left
     alone: the fail-safe direction is always "do not close".
  4. The quit is a request, never a kill. AppleScript `quit`, then verify the
     process is gone. If it does not go -- a modal is up, a save dialog
     appeared -- it is left running and reported. Nothing here ever signals a
     GUI app, so the worst case is an app that stays open.

Because gate 2 needs a history, an app must be watched for a full TTL before
it can be closed. A bot restart clears that history, so nothing closes for an
hour afterwards. That is the conservative direction and is intentional.

macOS only. On Linux HIDIdleTime does not exist, the probe returns None and
the sweep does nothing at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import subprocess
import time
from typing import Optional

logger = logging.getLogger("gui_app_closer")

# Bundle-name needles, matched against the innermost .app bundle of each
# process. A needle is a prefix in practice ("Adobe Photoshop" matches the
# year-suffixed "Adobe Photoshop 2026.app") which keeps the list stable across
# annual Adobe renames. Media Encoder is deliberately absent: it holds a render
# queue that is lost when it closes, and an idle queue looks exactly like an
# idle app.
DEFAULT_GUI_APPS = (
    "Adobe Photoshop",
    "Adobe Illustrator",
    "Adobe After Effects",
    "DaVinci Resolve",
)

# How long an app must be both unused and unattended before it is asked to go.
DEFAULT_GUI_IDLE_MINUTES = 60

# Percent of ONE core, averaged over the gap between two sweeps. Above this the
# app is working and its idle clock resets. Set from the measured idle band of
# 5-6% (see module docstring) with roughly 2.5x of headroom, and low enough
# that a short burst inside one 30s sample still clears it: 5 seconds of real
# work in a 30s window is ~17% on its own.
DEFAULT_BUSY_CPU_PERCENT = 15.0

# Two samples closer together than this are not a measurement. A CPU rate over
# a one-second gap is mostly noise -- a third of a second of work reads as 30%
# and resets an hour-old idle clock -- and the gap shrinks to nothing whenever
# something else asks for a reading, such as the status command. Below this the
# sample is held rather than used, so the NEXT one measures across the whole
# span instead. Only ever delays a close; never causes one.
MIN_SAMPLE_SECONDS = 5.0

# How long to wait for an app to disappear after being asked to quit.
QUIT_GRACE_SECONDS = 25.0
# Ceiling on any single osascript call, so a wedged app cannot stall the sweep.
OSASCRIPT_TIMEOUT = 20.0

_APP_EXEC_MARKER = ".app/Contents/MacOS/"
_HID_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


# --------------------------------------------------------------------------
# Pure helpers (no I/O -- unit tested directly)
# --------------------------------------------------------------------------

def parse_cpu_time(text: str) -> Optional[float]:
    """Parse `ps -o time=` into seconds.

    Formats seen from BSD ps: "9:32.89" (mm:ss.hh), "1:02:03" (hh:mm:ss) and
    "2-03:04:05" (dd-hh:mm:ss). Returns None on anything unrecognised so a
    caller can skip the row rather than invent a number.
    """
    text = (text or "").strip()
    if not text:
        return None
    days = 0.0
    if "-" in text:
        day_part, _, text = text.partition("-")
        try:
            days = float(day_part)
        except ValueError:
            return None
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    seconds = 0.0
    for v in values:
        seconds = seconds * 60.0 + v
    return days * 86400.0 + seconds


def bundle_name(command: str) -> Optional[str]:
    """Return the innermost .app bundle name for a process command line.

    "/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app/Contents/
    MacOS/Adobe Photoshop 2026" -> "Adobe Photoshop 2026". Resolve's executable
    is named differently from its bundle ("DaVinci Resolve.app/Contents/MacOS/
    Resolve"), which is exactly why this reads the bundle and not the binary.

    Taking the LAST marker and requiring the executable to sit directly inside
    MacOS/ is what keeps a helper from being mistaken for its parent: a helper
    in its own nested bundle reports its own name ("Creative Cloud UI Helper"),
    and a .appex plugin matches no marker at all and returns None. Either way a
    needle aimed at the parent app can never select one.
    """
    if not command:
        return None
    idx = command.rfind(_APP_EXEC_MARKER)
    if idx < 0:
        return None
    tail = command[idx + len(_APP_EXEC_MARKER):]
    # The executable is a direct child of MacOS/. Arguments may follow, so cut
    # at the first space; a path with a space in the executable name itself is
    # indistinguishable here, which is why the needle match below is a prefix.
    if "/" in tail.split(" ", 1)[0]:
        return None
    head = command[:idx]
    name = os.path.basename(head)
    return name or None


def matches_needle(bundle: str, needles) -> Optional[str]:
    """Return the needle a bundle matches, or None. Case-insensitive prefix."""
    low = (bundle or "").lower()
    for n in needles:
        if low.startswith(n.lower()):
            return n
    return None


def sample_apps(ps_lines, needles, self_pids) -> list:
    """Pure filter over `ps` rows into managed-GUI-app samples.

    Rows are "PID ELAPSED %CPU RSS TIME COMMAND" -- the same shape the process
    reaper already collects, with cumulative CPU TIME added, since the rate
    between two samples is the only honest idle measure for a GUI app.
    """
    out = []
    for line in ps_lines or []:
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid_s, _etime_s, _cpu_s, rss_s, time_s, command = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid in self_pids:
            continue
        bundle = bundle_name(command)
        if not bundle:
            continue
        needle = matches_needle(bundle, needles)
        if not needle:
            continue
        cpu_seconds = parse_cpu_time(time_s)
        if cpu_seconds is None:
            continue
        try:
            rss_mb = int(rss_s) / 1024.0
        except ValueError:
            rss_mb = 0.0
        out.append({
            "pid": pid,
            "bundle": bundle,
            "needle": needle,
            "cpu_seconds": cpu_seconds,
            "rss_mb": rss_mb,
            "command": command,
        })
    return out


class IdleTracker:
    """Tracks how long each managed app has gone without doing real work.

    Deliberately in-memory: the only cost of losing it is that apps become
    eligible an hour later than they might have, and a stale idle clock read
    off disk after a crash could close something that was in use.
    """

    def __init__(self, busy_percent: float = DEFAULT_BUSY_CPU_PERCENT):
        self.busy_percent = busy_percent
        self._state: dict = {}  # pid -> {cpu, wall, idle_since}

    def observe(self, rows, now: Optional[float] = None) -> list:
        """Fold one sample in. Returns rows with `idle_seconds` attached."""
        if now is None:
            now = time.monotonic()
        seen = set()
        result = []
        for row in rows:
            pid = row["pid"]
            seen.add(pid)
            prev = self._state.get(pid)
            cpu = row["cpu_seconds"]
            if prev is None or cpu < prev["cpu"]:
                # First sighting, or cumulative CPU went backwards, which means
                # the pid was reused by a different process. Start the clock.
                self._state[pid] = {"cpu": cpu, "wall": now, "idle_since": now}
            else:
                d_wall = now - prev["wall"]
                if d_wall >= MIN_SAMPLE_SECONDS:
                    rate = 100.0 * (cpu - prev["cpu"]) / d_wall
                    if rate > self.busy_percent:
                        prev["idle_since"] = now  # it did real work; reset
                    prev["cpu"] = cpu
                    prev["wall"] = now
            state = self._state[pid]
            enriched = dict(row)
            enriched["idle_seconds"] = max(0.0, now - state["idle_since"])
            result.append(enriched)
        for pid in [p for p in self._state if p not in seen]:
            del self._state[pid]  # app exited; forget it
        return result

    def peek(self, rows, now: Optional[float] = None) -> list:
        """Read the idle clocks without touching them.

        The status command must not disturb the measurement it is reporting:
        folding an extra sample in at an arbitrary moment is what MIN_SAMPLE_
        SECONDS exists to survive, and not taking the sample at all is better
        still. An app not yet being tracked reads as zero.
        """
        if now is None:
            now = time.monotonic()
        out = []
        for row in rows:
            st = self._state.get(row["pid"])
            enriched = dict(row)
            enriched["idle_seconds"] = (
                0.0 if st is None else max(0.0, now - st["idle_since"]))
            out.append(enriched)
        return out

    def forget(self, pid: int) -> None:
        self._state.pop(pid, None)

    def restart(self, pid: int, now: Optional[float] = None) -> None:
        """Put an app back to the start of its idle window.

        Used after any attempt that did not end in a close. Without it a app
        that cannot be closed -- unsaved, unreachable, or sitting behind a
        modal that swallows the quit -- would be probed and asked again on
        every 30s tick forever: an Apple Event storm at the app, up to 45s of
        the janitor loop blocked per attempt, and the probe's own CPU cost
        landing on the very process whose CPU is being measured. One attempt
        per window is enough.
        """
        st = self._state.get(pid)
        if st is not None:
            st["idle_since"] = time.monotonic() if now is None else now


# --------------------------------------------------------------------------
# Machine probes
# --------------------------------------------------------------------------

def human_idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard or mouse event, or None if unknowable.

    macOS keeps this in the IOHIDSystem registry in nanoseconds. None on any
    other platform or any failure, which disables the whole sweep -- an unknown
    human presence must never be read as absence.
    """
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = _HID_IDLE_RE.search(out.stdout or "")
    if not m:
        return None
    try:
        return int(m.group(1)) / 1_000_000_000.0
    except ValueError:
        return None


def phys_footprint_mb(pid: int) -> Optional[float]:
    """How much memory a process really costs, in MB, or None.

    NOT resident set size. macOS compresses the pages of an idle process, and
    `ps -o rss=` only counts what is still uncompressed, so it reports a
    100x-too-small number for exactly the processes this code is about: the
    speaking engine measured 37 MB by RSS and 5028 MB by footprint, and
    Photoshop 927 MB against 3129 MB. Reporting RSS would make a sweep that
    freed 3 GB claim it freed 900 MB.

    `top -l 1 -stats mem` reports the same figure as /usr/bin/footprint's
    phys_footprint (checked against it: 5028 MB both ways) and needs no
    privileges, where footprint needs root for another process.
    """
    try:
        r = subprocess.run(["top", "-l", "1", "-pid", str(pid), "-stats", "mem"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed((r.stdout or "").splitlines()):
        m = re.match(r"^\s*([\d.]+)([KMGB])", line.strip())
        if m:
            value, unit = float(m.group(1)), m.group(2)
            return {"K": value / 1024.0, "M": value,
                    "G": value * 1024.0, "B": value / 1048576.0}[unit]
    return None


def _osascript(args: list, timeout: float = OSASCRIPT_TIMEOUT):
    """Run osascript. Returns (ok, output). Never raises."""
    try:
        r = subprocess.run(["osascript"] + args, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if r.returncode != 0:
        return False, (r.stderr or "").strip()
    return True, (r.stdout or "").strip()


def _tell(app: str, body: str, timeout: float = OSASCRIPT_TIMEOUT):
    """`tell application X to <body>` wrapped in an AppleScript timeout.

    The inner timeout matters as much as the outer one: without it a busy app
    leaves the Apple Event queued and osascript waits forever.
    """
    inner = max(5, int(timeout) - 5)
    return _osascript([
        "-e", f"with timeout of {inner} seconds",
        "-e", f'tell application "{app}" to {body}',
        "-e", "end timeout",
    ], timeout=timeout)


# --- unsaved-work probes, one per app family ------------------------------
# Each returns (state, detail) where state is "clean", "dirty" or "unknown".
# Only "clean" ever permits a close.

def _as_applescript_string(text: str) -> str:
    """Quote a string for embedding in AppleScript source.

    The scripts below carry JavaScript into an app, and that JavaScript has its
    own quotes in it. Concatenating raw would end the AppleScript string early
    and osascript answers "syntax error: Expected end of line" -- which the
    probe reads as "unknown", so the app is never closed and the fault is
    silent apart from a log line. Caught live rather than in the unit tests,
    which stub the osascript call out.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


_PS_JS = (
    "var n=0; for (var i=0;i<app.documents.length;i++)"
    "{ if (app.documents[i].saved == false) n++; } 'unsaved=' + n;"
)


def _probe_photoshop(bundle: str):
    """Photoshop's JS DOM reports Document.saved. Verified live on this
    machine: a freshly created document reads true, and false immediately
    after a fill, so the dirty case genuinely fires."""
    ok, out = _tell(bundle, "do javascript " + _as_applescript_string(_PS_JS))
    if not ok:
        return "unknown", out or "no answer"
    m = re.search(r"unsaved=(\d+)", out)
    if not m:
        return "unknown", out
    n = int(m.group(1))
    return ("clean", "") if n == 0 else ("dirty", f"{n} unsaved document(s)")


def _probe_illustrator(bundle: str):
    """Illustrator's dictionary carries `modified` on document, so this needs
    no JS bridge. `every document` returns an empty list when nothing is open,
    which reads as clean."""
    ok, out = _tell(bundle, "get modified of every document")
    if not ok:
        return "unknown", out or "no answer"
    low = out.lower().strip()
    if low in ("", "{}"):
        return "clean", ""
    tokens = [t.strip() for t in low.strip("{}").split(",") if t.strip()]
    if not tokens:
        return "clean", ""
    if any(t not in ("true", "false") for t in tokens):
        return "unknown", out
    n = sum(1 for t in tokens if t == "true")
    return ("clean", "") if n == 0 else ("dirty", f"{n} unsaved document(s)")


def _probe_after_effects(bundle: str):
    """After Effects exposes DoScript, and ExtendScript's app.project.dirty is
    the unsaved flag. The parse is exact-match only: anything but a bare
    "false" is unknown, so a changed return shape can never read as clean.

    Untested against a live After Effects on this machine -- it has never been
    open while this ran -- which is precisely why it fails to "unknown" and
    therefore never closes until it has been seen to answer properly.
    """
    ok, out = _tell(bundle, "DoScript " + _as_applescript_string(
        "(app.project ? app.project.dirty : true).toString()"))
    if not ok:
        return "unknown", out or "no answer"
    low = out.strip().strip('"').lower()
    if low == "false":
        return "clean", ""
    if low == "true":
        return "dirty", "project has unsaved changes"
    return "unknown", out


def _probe_resolve(bundle: str):
    """Resolve has no dirty flag in its API, so the question is answered a
    different way: ask the Resolve skill to prepare a quit, which refuses while
    a render is in progress and otherwise saves the current project through the
    API -- the same thing Resolve's own Live Save does. Clean here means "saved
    just now", not "was already saved"."""
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "skills", "davinci-resolve", "scripts", "resolve_api.py")
    if not os.path.exists(script):
        return "unknown", "resolve_api.py not found"
    try:
        r = subprocess.run(["python3", script, "prepare-quit"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return "unknown", str(e)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and "SAVED" in out:
        return "clean", ""
    return "unknown", out or (r.stderr or "").strip() or "no answer"


_PROBES = {
    "Adobe Photoshop": _probe_photoshop,
    "Adobe Illustrator": _probe_illustrator,
    "Adobe After Effects": _probe_after_effects,
    "DaVinci Resolve": _probe_resolve,
}


def probe_unsaved(needle: str, bundle: str):
    """Dispatch to the family probe. An app with no probe is never closed."""
    fn = _PROBES.get(needle)
    if fn is None:
        return "unknown", "no unsaved-work probe for this app"
    try:
        return fn(bundle)
    except Exception as e:  # a probe must never take the sweep down
        return "unknown", f"probe failed: {e}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def quit_app(bundle: str, pid: int, grace: float = QUIT_GRACE_SECONDS) -> bool:
    """Ask an app to quit and confirm it went. Never signals the process.

    Returns True only when the pid is actually gone, so a save dialog that
    swallows the quit is reported as a failure rather than assumed a success.
    """
    ok, detail = await asyncio.to_thread(_tell, bundle, "quit")
    if not ok:
        logger.warning("Quit request to %s was refused: %s", bundle, detail)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(1.0)
    return not _pid_alive(pid)


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

_tracker: Optional[IdleTracker] = None


def get_tracker() -> IdleTracker:
    global _tracker
    if _tracker is None:
        _tracker = IdleTracker()
    return _tracker


async def sweep(config: Optional[dict] = None,
                ps_lines: Optional[list] = None,
                tracker: Optional[IdleTracker] = None,
                human_idle_probe=None,
                prober=None,
                quitter=None,
                now: Optional[float] = None,
                force: bool = False) -> list:
    """One pass. Returns descriptors of the apps that were actually closed.

    Everything the pass depends on is injectable so the tests drive it with
    synthetic rows, a fake keyboard idle, a fake prober and a fake quitter --
    no Adobe app is ever launched or signalled by the suite.
    """
    if config is None:
        from utils.maintenance import load_config
        config = load_config()

    if not force and not config.get("close_idle_gui_apps", True):
        return []
    if platform.system() != "Darwin":
        return []

    minutes = config.get("close_idle_gui_app_minutes", DEFAULT_GUI_IDLE_MINUTES)
    try:
        ttl = float(minutes) * 60.0
    except (TypeError, ValueError):
        ttl = DEFAULT_GUI_IDLE_MINUTES * 60.0
    needles = tuple(config.get("close_idle_gui_app_names") or DEFAULT_GUI_APPS)

    if tracker is None:
        tracker = get_tracker()
    if prober is None:
        prober = probe_unsaved
    if quitter is None:
        quitter = quit_app

    if ps_lines is None:
        ps_lines = await list_gui_process_lines()

    rows = tracker.observe(
        sample_apps(ps_lines, needles, {os.getpid(), os.getppid()}), now=now)
    if not rows:
        return []

    ready = [r for r in rows if r["idle_seconds"] >= ttl]
    if not ready:
        return []

    # Only ask about the human once there is something worth closing: the
    # ioreg call is the most expensive probe in the pass. This is injected as a
    # probe rather than as a value so that "the probe answered None" stays
    # distinguishable from "no value was supplied" -- the two must not collapse,
    # because None is the answer that has to block every close.
    human_idle = await asyncio.to_thread(human_idle_probe or human_idle_seconds)
    if human_idle is None:
        logger.debug("Keyboard idle unreadable; leaving GUI apps alone.")
        return []
    if human_idle < ttl:
        logger.debug("Someone is at the machine (%.0fs since last input); "
                     "leaving %d idle app(s) alone.", human_idle, len(ready))
        return []

    closed = []
    for r in ready:
        state, detail = await asyncio.to_thread(prober, r["needle"], r["bundle"])
        if state != "clean":
            logger.info("Leaving %s open: %s (%s)", r["bundle"], state, detail or "-")
            tracker.restart(r["pid"], now=now)
            continue
        footprint = await asyncio.to_thread(phys_footprint_mb, r["pid"])
        if footprint is not None:
            r["rss_mb"] = footprint
        logger.warning(
            "Closing %s (pid=%s): unused for %.0f min, nobody at the machine for "
            "%.0f min, nothing unsaved, ~%.0f MB.",
            r["bundle"], r["pid"], r["idle_seconds"] / 60.0, human_idle / 60.0,
            r["rss_mb"],
        )
        try:
            gone = await quitter(r["bundle"], r["pid"])
        except Exception as e:
            logger.error("Quit failed for %s: %s", r["bundle"], e)
            tracker.restart(r["pid"], now=now)
            continue
        if gone:
            tracker.forget(r["pid"])
            closed.append(r)
        else:
            logger.warning("%s did not quit (a dialog may be open); left running.",
                           r["bundle"])
            tracker.restart(r["pid"], now=now)
    if closed:
        freed = sum(c["rss_mb"] for c in closed)
        logger.info("Closed %d idle GUI app(s), freed ~%.0f MB", len(closed), freed)
    return closed


async def list_gui_process_lines() -> list:
    """Process table rows including cumulative CPU time. [] on any failure."""
    def _run() -> list:
        try:
            out = subprocess.run(
                ["ps", "-axo", "pid=,etime=,%cpu=,rss=,time=,command="],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [ln for ln in out.stdout.splitlines() if ln.strip()]

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return []


def describe_candidates(ps_lines=None, config=None, tracker=None) -> list:
    """Read-only view of what the sweep currently sees, for /maintenance and
    for checking the thing works without waiting an hour for it to fire.

    Strictly read-only: it peeks at the idle clocks rather than folding a
    sample in, so asking what the sweep can see never changes what it will do.
    """
    if config is None:
        from utils.maintenance import load_config
        config = load_config()
    needles = tuple(config.get("close_idle_gui_app_names") or DEFAULT_GUI_APPS)
    if ps_lines is None:
        def _run():
            try:
                out = subprocess.run(
                    ["ps", "-axo", "pid=,etime=,%cpu=,rss=,time=,command="],
                    capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                return []
            return [ln for ln in out.stdout.splitlines() if ln.strip()]
        ps_lines = _run()
    rows = sample_apps(ps_lines, needles, {os.getpid(), os.getppid()})
    if tracker is None:
        tracker = get_tracker()
    observed = tracker.peek(rows)
    for r in observed:
        # Only a handful of processes, and only when someone is looking.
        footprint = phys_footprint_mb(r["pid"])
        if footprint is not None:
            r["rss_mb"] = footprint
    return observed
