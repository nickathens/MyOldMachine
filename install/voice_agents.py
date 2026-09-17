#!/usr/bin/env python3
"""Choose when the voice engines load: at boot, or at the first voice message.

Two heavy models sit behind the voice pipeline. Speech-to-text is Whisper
large-v3-turbo (~2.5 GB resident) and text-to-speech is Chatterbox (~5 GB). On
a 24 GB machine that is roughly a third of memory, held whether or not anyone
has spoken all day, and it is a large part of why macOS reports memory
pressure on an otherwise quiet box.

Nothing in the pipeline actually requires that. Both daemons already self-exit
after IDLE_TIMEOUT of no requests, and both clients (data/stt/hear.py,
data/chatterbox/say.py) already start their daemon on demand and wait for it
to warm. The ONLY reason the models sit resident is a pair of LaunchAgents
installed outside this repo, which set RunAtLoad + KeepAlive and override the
idle timeout to 100 years so the self-exit can never fire. The two settings go
together: with KeepAlive on, a daemon that idled out would be restarted by
launchd within seconds and reload the model for nobody.

This module owns that choice, in both directions:

  on-demand   remove the agents, stop whatever they left resident, and let the
              clients start an engine when a voice message actually arrives.
  always-warm reinstall them from install/templates, for a machine with memory
              to spare that wants zero first-message latency.

The cost of on-demand is one warm-up on the first voice message after a quiet
spell, measured from this machine's own daemon logs: about 2 seconds to start
listening, about 15 seconds before it can speak. Everything after that, until
the idle window expires, is as fast as it is now.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))  # so `python3 install/voice_agents.py` finds utils
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
TEMPLATE_DIR = REPO_DIR / "install" / "templates"
# Removed agents are archived rather than deleted, so a machine can be put back
# the way it was even if the repo templates move on.
ARCHIVE_DIR = REPO_DIR / "data" / "voice_agents_removed"

# A daemon burning more than this share of one core is mid-request. Sampled
# live rather than trusted from a flag, because neither daemon reports whether
# it is busy and a transcription cut off halfway is a lost voice message.
BUSY_CPU_PERCENT = 10.0


class VoiceEngine:
    def __init__(self, key, label, human, port, subdir, daemon, idle_env):
        self.key = key
        self.label = label
        self.human = human
        self.port = port
        self.subdir = subdir
        self.daemon = daemon
        self.idle_env = idle_env

    @property
    def plist_path(self) -> Path:
        return LAUNCH_AGENTS / f"{self.label}.plist"

    @property
    def template_path(self) -> Path:
        return TEMPLATE_DIR / f"{self.label}.plist"

    @property
    def daemon_path(self) -> Path:
        return REPO_DIR / "data" / self.subdir / self.daemon


ENGINES = (
    VoiceEngine("stt", "com.coocoo.stt-whisper", "listening (speech to text)",
                8779, "stt", "stt_daemon.py", "STT_DAEMON_IDLE"),
    VoiceEngine("tts", "com.coocoo.tts-attenborough", "speaking (text to speech)",
                8778, "chatterbox", "tts_daemon.py", "TTS_DAEMON_IDLE"),
)


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def render_plist(template_text: str, repo_dir: Path) -> str:
    """Fill a LaunchAgent template. Pure, so it is testable."""
    return template_text.replace("{{REPO_DIR}}", str(repo_dir))


def plist_starts_at_boot(text: bytes) -> Optional[bool]:
    """True if this agent loads its model at login. None if unreadable."""
    try:
        data = plistlib.loads(text)
    except Exception:
        return None
    return bool(data.get("RunAtLoad")) or bool(data.get("KeepAlive"))


def parse_cpu_seconds(text: str) -> Optional[float]:
    """`ps -o time=` to seconds. Shared shape with utils.gui_app_closer."""
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
    try:
        values = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if not 1 <= len(values) <= 3:
        return None
    seconds = 0.0
    for v in values:
        seconds = seconds * 60.0 + v
    return days * 86400.0 + seconds


# --------------------------------------------------------------------------
# Machine state
# --------------------------------------------------------------------------

def _pids_for(engine: VoiceEngine) -> list:
    """Find the running engine by its daemon's FILENAME, not its full path.

    Matching the full path looks tighter and is worse: the daemon that is
    actually resident was started by whoever launched it first -- launchd, or a
    client, or a copy of the repo checked out somewhere else -- and a path that
    disagrees by one directory reports "not running" for an engine sitting on
    5 GB. The filenames are unique enough to stand alone.
    """
    try:
        r = subprocess.run(["pgrep", "-f", engine.daemon],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def _cpu_seconds(pid: int) -> Optional[float]:
    try:
        r = subprocess.run(["ps", "-o", "time=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_cpu_seconds(r.stdout)


def is_busy(pid: int, window: float = 3.0) -> bool:
    """True if the process is doing real work right now.

    Unknown counts as busy: if the CPU cannot be read, the daemon is left
    running rather than interrupted.
    """
    first = _cpu_seconds(pid)
    if first is None:
        return True
    time.sleep(window)
    second = _cpu_seconds(pid)
    if second is None:
        return True
    return (100.0 * (second - first) / window) > BUSY_CPU_PERCENT


def _port_answers(engine: VoiceEngine, timeout: float = 2.0) -> Optional[str]:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{engine.port}/health", timeout=timeout) as r:
            import json
            return json.loads(r.read()).get("status")
    except Exception:
        return None


def engine_state(engine: VoiceEngine) -> dict:
    plist = engine.plist_path
    at_boot = None
    if plist.exists():
        try:
            at_boot = plist_starts_at_boot(plist.read_bytes())
        except OSError:
            at_boot = None
    pids = _pids_for(engine)
    # Footprint, not RSS. An idle engine's model pages are compressed, and RSS
    # counts only what is left uncompressed: the speaking engine reads 37 MB by
    # RSS and 5028 MB by footprint. Reporting RSS here would say the models
    # cost nothing, which is the opposite of the point.
    rss = 0.0
    for pid in pids:
        mb = None
        try:
            from utils.gui_app_closer import phys_footprint_mb
            mb = phys_footprint_mb(pid)
        except Exception:
            mb = None
        if mb is None:
            try:
                r = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                                   capture_output=True, text=True, timeout=10)
                mb = int((r.stdout or "0").strip() or 0) / 1024.0
            except (OSError, subprocess.SubprocessError, ValueError):
                mb = 0.0
        rss += mb or 0.0
    return {
        "key": engine.key,
        "label": engine.label,
        "human": engine.human,
        "agent_installed": plist.exists(),
        "starts_at_boot": at_boot,
        "pids": pids,
        "rss_mb": rss,
        "health": _port_answers(engine),
    }


def status_report() -> str:
    lines = ["Voice engines", ""]
    for engine in ENGINES:
        s = engine_state(engine)
        mode = ("loads at boot and never exits" if s["starts_at_boot"]
                else "on demand" if not s["agent_installed"]
                else "agent installed but not set to start at boot")
        lines.append(f"{s['human']}: {mode}")
        if s["pids"]:
            lines.append(f"  running now: pid {', '.join(str(p) for p in s['pids'])}, "
                         f"~{s['rss_mb']:.0f} MB, health {s['health'] or 'no answer'}")
        elif s["health"]:
            lines.append(f"  answering on port {engine.port} ({s['health']}) but its "
                         f"process was not found")
        else:
            lines.append("  not running (starts on the first voice message)")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def _launchctl(args: list) -> tuple:
    try:
        r = subprocess.run(["launchctl"] + args, capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _unload(plist: Path) -> tuple:
    """bootout on modern launchd, falling back to the legacy unload verb."""
    ok, out = _launchctl(["bootout", f"gui/{os.getuid()}", str(plist)])
    if ok:
        return True, out
    ok2, out2 = _launchctl(["unload", "-w", str(plist)])
    return ok2, out2 or out


def _load(plist: Path) -> tuple:
    ok, out = _launchctl(["bootstrap", f"gui/{os.getuid()}", str(plist)])
    if ok:
        return True, out
    ok2, out2 = _launchctl(["load", "-w", str(plist)])
    return ok2, out2 or out


def set_on_demand(stop_running: bool = True) -> list:
    """Remove the always-warm agents. Returns a log of what happened."""
    log = []
    for engine in ENGINES:
        plist = engine.plist_path
        if plist.exists():
            ok, out = _unload(plist)
            log.append(f"{engine.human}: agent unloaded" if ok
                       else f"{engine.human}: unload reported '{out}'")
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(plist, ARCHIVE_DIR / plist.name)
                plist.unlink()
                log.append(f"  agent removed (a copy is kept in {ARCHIVE_DIR})")
            except OSError as e:
                log.append(f"  could not remove the agent file: {e}")
        else:
            log.append(f"{engine.human}: no always-warm agent installed")

        if not stop_running:
            continue
        for pid in _pids_for(engine):
            if is_busy(pid):
                log.append(f"  pid {pid} is mid-request, left running "
                           f"(it will exit on its own once idle)")
                continue
            try:
                os.kill(pid, 15)
                log.append(f"  stopped the resident engine (pid {pid}); "
                           f"it restarts on the next voice message")
            except OSError as e:
                log.append(f"  could not stop pid {pid}: {e}")
    return log


def set_always_warm() -> list:
    """Reinstall both agents from the repo templates."""
    log = []
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    for engine in ENGINES:
        template = engine.template_path
        if not template.exists():
            log.append(f"{engine.human}: template missing at {template}")
            continue
        text = render_plist(template.read_text(encoding="utf-8"), REPO_DIR)
        plist = engine.plist_path
        if plist.exists():
            _unload(plist)
        try:
            plist.write_text(text, encoding="utf-8")
        except OSError as e:
            log.append(f"{engine.human}: could not write the agent: {e}")
            continue
        ok, out = _load(plist)
        log.append(f"{engine.human}: always-warm agent installed and loaded" if ok
                   else f"{engine.human}: installed, but load reported '{out}'")
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("status", "on-demand", "always-warm"),
                    nargs="?", default="status")
    ap.add_argument("--keep-running", action="store_true",
                    help="on-demand: leave an already-loaded engine resident "
                         "instead of stopping it now")
    args = ap.parse_args(argv)

    if sys.platform != "darwin":
        print("macOS only: these are LaunchAgents.")
        return 1

    if args.action == "status":
        print(status_report())
        return 0
    if args.action == "on-demand":
        for line in set_on_demand(stop_running=not args.keep_running):
            print(line)
    else:
        for line in set_always_warm():
            print(line)
    print()
    print(status_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
