#!/usr/bin/env python3
"""
Process reaper — two janitors on one background loop.

1. Silent background shell commands (reap_once). Closes the gap where an
   agent backgrounds a runaway shell command (e.g. `find $HOME ... | head`),
   the command produces no output for many minutes, and the agent loop sits
   polling it until BACKGROUND_TIMEOUT (1h) fires. Any registered background
   process silent past the threshold is force-killed (SIGTERM → SIGKILL) and
   tagged so the next poll surfaces a clear "killed by watchdog" message.
   Foreground processes are left alone — tools.py enforces COMMAND_TIMEOUT
   (120s) on those inline. This scan is a ps-less walk of an in-memory dict.

2. Idle heavyweight helper apps (reap_idle_apps). Skills launch LibreOffice,
   GIMP, Inkscape and Blender headless to convert or render, then exit —
   but those apps are started detached (start_new_session) as socket daemons
   and get orphaned to launchd/init, lingering for hours holding hundreds of
   MB (a converted spreadsheet leaves a ~300 MB soffice.bin resident). Unlike
   case 1 these are NOT in our registry, so this sweep reads the system
   process table and closes an app only when ALL of: it was launched headless
   or in batch mode (the safety latch — a GUI copy the user opened by hand is
   never matched, so no unsaved work is ever killed), it is older than a TTL,
   AND it is sitting near-idle on CPU (a live render or mid-flight conversion
   is busy and never touched). Chrome/Chromium is also excluded outright — the
   browser skill keeps it warm as a daemon on purpose.

Both are controlled by data/maintenance.json (reap_idle_apps toggle) and run
from one asyncio task started in bot.py post_init.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Optional

logger = logging.getLogger("process_reaper")

# Defaults — tuned for "the agent shouldn't sit on a silent background
# command for more than ~5 minutes." A long install with progress output is
# fine: the watchdog only fires when nothing is written to stdout/stderr.
DEFAULT_SILENCE_SECONDS = 300  # 5 minutes
DEFAULT_INTERVAL_SECONDS = 30  # Scan every 30s

# --- Idle heavyweight-app sweep (case 2) ---------------------------------
# Substring needles matched against each process's EXECUTABLE basename. These
# are the headless "do a job then should exit" helpers skills spawn. Chrome and
# Chromium are intentionally absent: the browser skill keeps a Chromium daemon
# warm between commands by design, and node/python are never listed so the bot
# and Remotion/MCP workers can never match.
DEFAULT_IDLE_APPS = ("soffice", "gimp", "inkscape", "blender")
# The safety latch. A candidate is reaped ONLY if its command line also carries
# one of these headless/batch markers — which is how every skill launches these
# apps (soffice --headless --accept=socket, gimp -i -b, blender -b/--background).
# A GUI copy the user opened by hand never carries them, so an interactively
# opened Blender or LibreOffice document is provably never touched, even idle.
HEADLESS_TOKENS = frozenset({"--headless", "--background", "--batch", "-b", "--shell"})
HEADLESS_PREFIXES = ("--accept=socket", "--export")
# An idle helper older than this is an orphan, not an in-flight job. Spreadsheet
# and image conversions finish in seconds; nothing legitimate keeps one of
# these resident-and-idle for 20 minutes.
DEFAULT_APP_IDLE_MINUTES = 20
# %CPU at or below this counts as idle. A live render or conversion sits far
# above it, so it is never reaped; a parked orphan sits at ~0.
DEFAULT_APP_IDLE_CPU = 5.0
# The app sweep needs far less than 30s granularity, and each pass shells out
# to `ps`. Run it at most this often regardless of the loop interval.
DEFAULT_APP_SWEEP_SECONDS = 120

_reaper_task: Optional[asyncio.Task] = None
# Wall-clock of the last idle-app sweep, for the throttle above.
_last_app_sweep_at: float = 0.0


async def _kill_process_group(pid: int, grace: float = 3.0) -> None:
    """SIGTERM the process group, wait briefly, then SIGKILL stragglers."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return

    await asyncio.sleep(grace)

    try:
        os.kill(pid, 0)  # Still alive?
    except OSError:
        return  # Already dead, good.

    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def reap_once(silence_seconds: float = DEFAULT_SILENCE_SECONDS) -> list[str]:
    """One pass over the registry. Returns process_ids that were reaped.

    Public + cheap so tests can drive a single tick without spinning the loop.
    """
    # Local import to avoid a circular import at module load.
    from core.tools import get_process_registry

    registry = get_process_registry()
    reaped: list[str] = []
    now = time.time()

    for managed in list(registry.list_running()):
        # Only reap backgrounds — foregrounds have COMMAND_TIMEOUT inline.
        if not managed.is_background:
            continue

        silent = now - managed.last_output_at
        if silent < silence_seconds:
            continue

        pid = managed.process.pid if managed.process else None
        cmd_preview = managed.command[:80]
        logger.warning(
            "Reaping silent background process %s (pid=%s, silent=%.0fs): %s",
            managed.process_id, pid, silent, cmd_preview,
        )

        if pid is not None:
            try:
                await _kill_process_group(pid)
            except Exception as e:
                logger.error("Reaper kill failed for %s: %s", managed.process_id, e)

        # Wait briefly for the process to actually exit, then mark it.
        try:
            await asyncio.wait_for(managed.process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, AttributeError):
            pass

        managed.reaped = True
        managed.return_code = managed.process.returncode if managed.process else -1
        managed.finished_at = time.time()
        managed.output_chunks.append(
            f"\n[Killed by watchdog: no output for {silent:.0f}s "
            f"(threshold {silence_seconds:.0f}s). "
            "If this command needs to run longer, write progress to stdout, "
            "or scope it tighter — e.g. find with -maxdepth and a specific "
            "path instead of $HOME.]\n"
        )
        reaped.append(managed.process_id)

    return reaped


# --- Idle heavyweight-app sweep (case 2) ---------------------------------

def _parse_etime(etime: str) -> Optional[float]:
    """Parse a `ps` ELAPSED field into seconds.

    Formats: "MM:SS", "HH:MM:SS", "DD-HH:MM:SS". Returns None if unparseable
    so a malformed row is skipped rather than mis-reaped.
    """
    etime = etime.strip()
    if not etime:
        return None
    days = 0
    if "-" in etime:
        day_part, _, etime = etime.partition("-")
        try:
            days = int(day_part)
        except ValueError:
            return None
    parts = etime.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        hours, minutes, seconds = 0, nums[0], nums[1]
    elif len(nums) == 3:
        hours, minutes, seconds = nums
    else:
        return None
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _is_headless_launch(tokens: list[str]) -> bool:
    """True if the argv carries a headless/batch marker (a skill launch)."""
    for tok in tokens:
        if tok in HEADLESS_TOKENS:
            return True
        if any(tok.startswith(p) for p in HEADLESS_PREFIXES):
            return True
    return False


def _select_idle_apps(
    ps_lines: list[str],
    needles: tuple,
    ttl_seconds: float,
    cpu_idle: float,
    self_pids: set,
) -> list[dict]:
    """Pure filter over `ps` output. No I/O, no killing — unit-testable.

    Each line is "PID ELAPSED %CPU RSS COMMAND" (COMMAND is the full argv). A
    row is selected only when ALL hold:
      - the executable basename (first argv token) matches a listed app, so a
        process that merely mentions "blender" in a later argument never matches;
      - the argv carries a headless/batch marker, so an interactively opened GUI
        app is never selected even when old and idle;
      - it is older than ttl_seconds;
      - it is at or below cpu_idle.
    """
    selected: list[dict] = []
    for line in ps_lines:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid_s, etime_s, cpu_s, rss_s, command = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid in self_pids:
            continue  # never reap the bot itself
        tokens = command.split()
        if not tokens:
            continue
        base = os.path.basename(tokens[0]).lower()
        if not any(n in base for n in needles):
            continue
        if not _is_headless_launch(tokens):
            continue  # a GUI copy opened by hand — leave it and its work alone
        age = _parse_etime(etime_s)
        if age is None or age < ttl_seconds:
            continue
        try:
            cpu = float(cpu_s)
        except ValueError:
            continue
        if cpu > cpu_idle:
            continue  # actively rendering/converting — leave it alone
        try:
            rss_mb = int(rss_s) / 1024.0
        except ValueError:
            rss_mb = 0.0
        selected.append({
            "pid": pid,
            "name": base,
            "age_seconds": age,
            "cpu": cpu,
            "rss_mb": rss_mb,
            "comm": command,
        })
    return selected


async def _list_process_lines() -> list[str]:
    """Snapshot the process table as `ps` rows, off the event loop.

    Fields: pid, elapsed, %cpu, rss(KB), command(full argv). `command` is last
    so its spaces stay in one field. Returns [] on any failure — a janitor that
    can't read the table simply does nothing this pass.
    """
    def _run() -> list[str]:
        import subprocess
        try:
            out = subprocess.run(
                ["ps", "-axo", "pid=,etime=,%cpu=,rss=,command="],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [ln for ln in out.stdout.splitlines() if ln.strip()]

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return []


async def reap_idle_apps(
    config: Optional[dict] = None,
    ps_lines: Optional[list[str]] = None,
    killer=None,
) -> list[dict]:
    """Close idle, orphaned heavyweight helper apps. Returns reaped descriptors.

    Public + fully injectable (config, ps_lines, killer) so tests drive it with
    synthetic rows and a fake killer — no real GUI app required.
    """
    if config is None:
        from utils.maintenance import load_config
        config = load_config()

    if not config.get("reap_idle_apps", True):
        return []

    minutes = config.get("reap_idle_app_minutes", DEFAULT_APP_IDLE_MINUTES)
    try:
        ttl_seconds = float(minutes) * 60.0
    except (TypeError, ValueError):
        ttl_seconds = DEFAULT_APP_IDLE_MINUTES * 60.0
    cpu_idle = config.get("reap_idle_app_cpu", DEFAULT_APP_IDLE_CPU)
    needles = tuple(config.get("reap_idle_app_names") or DEFAULT_IDLE_APPS)

    if ps_lines is None:
        ps_lines = await _list_process_lines()

    self_pids = {os.getpid(), os.getppid()}
    candidates = _select_idle_apps(ps_lines, needles, ttl_seconds, cpu_idle, self_pids)
    if killer is None:
        killer = _kill_process_group

    reaped: list[dict] = []
    for c in candidates:
        logger.warning(
            "Reaping idle %s (pid=%s, idle for %.0fs at %.1f%% CPU, ~%.0f MB): %s",
            c["name"], c["pid"], c["age_seconds"], c["cpu"], c["rss_mb"], c["comm"],
        )
        try:
            await killer(c["pid"])
            reaped.append(c)
        except Exception as e:
            logger.error("Idle-app reap failed for pid %s: %s", c["pid"], e)
    if reaped:
        freed = sum(c["rss_mb"] for c in reaped)
        logger.info("Idle-app sweep closed %d app(s), freed ~%.0f MB", len(reaped), freed)
    return reaped


async def _reaper_loop(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
) -> None:
    """Run both janitors on a fixed interval until cancelled.

    reap_once runs every tick (cheap, in-memory). The idle-app sweep shells out
    to `ps`, so it is throttled to DEFAULT_APP_SWEEP_SECONDS regardless of how
    fast the loop ticks.
    """
    global _last_app_sweep_at
    logger.info(
        "Process reaper started (interval=%.0fs, silence_threshold=%.0fs)",
        interval_seconds, silence_seconds,
    )
    try:
        while True:
            try:
                await reap_once(silence_seconds=silence_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Reaper tick failed (non-fatal): %s", e)

            now = time.time()
            if now - _last_app_sweep_at >= DEFAULT_APP_SWEEP_SECONDS:
                _last_app_sweep_at = now
                try:
                    await reap_idle_apps()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("Idle-app sweep failed (non-fatal): %s", e)

            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("Process reaper stopped.")
        raise


def start_reaper(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
) -> asyncio.Task:
    """Start the reaper as a background asyncio task.

    Idempotent: returns the existing task if already running.
    """
    global _reaper_task
    if _reaper_task is not None and not _reaper_task.done():
        return _reaper_task
    _reaper_task = asyncio.create_task(
        _reaper_loop(interval_seconds=interval_seconds, silence_seconds=silence_seconds)
    )
    return _reaper_task


async def stop_reaper() -> None:
    """Cancel the reaper task and wait for it to exit."""
    global _reaper_task
    if _reaper_task is None:
        return
    _reaper_task.cancel()
    try:
        await _reaper_task
    except (asyncio.CancelledError, Exception):
        pass
    _reaper_task = None
