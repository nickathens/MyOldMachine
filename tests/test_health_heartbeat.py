#!/usr/bin/env python3
"""Unit tests for core.health PollingHealthMonitor poll-cycle watchdog.

Run: python3 -m unittest tests.test_health_heartbeat  (from repo root)

Regression coverage for the watchdog false-positive bug: the old monitor took
recovery action whenever *no inbound Telegram updates* had arrived for 30
minutes. That fired during normal quiet periods (nobody texting) and churned
recovery for no reason. On a MOM install WITHOUT a local Bot API the recovery
action is os._exit(1), so silence alone would restart the entire bot.

The fix keys recovery on a poll-cycle heartbeat (last_poll_time), refreshed every
time a getUpdates round-trip completes. Because PTB long-polls, a healthy loop
ticks every few seconds regardless of whether anyone texts, so a stale heartbeat
is the only condition that actually means the poll loop is dead.

These tests stub the network probe and the restart action so the decision logic
is exercised deterministically, with no sockets and no systemctl.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.health as health  # noqa: E402
from core.health import PollingHealthMonitor  # noqa: E402


class _ExitSentinel(Exception):
    """Raised by the patched os._exit so the test can assert it fired."""


class WatchdogHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.m = PollingHealthMonitor(local_api_base="http://localhost:8081")
        self.restart_calls: list[str] = []
        # Default world: internet up, local-API restart succeeds.
        self.internet_ok = True
        self.restart_succeeds = True

        async def _fake_restart(reason):
            self.restart_calls.append(reason)
            return self.restart_succeeds

        async def _fake_internet():
            return self.internet_ok

        self.m._restart_local_api = _fake_restart
        self.m._check_internet = _fake_internet

    def _stale(self) -> float:
        return time.monotonic() - (health.POLLING_POLL_STALE_THRESHOLD + 60)

    async def test_none_heartbeat_is_safe_mode_no_recovery(self):
        # Heartbeat never landed (hook missing or loop warming up): never recover.
        self.m.last_poll_time = None
        await self.m._check()
        self.assertEqual(self.restart_calls, [])
        self.assertTrue(self.m._warned_no_heartbeat)
        # Second call stays silent and still never recovers.
        await self.m._check()
        self.assertEqual(self.restart_calls, [])

    async def test_fresh_heartbeat_no_recovery_even_when_chat_is_silent(self):
        # THE core regression: nobody is texting (ancient inbound time) but the
        # poll loop is ticking => absolutely no recovery.
        now = time.monotonic()
        self.m.last_poll_time = now
        self.m.last_update_time = now - 99999
        await self.m._check()
        self.assertEqual(self.restart_calls, [])
        self.assertFalse(self.m._was_stale)

    async def test_stale_heartbeat_requires_two_cycles_then_restarts(self):
        self.m.last_poll_time = self._stale()
        # Cycle 1: confirm only, no restart yet.
        await self.m._check()
        self.assertEqual(self.restart_calls, [])
        self.assertTrue(self.m._was_stale)
        # Cycle 2: restart the local API.
        await self.m._check()
        self.assertEqual(len(self.restart_calls), 1)
        self.assertIn("no getUpdates cycles", self.restart_calls[0])

    async def test_stale_heartbeat_with_internet_down_does_not_recover(self):
        self.internet_ok = False
        self.m.last_poll_time = self._stale()
        await self.m._check()
        self.assertEqual(self.restart_calls, [])
        self.assertTrue(self.m.internet_was_down)
        self.assertFalse(self.m._was_stale)

    async def test_internet_restored_after_outage_restarts(self):
        self.m.internet_was_down = True
        self.internet_ok = True
        self.m.last_poll_time = self._stale()
        await self.m._check()
        self.assertEqual(len(self.restart_calls), 1)
        self.assertIn("Internet restored", self.restart_calls[0])
        self.assertFalse(self.m.internet_was_down)

    async def test_successful_restart_resets_heartbeat(self):
        # After a successful local-API restart the heartbeat is refreshed so the
        # next check does not immediately re-trigger.
        self.m.last_poll_time = self._stale()
        self.restart_succeeds = True
        await self.m._check()  # confirm
        await self.m._check()  # restart
        self.assertEqual(len(self.restart_calls), 1)
        self.assertLess(time.monotonic() - self.m.last_poll_time, 5)
        self.assertFalse(self.m._was_stale)

    async def test_no_local_api_exits_process_when_wedged(self):
        # MOM-specific: without a local Bot API, a wedged loop must exit the
        # process (systemd/launchd restarts it) instead of silently churning.
        self.m.local_api_base = None
        self.m.last_poll_time = self._stale()
        exits: list[int] = []

        def _fake_exit(code):
            exits.append(code)
            raise _ExitSentinel()

        orig = health.os._exit
        health.os._exit = _fake_exit
        try:
            await self.m._check()  # confirm, no exit yet
            self.assertEqual(exits, [])
            with self.assertRaises(_ExitSentinel):
                await self.m._check()  # second stale check => os._exit(1)
        finally:
            health.os._exit = orig
        self.assertEqual(exits, [1])

    async def test_cooldown_blocks_rapid_recovery(self):
        # A recovery moments ago must suppress another within the cooldown window.
        self.m.last_poll_time = self._stale()
        await self.m._check()  # confirm => _was_stale True
        self.m.last_recovery_time = time.monotonic()  # pretend we just recovered
        await self.m._check()  # would restart, but cooldown active
        self.assertEqual(self.restart_calls, [])

    async def test_record_poll_cycle_sets_heartbeat(self):
        self.m.last_poll_time = None
        self.m.record_poll_cycle()
        self.assertIsNotNone(self.m.last_poll_time)

    async def test_record_update_does_not_count_as_poll_liveness(self):
        # An inbound message must NOT be mistaken for poll-loop liveness.
        self.m.last_poll_time = None
        self.m.record_update()
        self.assertIsNone(self.m.last_poll_time)


if __name__ == "__main__":
    unittest.main()
