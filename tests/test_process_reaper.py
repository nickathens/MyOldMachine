"""Unit tests for utils.process_reaper.

The reaper protects against runaway background commands (e.g. an unbounded
`find` on $HOME piped into `head`) that produce no output for many minutes
and would otherwise pin the agent loop until BACKGROUND_TIMEOUT (1h).

These tests drive the watchdog directly without spinning the periodic loop:
register a real subprocess in the shared registry, fake its silence age,
call reap_once(), assert it was killed and tagged.
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.tools import get_process_registry  # noqa: E402
from utils import process_reaper  # noqa: E402


class ReapOnceTests(unittest.TestCase):
    """reap_once: kills silent backgrounds, leaves chatty + foreground alone."""

    def setUp(self):
        # Each test gets a clean registry to avoid cross-test bleed.
        reg = get_process_registry()
        asyncio.run(reg.cleanup_all())

    def tearDown(self):
        reg = get_process_registry()
        asyncio.run(reg.cleanup_all())

    async def _spawn(self, command: str, background: bool = True):
        """Spawn a real subprocess and register it like _run_command does."""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        reg = get_process_registry()
        return await reg.register(command, proc, background=background)

    def test_silent_background_is_reaped(self):
        async def scenario():
            # Long sleep == will never produce output on its own.
            managed = await self._spawn("sleep 600", background=True)
            self.assertTrue(managed.is_running)

            # Fake silence well past the threshold.
            managed.last_output_at = time.time() - 999

            reaped = await process_reaper.reap_once(silence_seconds=300)

            self.assertIn(managed.process_id, reaped)
            self.assertTrue(managed.reaped)
            self.assertFalse(managed.is_running)
            # Watchdog leaves a clear breadcrumb in the buffer.
            full = "".join(managed.output_chunks)
            self.assertIn("Killed by watchdog", full)

        asyncio.run(scenario())

    def test_chatty_background_is_left_alone(self):
        async def scenario():
            # last_output_at is initialized to start time → not silent.
            managed = await self._spawn("sleep 600", background=True)
            managed.last_output_at = time.time()  # fresh

            reaped = await process_reaper.reap_once(silence_seconds=300)

            self.assertNotIn(managed.process_id, reaped)
            self.assertFalse(managed.reaped)
            self.assertTrue(managed.is_running)

            # Clean up the still-running sleep.
            reg = get_process_registry()
            await reg.kill(managed.process_id)

        asyncio.run(scenario())

    def test_foreground_is_never_reaped(self):
        # Even if a foreground process is somehow silent past the threshold,
        # the reaper must not touch it — COMMAND_TIMEOUT handles foregrounds.
        async def scenario():
            managed = await self._spawn("sleep 600", background=False)
            managed.last_output_at = time.time() - 999  # very silent

            reaped = await process_reaper.reap_once(silence_seconds=300)

            self.assertNotIn(managed.process_id, reaped)
            self.assertFalse(managed.reaped)
            self.assertTrue(managed.is_running)

            reg = get_process_registry()
            await reg.kill(managed.process_id)

        asyncio.run(scenario())

    def test_finished_process_is_not_reaped(self):
        async def scenario():
            managed = await self._spawn("true", background=True)
            # Let it finish naturally.
            await managed.process.wait()
            managed.return_code = managed.process.returncode
            managed.finished_at = time.time()
            managed.last_output_at = time.time() - 999

            reaped = await process_reaper.reap_once(silence_seconds=300)

            # list_running() excludes finished, so reaper never sees it.
            self.assertNotIn(managed.process_id, reaped)
            self.assertFalse(managed.reaped)

        asyncio.run(scenario())

    def test_silence_threshold_is_respected(self):
        async def scenario():
            managed = await self._spawn("sleep 600", background=True)
            # Silent for 100s — below a 300s threshold.
            managed.last_output_at = time.time() - 100

            reaped = await process_reaper.reap_once(silence_seconds=300)

            self.assertNotIn(managed.process_id, reaped)
            self.assertFalse(managed.reaped)
            self.assertTrue(managed.is_running)

            reg = get_process_registry()
            await reg.kill(managed.process_id)

        asyncio.run(scenario())


class StartStopReaperTests(unittest.TestCase):
    """The async loop wrapper starts/stops cleanly and is idempotent."""

    def test_start_is_idempotent(self):
        async def scenario():
            t1 = process_reaper.start_reaper(interval_seconds=60, silence_seconds=600)
            t2 = process_reaper.start_reaper(interval_seconds=60, silence_seconds=600)
            self.assertIs(t1, t2)
            await process_reaper.stop_reaper()
            self.assertIsNone(process_reaper._reaper_task)

        asyncio.run(scenario())

    def test_stop_when_not_started_is_safe(self):
        async def scenario():
            process_reaper._reaper_task = None
            await process_reaper.stop_reaper()  # must not raise

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
