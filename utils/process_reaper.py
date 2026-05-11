#!/usr/bin/env python3
"""
Process reaper — kills background shell processes that have gone silent.

Closes the gap where an agent backgrounds a runaway shell command (e.g.
`find $HOME ... | head -20`), the command produces no output for many
minutes, and the agent loop sits polling it until BACKGROUND_TIMEOUT (1h)
fires. Result for the user: the bot appears hung for tens of minutes.

This watchdog scans the global ProcessRegistry on a fixed interval. Any
background process whose stdout/stderr has been silent for longer than the
silence threshold is force-killed (SIGTERM → SIGKILL) and tagged so the
next poll from the agent surfaces a clear "killed by watchdog" message
instead of an opaque hang.

Foreground processes are intentionally NOT touched here — they already have
the much tighter COMMAND_TIMEOUT (120s) enforced inline by tools.py.

Started as an asyncio task from bot.py post_init. Cheap: one ps-less scan
of an in-memory dict every interval.
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

_reaper_task: Optional[asyncio.Task] = None


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


async def _reaper_loop(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
) -> None:
    """Run reap_once on a fixed interval until cancelled."""
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
