"""Pin the flat 10-minute progress cadence.

Run: python3 -m unittest tests.test_progress_cadence  (from repo root)
  or: python3 tests/test_progress_cadence.py

Between progress updates the user sees only a Telegram "typing" indicator.
An earlier change front-loaded the first few updates (25s/60s/150s) so a working
turn showed life quickly, but the user found those early heartbeats more
annoying than a quiet gap ("why are you giving me reports every minute"). The
cadence is now flat: no front-loaded burst, one update every PROGRESS_INTERVAL
(10 minutes), so a short turn finishes before the first update and stays silent.

These tests pin that shape: an empty PROGRESS_SCHEDULE, a 10-minute interval,
both providers in sync, and _next_progress_delay returning the interval for
every update including the first. A regression back toward front-loading or a
sub-10-minute interval should fail here, deliberately.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import (  # noqa: E402
    ClaudeCLIProvider,
    CodexCLIProvider,
    _format_elapsed,
    _next_progress_delay,
)

PROVIDERS = (ClaudeCLIProvider, CodexCLIProvider)


class TestFormatElapsed(unittest.TestCase):
    def test_seconds_under_a_minute(self):
        # The sub-minute branch is unreached at a 10-minute cadence but kept for
        # safety; pin it so the helper stays correct.
        self.assertEqual(_format_elapsed(0), "0s")
        self.assertEqual(_format_elapsed(25), "25s")
        self.assertEqual(_format_elapsed(59.9), "59s")

    def test_minutes_at_and_above_one(self):
        self.assertEqual(_format_elapsed(60), "1 min")
        self.assertEqual(_format_elapsed(125), "2 min")
        # The first update a user actually sees lands here, at the 10-minute mark.
        self.assertEqual(_format_elapsed(600), "10 min")


class TestProgressCadence(unittest.TestCase):
    def test_both_providers_share_the_cadence(self):
        # The two providers run parallel loops; their cadence must not drift.
        self.assertEqual(
            ClaudeCLIProvider.PROGRESS_SCHEDULE, CodexCLIProvider.PROGRESS_SCHEDULE
        )
        self.assertEqual(
            ClaudeCLIProvider.PROGRESS_INTERVAL, CodexCLIProvider.PROGRESS_INTERVAL
        )

    def test_no_front_loaded_schedule(self):
        # The core ask: no early burst of heartbeats. Empty schedule => the first
        # update waits the full interval like every other update.
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                self.assertEqual(provider.PROGRESS_SCHEDULE, ())

    def test_interval_is_ten_minutes(self):
        # Flat 10-minute cadence. Guard against re-tightening to a chatty gap.
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                self.assertEqual(provider.PROGRESS_INTERVAL, 600)


class TestNextProgressDelay(unittest.TestCase):
    def test_every_update_holds_the_interval(self):
        # With an empty schedule, every update — including the first (count=0) —
        # waits the full interval, so a short turn finishes before any heartbeat.
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                for count in (0, 1, 2, 5, 50):
                    self.assertEqual(
                        _next_progress_delay(provider, count),
                        provider.PROGRESS_INTERVAL,
                    )


if __name__ == "__main__":
    unittest.main()
