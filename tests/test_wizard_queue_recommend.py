"""Regression tests for the multi-user queue recommendation logic.

The wizard probes RAM + logical CPU cores after the user picks a slot
count and uses _recommend_queue() to default the request-queue prompt.
This file pins the decision matrix so future tweaks to the thresholds
have to be deliberate.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install.wizard import _recommend_queue  # noqa: E402


class RecommendQueueTests(unittest.TestCase):
    def _check(self, ram_gb: float, cores: int, num_users: int, expected: str):
        got, verdict = _recommend_queue(ram_gb, cores, num_users)
        self.assertEqual(
            got, expected,
            f"ram={ram_gb} cores={cores} users={num_users}: "
            f"expected {expected!r} got {got!r} ({verdict})",
        )
        self.assertIn(verdict.strip()[:1] if verdict else "", verdict)

    def test_no_detection_defaults_to_queue(self):
        self._check(0, 0, 4, "y")

    def test_ram_missing_defaults_to_queue(self):
        # Cores alone is not enough — RAM is the dominant OOM signal.
        self._check(0, 8, 4, "y")

    def test_cores_missing_uses_ram_only(self):
        # 16 GB / 4 users = 4 GB per user → moderate, queue still on
        self._check(16.0, 0, 4, "y")
        # 32 GB / 2 users = 16 GB per user → comfortable
        self._check(32.0, 0, 2, "n")

    def test_tight_ram_triggers_queue(self):
        # 4 GB / 2 users = 2 GB per user → tight
        self._check(4.0, 8, 2, "y")

    def test_tight_cores_triggers_queue(self):
        # 32 GB / 2 cores / 4 users → 0.5 cores per user
        self._check(32.0, 2, 4, "y")

    def test_moderate_ram_triggers_queue(self):
        # 16 GB / 4 cores / 4 users → 4 GB & 1 core per user (boundary)
        self._check(16.0, 4, 4, "y")

    def test_comfortable_budget_disables_queue_default(self):
        # 32 GB / 8 cores / 4 users → 8 GB & 2 cores per user (boundary)
        self._check(32.0, 8, 4, "n")
        # 64 GB / 16 cores / 8 users → 8 GB & 2 cores per user
        self._check(64.0, 16, 8, "n")

    def test_single_user_comfortable(self):
        # Even modest hardware looks comfortable for a single user.
        self._check(8.0, 4, 1, "n")

    def test_max_slot_cap_8_users_on_16gb_box(self):
        # The new 8-user cap on a typical 16 GB / 4 core machine
        # should always recommend the queue.
        self._check(16.0, 4, 8, "y")

    def test_zero_users_does_not_divide_by_zero(self):
        # Defensive — _recommend_queue normalizes 0/negative to 1
        got, _ = _recommend_queue(16.0, 4, 0)
        self.assertIn(got, ("y", "n"))


if __name__ == "__main__":
    unittest.main()
