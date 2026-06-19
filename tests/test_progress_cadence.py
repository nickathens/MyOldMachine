"""Pin the front-loaded progress cadence that keeps a working turn from looking frozen.

Run: python3 -m unittest tests.test_progress_cadence  (from repo root)
  or: python3 tests/test_progress_cadence.py

Between progress updates the user sees only a Telegram "typing" indicator.
The old cadence sent the first update after a flat 10-minute gap, so every
multi-minute turn looked frozen even while Claude/Codex worked normally —
the recurring "you hang on every reply" complaint.

The fix front-loads PROGRESS_SCHEDULE (seconds before each of the first few
updates) so a healthy turn shows life within seconds, then holds at
PROGRESS_INTERVAL so a long job settles into a calm cadence. These tests pin
that shape: the first update must stay well under a minute, the schedule must
be non-decreasing, and the steady-state interval must stay tight. A regression
back toward a multi-minute first gap should fail here, deliberately.
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
        # A bare "0 min elapsed" header read as broken; show seconds instead.
        self.assertEqual(_format_elapsed(0), "0s")
        self.assertEqual(_format_elapsed(25), "25s")
        self.assertEqual(_format_elapsed(59.9), "59s")

    def test_minutes_at_and_above_one(self):
        self.assertEqual(_format_elapsed(60), "1 min")
        self.assertEqual(_format_elapsed(125), "2 min")


class TestProgressSchedule(unittest.TestCase):
    def test_both_providers_share_the_cadence(self):
        # The two providers run parallel loops; their cadence must not drift.
        self.assertEqual(
            ClaudeCLIProvider.PROGRESS_SCHEDULE, CodexCLIProvider.PROGRESS_SCHEDULE
        )
        self.assertEqual(
            ClaudeCLIProvider.PROGRESS_INTERVAL, CodexCLIProvider.PROGRESS_INTERVAL
        )

    def test_first_update_is_well_under_a_minute(self):
        # The core fix: the first heartbeat must arrive fast, not after minutes.
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                self.assertTrue(provider.PROGRESS_SCHEDULE, "schedule must be non-empty")
                self.assertLessEqual(provider.PROGRESS_SCHEDULE[0], 30)

    def test_schedule_is_non_decreasing(self):
        # Updates should space out over time, never tighten.
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                schedule = provider.PROGRESS_SCHEDULE
                for earlier, later in zip(schedule, schedule[1:]):
                    self.assertLessEqual(earlier, later)

    def test_steady_state_interval_stays_tight(self):
        # Guard against regressing to the old 10-minute gap that looked frozen.
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                self.assertLessEqual(provider.PROGRESS_INTERVAL, 300)
                self.assertGreaterEqual(provider.PROGRESS_INTERVAL, schedule_tail(provider))


class TestNextProgressDelay(unittest.TestCase):
    def test_walks_schedule_then_holds_interval(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider.__name__):
                schedule = provider.PROGRESS_SCHEDULE
                for count, expected in enumerate(schedule):
                    self.assertEqual(_next_progress_delay(provider, count), expected)
                # Once the schedule is spent, every later update holds at the interval.
                self.assertEqual(
                    _next_progress_delay(provider, len(schedule)),
                    provider.PROGRESS_INTERVAL,
                )
                self.assertEqual(
                    _next_progress_delay(provider, len(schedule) + 50),
                    provider.PROGRESS_INTERVAL,
                )


def schedule_tail(provider) -> float:
    """Last scheduled delay — the interval should be >= this so cadence never tightens."""
    return provider.PROGRESS_SCHEDULE[-1] if provider.PROGRESS_SCHEDULE else 0


if __name__ == "__main__":
    unittest.main()
