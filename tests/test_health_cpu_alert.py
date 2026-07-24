#!/usr/bin/env python3
"""Unit tests for the core.health CPU false-alarm fix.

Run: python3 -m unittest tests.test_health_cpu_alert  (from repo root)

Regression coverage for the daily post-restart "CPU at 100%" false alarm. The
old check was wrong three ways: (1) it reported load average as if it were CPU%,
clamped to 100, so any load above core-count read as exactly 100%; (2) it said
"sustained" off a SINGLE sample; (3) it took that sample ~60s into a cold boot,
inside the startup storm — guaranteeing a false alert after every restart,
nightly.

The fix: measure REAL CPU busy%, require N consecutive breaching checks (like
the existing network guard), and suppress CPU/RAM/swap alerts until the machine
is past a post-boot settling window. Disk and network alerts stay live from
second one.

These tests stub every system reader so the decision logic is exercised
deterministically, with no subprocesses and no real /proc or sysctl.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.health as health  # noqa: E402


# Healthy baselines — nothing breaching. Individual tests override as needed.
_OK_DISK = {"total_gb": 460.0, "used_gb": 200.0, "free_gb": 260.0, "percent": 43.0}
_OK_MEM = {"total_gb": 24.0, "used_gb": 8.0, "free_gb": 16.0, "percent": 33.0}
_OK_SWAP = {"total_gb": 4.0, "used_gb": 0.5, "free_gb": 3.5, "percent": 12.0}
_SETTLED = health._SETTLING_WINDOW_SECONDS + 60  # comfortably past the window
_BOOTING = 30.0                                  # deep inside the settling window


class CheckCriticalCpuTests(unittest.TestCase):
    def setUp(self):
        # Reset the module de-noising counters before each test.
        health._consecutive_cpu_breaches = 0
        health._consecutive_net_failures = 0

        # Patch every reader check_critical() calls to a healthy default; tests
        # override individual return_values through the stored mocks.
        self._patchers = {
            "disk": patch.object(health, "get_disk_usage", return_value=_OK_DISK),
            "mem": patch.object(health, "get_memory_usage", return_value=_OK_MEM),
            "swap": patch.object(health, "get_swap_usage", return_value=_OK_SWAP),
            "cpu": patch.object(health, "get_cpu_usage", return_value=5.0),
            "net": patch.object(health, "get_network_status", return_value=True),
            "uptime": patch.object(
                health, "get_system_uptime_seconds", return_value=_SETTLED
            ),
            "load": patch.object(
                health, "get_load_average", return_value="1.00, 1.00, 1.00"
            ),
        }
        self.mocks = {name: p.start() for name, p in self._patchers.items()}
        self.addCleanup(lambda: [p.stop() for p in self._patchers.values()])

    # --- Baseline ---

    def test_healthy_machine_no_alerts(self):
        self.assertEqual(health.check_critical(), [])

    # --- Fix B: "sustained" must mean N consecutive breaches ---

    def test_single_cpu_spike_is_silent(self):
        self.mocks["cpu"].return_value = 99.0
        # One breaching check must NOT alert (threshold is 3).
        self.assertEqual(health.check_critical(), [])
        self.assertEqual(health._consecutive_cpu_breaches, 1)

    def test_cpu_alerts_only_after_threshold_consecutive_breaches(self):
        self.mocks["cpu"].return_value = 99.0
        # The first (threshold - 1) breaching checks stay silent...
        for _ in range(health._CPU_BREACH_THRESHOLD - 1):
            self.assertEqual(health.check_critical(), [])
        # ...the threshold-th check finally speaks.
        alerts = health.check_critical()
        cpu_alerts = [a for a in alerts if "CPU" in a]
        self.assertEqual(len(cpu_alerts), 1)
        self.assertIn("CPU busy at 99.0%", cpu_alerts[0])

    def test_cpu_breach_streak_resets_when_it_drops(self):
        self.mocks["cpu"].return_value = 99.0
        health.check_critical()
        health.check_critical()
        self.assertEqual(health._consecutive_cpu_breaches, 2)
        # A calm reading clears the streak — a later spike starts from zero.
        self.mocks["cpu"].return_value = 10.0
        health.check_critical()
        self.assertEqual(health._consecutive_cpu_breaches, 0)

    def test_message_reports_busy_not_load_and_drops_false_sustained(self):
        # The old message read "CPU load sustained at X%" off ONE sample. The new
        # one only fires after a real streak and reports busy%, not load.
        self.mocks["cpu"].return_value = 99.0
        alerts: list[str] = []
        for _ in range(health._CPU_BREACH_THRESHOLD):
            alerts = health.check_critical()
        msg = [a for a in alerts if "CPU" in a][0]
        self.assertIn("busy", msg)
        self.assertIn("consecutive checks", msg)
        self.assertNotIn("CPU load sustained", msg)

    def test_cpu_none_reading_never_alerts_and_holds_streak_low(self):
        # If the platform reading is unavailable, get_cpu_usage returns None and
        # the CPU branch must neither alert nor advance the streak.
        self.mocks["cpu"].return_value = None
        for _ in range(health._CPU_BREACH_THRESHOLD + 2):
            self.assertEqual(health.check_critical(), [])
        self.assertEqual(health._consecutive_cpu_breaches, 0)

    # --- Fix C: settling window suppresses boot-time noise ---

    def test_booting_machine_suppresses_cpu_ram_swap(self):
        self.mocks["uptime"].return_value = _BOOTING
        self.mocks["cpu"].return_value = 100.0
        self.mocks["mem"].return_value = {
            "total_gb": 24.0, "used_gb": 23.5, "free_gb": 0.5, "percent": 98.0
        }
        self.mocks["swap"].return_value = {
            "total_gb": 4.0, "used_gb": 3.9, "free_gb": 0.1, "percent": 97.0
        }
        # Even repeated breaches during the settling window stay silent.
        for _ in range(health._CPU_BREACH_THRESHOLD + 1):
            alerts = health.check_critical()
        noisy = [a for a in alerts if "CPU" in a or "RAM" in a or "Swap" in a]
        self.assertEqual(noisy, [])

    def test_booting_machine_still_reports_disk_and_network(self):
        self.mocks["uptime"].return_value = _BOOTING
        self.mocks["disk"].return_value = {
            "total_gb": 460.0, "used_gb": 459.0, "free_gb": 1.0, "percent": 99.8
        }
        # Network needs two consecutive failures (threshold 2); drive both.
        self.mocks["net"].return_value = False
        health.check_critical()
        alerts = health.check_critical()
        self.assertTrue(any("Disk almost full" in a for a in alerts))
        self.assertTrue(any("No internet" in a for a in alerts))

    def test_settling_window_clears_pending_cpu_streak(self):
        # A breach streak built up while settled must not survive a reboot: the
        # first booting check resets it so nothing carries across the boot.
        self.mocks["cpu"].return_value = 99.0
        health.check_critical()
        health.check_critical()
        self.assertEqual(health._consecutive_cpu_breaches, 2)
        self.mocks["uptime"].return_value = _BOOTING
        health.check_critical()
        self.assertEqual(health._consecutive_cpu_breaches, 0)

    def test_unknown_uptime_is_treated_as_settled(self):
        # If uptime cannot be read we must NOT mute real alerts forever.
        self.mocks["uptime"].return_value = None
        self.mocks["cpu"].return_value = 99.0
        alerts: list[str] = []
        for _ in range(health._CPU_BREACH_THRESHOLD):
            alerts = health.check_critical()
        self.assertTrue(any("CPU busy" in a for a in alerts))


class GetCpuUsageTests(unittest.TestCase):
    """get_cpu_usage must report REAL busy%, parsed from a true interval sample,
    not load average (which is what produced the clamped false 100%)."""

    _TOP_TWO_SAMPLES = (
        "Processes: 400 total\n"
        "Load Avg: 17.00, 12.00, 9.00\n"
        "CPU usage: 50.00% user, 30.00% sys, 20.00% idle\n"  # 1st = since-boot, ignored
        "PhysMem: ...\n"
        "Processes: 400 total\n"
        "Load Avg: 17.00, 12.00, 9.00\n"
        "CPU usage: 2.00% user, 3.00% sys, 95.00% idle\n"    # 2nd = interval, used
        "PhysMem: ...\n"
    )

    def test_darwin_parses_last_cpu_usage_line(self):
        completed = MagicMock()
        completed.stdout = self._TOP_TWO_SAMPLES
        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.subprocess, "run", return_value=completed):
            busy = health.get_cpu_usage()
        # busy = 100 - idle of the SECOND sample = 100 - 95 = 5.0.
        # (Load average is 17 — the old code would have clamped this to 100.)
        self.assertEqual(busy, 5.0)

    def test_darwin_high_busy_is_reported(self):
        completed = MagicMock()
        completed.stdout = "CPU usage: 80.00% user, 18.00% sys, 2.00% idle\n"
        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.subprocess, "run", return_value=completed):
            busy = health.get_cpu_usage()
        self.assertEqual(busy, 98.0)

    def test_darwin_unparseable_returns_none(self):
        completed = MagicMock()
        completed.stdout = "garbage without a cpu line\n"
        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.subprocess, "run", return_value=completed):
            self.assertIsNone(health.get_cpu_usage())

    def test_linux_computes_busy_from_proc_stat_delta(self):
        # Two /proc/stat snapshots: over the interval 100 of 1000 jiffies were
        # non-idle => 10% busy.
        snapshots = [(1000.0, 10000.0), (1100.0, 11000.0)]  # (busy, total)
        with patch.object(health.platform, "system", return_value="Linux"), \
             patch.object(health, "_read_proc_stat_cpu", side_effect=snapshots), \
             patch.object(health.time, "sleep", return_value=None):
            busy = health.get_cpu_usage()
        self.assertEqual(busy, 10.0)


class GetSystemUptimeSecondsTests(unittest.TestCase):
    def test_darwin_parses_kern_boottime(self):
        completed = MagicMock()
        completed.stdout = "{ sec = 1000, usec = 0 } Thu Jan  1 00:00:00 1970"
        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.subprocess, "run", return_value=completed), \
             patch.object(health.time, "time", return_value=1300.0):
            secs = health.get_system_uptime_seconds()
        self.assertEqual(secs, 300.0)


if __name__ == "__main__":
    unittest.main()
