#!/usr/bin/env python3
"""Unit tests for the core.health swap-alert flood fix.

Run: python3 -m unittest tests.test_health_pressure_alert  (from repo root)

Regression coverage for the "ton of health alerts" report. Two independent
faults, either of which alone would have been survivable:

(1) THE TRIGGER WAS A NUMB INSTRUMENT. check_critical() alerted on swap
    used/total > 80%. macOS grows swap on demand, one 1 GB swapfile at a time,
    and only adds the next file when the existing ones are nearly full, so
    vm.swapusage total is a since-boot high-water mark of what has already been
    allocated rather than a capacity. The ratio therefore pins itself near 90%
    on any Mac that swaps at all and CANNOT come out low. On the machine that
    reported this it read 89.5% while memory pressure was NORMAL and 16.9 of
    24 GB of RAM were free. A statistic that cannot say otherwise is not a
    check. Replaced with memory pressure, which is the kernel's own verdict on
    macOS and real stall time on Linux.

(2) THE 4-HOUR COOLDOWN NEVER ENGAGED. _alert_key() built the cooldown key by
    splitting the message on the em dash. Only the disk alerts are worded with
    every number after the dash; the swap and RAM messages carry the reading
    BEFORE it, and the CPU message has no em dash at all. So "Swap at 89.5%"
    and "Swap at 91.0%" were two different keys, neither silenced the other,
    and every single check spoke. The key now strips numbers, so it cannot
    depend on a measured value by construction.

Both are decision-logic tests: every system reader is stubbed, no subprocesses,
no real sysctl and no real /proc.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.health as health  # noqa: E402


_OK_DISK = {"total_gb": 460.0, "used_gb": 200.0, "free_gb": 260.0, "percent": 43.0}
_OK_MEM = {"total_gb": 24.0, "used_gb": 7.1, "free_gb": 16.9, "percent": 29.5}

# The exact reading from the machine that reported the flood: swap pinned at
# 89.5% while the machine was entirely healthy.
_PINNED_SWAP = {"total_gb": 8.0, "used_gb": 7.16, "free_gb": 0.84, "percent": 89.5}

_SETTLED = health._SETTLING_WINDOW_SECONDS + 60


class SwapNoLongerTriggersAlertsTests(unittest.TestCase):
    """Fault 1: the trigger must be a statistic that can say otherwise."""

    def setUp(self):
        health._consecutive_cpu_breaches = 0
        health._consecutive_net_failures = 0
        self._patchers = {
            "disk": patch.object(health, "get_disk_usage", return_value=_OK_DISK),
            "mem": patch.object(health, "get_memory_usage", return_value=_OK_MEM),
            "swap": patch.object(health, "get_swap_usage", return_value=_PINNED_SWAP),
            "cpu": patch.object(health, "get_cpu_usage", return_value=5.0),
            "net": patch.object(health, "get_network_status", return_value=True),
            "uptime": patch.object(
                health, "get_system_uptime_seconds", return_value=_SETTLED
            ),
            "load": patch.object(
                health, "get_load_average", return_value="1.00, 1.00, 1.00"
            ),
            "pressure": patch.object(
                health, "get_memory_pressure", return_value=health.PRESSURE_NORMAL
            ),
        }
        self.mocks = {name: p.start() for name, p in self._patchers.items()}
        self.addCleanup(lambda: [p.stop() for p in self._patchers.values()])

    def test_pinned_swap_with_normal_pressure_is_silent(self):
        # THE regression: this exact state produced an alert every single check.
        self.assertEqual(health.check_critical(), [])

    def test_swap_at_100_percent_is_still_silent(self):
        # Not just below a raised threshold — swap percent must not be consulted
        # at all, so even a full allocation stays quiet while pressure is normal.
        self.mocks["swap"].return_value = {
            "total_gb": 8.0, "used_gb": 8.0, "free_gb": 0.0, "percent": 100.0
        }
        self.assertEqual(health.check_critical(), [])

    def test_warn_pressure_alerts(self):
        self.mocks["pressure"].return_value = health.PRESSURE_WARN
        alerts = health.check_critical()
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].startswith("WARNING: Memory pressure"))

    def test_critical_pressure_escalates(self):
        self.mocks["pressure"].return_value = health.PRESSURE_CRITICAL
        alerts = health.check_critical()
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].startswith("CRITICAL: Memory pressure"))

    def test_unknown_pressure_never_alerts(self):
        # A check that cannot see must stay silent rather than invent a verdict.
        self.mocks["pressure"].return_value = health.PRESSURE_UNKNOWN
        self.assertEqual(health.check_critical(), [])

    def test_pressure_alert_carries_swap_as_context_not_as_ratio(self):
        self.mocks["pressure"].return_value = health.PRESSURE_WARN
        msg = health.check_critical()[0]
        self.assertIn("7.2 GB swapped out", msg)   # absolute: means something
        self.assertNotIn("89.5", msg)              # the ratio: means nothing

    def test_pressure_is_suppressed_during_the_settling_window(self):
        self.mocks["uptime"].return_value = 30.0
        self.mocks["pressure"].return_value = health.PRESSURE_CRITICAL
        self.assertEqual(health.check_critical(), [])

    def test_disk_alert_is_untouched(self):
        # The one alert that was working correctly must keep working.
        self.mocks["disk"].return_value = {
            "total_gb": 460.0, "used_gb": 457.0, "free_gb": 3.0, "percent": 99.3
        }
        alerts = health.check_critical()
        self.assertTrue(any("Low disk space" in a for a in alerts))


class GetMemoryPressureTests(unittest.TestCase):
    """The burst debounce: report only the level EVERY sample held."""

    def _burst(self, levels):
        with patch.object(health, "_read_pressure_once", side_effect=levels), \
             patch.object(health.time, "sleep", return_value=None):
            return health.get_memory_pressure()

    def test_all_normal_reads_normal(self):
        self.assertEqual(
            self._burst([health.PRESSURE_NORMAL]), health.PRESSURE_NORMAL
        )

    def test_sustained_warn_reads_warn(self):
        self.assertEqual(
            self._burst([health.PRESSURE_WARN] * health._PRESSURE_SAMPLES),
            health.PRESSURE_WARN,
        )

    def test_sustained_critical_reads_critical(self):
        self.assertEqual(
            self._burst([health.PRESSURE_CRITICAL] * health._PRESSURE_SAMPLES),
            health.PRESSURE_CRITICAL,
        )

    def test_a_single_normal_sample_defeats_a_spike(self):
        # Four critical samples and one normal one is a blip, not a condition.
        levels = [health.PRESSURE_CRITICAL] * 4 + [health.PRESSURE_NORMAL]
        self.assertEqual(self._burst(levels), health.PRESSURE_NORMAL)

    def test_burst_reports_the_lowest_level_held_throughout(self):
        # Critical for part of the burst, warn for the rest => warn is what was
        # actually sustained, and warn is what must be reported.
        levels = [health.PRESSURE_CRITICAL, health.PRESSURE_CRITICAL,
                  health.PRESSURE_WARN, health.PRESSURE_WARN, health.PRESSURE_WARN]
        self.assertEqual(self._burst(levels), health.PRESSURE_WARN)

    def test_any_unknown_sample_makes_the_whole_burst_unknown(self):
        levels = [health.PRESSURE_CRITICAL, health.PRESSURE_UNKNOWN,
                  health.PRESSURE_CRITICAL]
        self.assertEqual(self._burst(levels), health.PRESSURE_UNKNOWN)

    def test_normal_short_circuits_without_burning_the_full_burst(self):
        # The common case must not cost a full burst of sleeps.
        reader = MagicMock(return_value=health.PRESSURE_NORMAL)
        with patch.object(health, "_read_pressure_once", reader), \
             patch.object(health.time, "sleep", return_value=None):
            health.get_memory_pressure()
        self.assertEqual(reader.call_count, 1)


class ReadPressureOnceTests(unittest.TestCase):
    """Platform readers. macOS levels are the kernel's own 1/2/4 constants."""

    def _darwin(self, stdout):
        completed = MagicMock()
        completed.stdout = stdout
        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.subprocess, "run", return_value=completed):
            return health._read_pressure_once()

    def test_darwin_level_1_is_normal(self):
        self.assertEqual(self._darwin("1\n"), health.PRESSURE_NORMAL)

    def test_darwin_level_2_is_warn(self):
        self.assertEqual(self._darwin("2\n"), health.PRESSURE_WARN)

    def test_darwin_level_4_is_critical(self):
        self.assertEqual(self._darwin("4\n"), health.PRESSURE_CRITICAL)

    def test_darwin_unrecognised_level_is_unknown_not_normal(self):
        # An unmapped level must not be guessed at in the reassuring direction.
        self.assertEqual(self._darwin("3\n"), health.PRESSURE_UNKNOWN)

    def test_darwin_empty_output_is_unknown(self):
        self.assertEqual(self._darwin(""), health.PRESSURE_UNKNOWN)

    def test_darwin_subprocess_failure_is_unknown(self):
        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.subprocess, "run", side_effect=OSError("boom")):
            self.assertEqual(health._read_pressure_once(), health.PRESSURE_UNKNOWN)

    def _linux(self, psi_text):
        import io
        with patch.object(health.platform, "system", return_value="Linux"), \
             patch("builtins.open", return_value=io.StringIO(psi_text)):
            return health._read_pressure_once()

    def test_linux_idle_psi_is_normal(self):
        psi = ("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
               "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        self.assertEqual(self._linux(psi), health.PRESSURE_NORMAL)

    def test_linux_reads_full_not_some(self):
        # "some" being high is one task waiting; only "full" means everything
        # stalled. A high "some" with an idle "full" must read normal.
        psi = ("some avg10=90.00 avg60=90.00 avg300=90.00 total=1\n"
               "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        self.assertEqual(self._linux(psi), health.PRESSURE_NORMAL)

    def test_linux_full_stall_above_warn_threshold(self):
        psi = ("some avg10=50.00 avg60=50.00 avg300=50.00 total=1\n"
               f"full avg10=9.00 avg60={health._PSI_FULL_WARN + 1:.2f} "
               "avg300=4.00 total=1\n")
        self.assertEqual(self._linux(psi), health.PRESSURE_WARN)

    def test_linux_full_stall_above_critical_threshold(self):
        psi = ("some avg10=80.00 avg60=80.00 avg300=80.00 total=1\n"
               f"full avg10=40.00 avg60={health._PSI_FULL_CRITICAL + 1:.2f} "
               "avg300=20.00 total=1\n")
        self.assertEqual(self._linux(psi), health.PRESSURE_CRITICAL)

    # The two Linux cutoffs are OURS, not the kernel's, so they are the one
    # part of this check that a future edit can move. The tests above are
    # written as `_PSI_FULL_WARN + 1`, which moves with the constant: they pin
    # that a threshold exists and leave its entire interior free. Editing warn
    # from 5.0 to 0.5 — a Linux flood, which is the exact failure this file
    # exists to prevent — passes every one of them. So both cutoffs are also
    # bracketed from both sides by LITERAL readings, and the boundary itself is
    # pinned, which is what makes `>=` meaningful rather than incidental.
    def _linux_full(self, avg60):
        return self._linux(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            f"full avg10=0.00 avg60={avg60:.2f} avg300=0.00 total=0\n"
        )

    def test_linux_just_below_warn_is_normal(self):
        self.assertEqual(self._linux_full(4.0), health.PRESSURE_NORMAL)

    def test_linux_exactly_at_warn_is_warn(self):
        self.assertEqual(self._linux_full(5.0), health.PRESSURE_WARN)

    def test_linux_just_below_critical_is_still_only_warn(self):
        self.assertEqual(self._linux_full(19.0), health.PRESSURE_WARN)

    def test_linux_exactly_at_critical_is_critical(self):
        self.assertEqual(self._linux_full(20.0), health.PRESSURE_CRITICAL)

    def test_linux_well_above_critical_is_critical(self):
        self.assertEqual(self._linux_full(60.0), health.PRESSURE_CRITICAL)

    def test_linux_missing_psi_file_is_unknown(self):
        # PSI needs kernel 4.20+ and psi=1 on some distros.
        with patch.object(health.platform, "system", return_value="Linux"), \
             patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(health._read_pressure_once(), health.PRESSURE_UNKNOWN)


class AlertKeyStabilityTests(unittest.TestCase):
    """Fault 2: the cooldown key must depend on the KIND of alert only."""

    # The historical swap wording is quoted verbatim on purpose. The message no
    # longer exists — fault 1 removed it — but it is the shape that flooded, and
    # keeping it here pins fault 2 independently of fault 1's fix. Any future
    # message that puts a reading before the em dash has the same defect.
    _FLOOD_A = "WARNING: Swap at 89.5% — 7.2/8.0 GB"
    _FLOOD_B = "WARNING: Swap at 91.0% — 7.3/8.0 GB"

    def test_the_historical_flood_messages_collapse_to_one_key(self):
        self.assertEqual(
            health._alert_key(self._FLOOD_A), health._alert_key(self._FLOOD_B)
        )

    def test_ram_readings_collapse_to_one_key(self):
        a = "WARNING: RAM at 91.2% — 1.2 GB free"
        b = "WARNING: RAM at 93.8% — 0.8 GB free"
        self.assertEqual(health._alert_key(a), health._alert_key(b))

    def test_cpu_readings_collapse_despite_having_no_em_dash(self):
        a = "WARNING: CPU busy at 99.0% across 3 consecutive checks (load avg: 5.0, 4.0, 3.0)"
        b = "WARNING: CPU busy at 97.5% across 8 consecutive checks (load avg: 9.1, 8.0, 7.2)"
        self.assertEqual(health._alert_key(a), health._alert_key(b))

    def test_no_digit_survives_into_any_key(self):
        # The property that makes this stable BY CONSTRUCTION rather than by
        # the current wording of each message.
        messages = [
            "WARNING: Memory pressure warn — 1.2 GB RAM free, 7.2 GB swapped out",
            "CRITICAL: Memory pressure critical — 0.1 GB RAM free, 8.0 GB swapped out",
            "WARNING: RAM at 91.2% — 1.2 GB free",
            "CRITICAL: RAM at 96.0% — 0.4 GB free",
            "WARNING: CPU busy at 99.0% across 3 consecutive checks (load avg: 5.0)",
            "CRITICAL: Disk almost full — 1.4 GB free",
            "WARNING: Low disk space — 4.2 GB free",
            "WARNING: No internet connectivity",
        ]
        for msg in messages:
            with self.subTest(msg=msg):
                self.assertFalse(any(c.isdigit() for c in health._alert_key(msg)))

    def test_distinct_subsystems_keep_distinct_keys(self):
        keys = {
            health._alert_key("WARNING: RAM at 91.2% — 1.2 GB free"),
            health._alert_key("WARNING: CPU busy at 99.0% across 3 consecutive checks"),
            health._alert_key("WARNING: Low disk space — 4.2 GB free"),
            health._alert_key("WARNING: Memory pressure warn — 1.2 GB RAM free, 7.2 GB swapped out"),
            health._alert_key("WARNING: No internet connectivity"),
        }
        self.assertEqual(len(keys), 5)

    def test_escalation_is_not_muted_by_the_warning_before_it(self):
        # Severity stays in the key on purpose: a CRITICAL must be able to speak
        # inside the cooldown window opened by its own WARNING.
        warn = health._alert_key("WARNING: RAM at 91.2% — 1.2 GB free")
        crit = health._alert_key("CRITICAL: RAM at 96.0% — 0.4 GB free")
        self.assertNotEqual(warn, crit)

    def test_disk_key_is_unchanged_by_the_fix(self):
        # Disk was the one alert whose cooldown already worked; keep it working.
        self.assertEqual(
            health._alert_key("WARNING: Low disk space — 62.0 GB free"),
            health._alert_key("WARNING: Low disk space — 4.2 GB free"),
        )


class CooldownEngagesTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: the same condition must speak once, not on every check."""

    async def asyncSetUp(self):
        health._alert_cooldowns.clear()
        self.sent: list[tuple[int, str]] = []

        async def send_fn(uid, text):
            self.sent.append((uid, text))
            return True

        self.send_fn = send_fn

    async def _run_with_alerts(self, alerts):
        with patch.object(health, "check_critical", return_value=alerts):
            await health.run_health_check(self.send_fn, [1])

    async def test_the_historical_swap_flood_now_speaks_once(self):
        # The incident itself, replayed through run_health_check with the exact
        # wording that produced it: one condition, four checks, a slightly
        # different reading each time. Every one of these was sent. Only the
        # first may send now.
        for pct, used in [(89.5, 7.2), (91.0, 7.3), (93.4, 7.5), (90.2, 7.2)]:
            await self._run_with_alerts([f"WARNING: Swap at {pct}% — {used}/8.0 GB"])
        self.assertEqual(len(self.sent), 1)

    async def test_drifting_cpu_readings_speak_once(self):
        # The CPU message has no em dash at all, so its whole text was the key,
        # including the busy percent, the breach count and the load average.
        for busy, streak in [(99.0, 3), (97.5, 4), (98.2, 5)]:
            await self._run_with_alerts([
                f"WARNING: CPU busy at {busy}% across {streak} "
                f"consecutive checks (load avg: {busy / 10:.2f}, 8.00, 7.00)"
            ])
        self.assertEqual(len(self.sent), 1)

    async def test_drifting_pressure_readings_speak_once(self):
        # The replacement message, same property.
        for free, swapped in [(1.2, 7.2), (1.1, 7.4), (0.9, 7.6)]:
            await self._run_with_alerts([
                f"WARNING: Memory pressure warn — {free} GB RAM free, "
                f"{swapped} GB swapped out"
            ])
        self.assertEqual(len(self.sent), 1)

    async def test_escalation_still_gets_through_the_cooldown(self):
        await self._run_with_alerts(["WARNING: RAM at 91.2% — 1.2 GB free"])
        await self._run_with_alerts(["CRITICAL: RAM at 96.0% — 0.4 GB free"])
        self.assertEqual(len(self.sent), 2)

    async def test_a_different_subsystem_still_gets_through(self):
        await self._run_with_alerts(["WARNING: RAM at 91.2% — 1.2 GB free"])
        await self._run_with_alerts(["WARNING: Low disk space — 4.2 GB free"])
        self.assertEqual(len(self.sent), 2)

    async def test_cooldown_expires_after_the_window(self):
        await self._run_with_alerts(["WARNING: RAM at 91.2% — 1.2 GB free"])
        for key in health._alert_cooldowns:
            health._alert_cooldowns[key] -= health._ALERT_COOLDOWN_SECONDS + 1
        await self._run_with_alerts(["WARNING: RAM at 92.0% — 1.1 GB free"])
        self.assertEqual(len(self.sent), 2)


class HealthReportTests(unittest.TestCase):
    """/health output. Nothing pinned this before, and the swap line changed."""

    def setUp(self):
        self._patchers = [
            patch.object(health, "get_disk_usage", return_value=_OK_DISK),
            patch.object(health, "get_memory_usage", return_value=_OK_MEM),
            patch.object(health, "get_swap_usage", return_value=_PINNED_SWAP),
            patch.object(health, "get_load_average", return_value="1.00, 1.00, 1.00"),
            patch.object(health, "get_network_status", return_value=True),
        ]
        for pt in self._patchers:
            pt.start()
        self.addCleanup(lambda: [pt.stop() for pt in self._patchers])

    def _report(self, pressure):
        with patch.object(health, "get_memory_pressure", return_value=pressure):
            return health.build_health_report()

    def test_normal_pressure_is_reported(self):
        self.assertIn("Memory pressure: normal", self._report(health.PRESSURE_NORMAL))

    def test_unknown_pressure_is_reported_as_unknown_not_hidden(self):
        # Unknown never alerts, so on a machine that cannot read it, memory
        # alerting is switched OFF. This line is the only place an operator
        # could notice that, so it must not vanish. (Silence is right for the
        # ALERT; a status report that omits what it could not measure is the
        # opposite of the same principle.)
        report = self._report(health.PRESSURE_UNKNOWN)
        self.assertIn("Memory pressure: unknown", report)
        self.assertIn("alerting is off", report)

    def test_swap_is_absolute_gb_with_no_ratio(self):
        # The ratio is what could only ever read ~90%; it must not come back
        # into the report either, where it would read as a live 89.5% warning.
        report = self._report(health.PRESSURE_NORMAL)
        self.assertIn("Swap in use: 7.2 GB", report)
        self.assertNotIn("89.5", report)

    def test_a_machine_with_no_swap_shows_no_swap_line(self):
        with patch.object(health, "get_swap_usage",
                          return_value={"total_gb": 0, "used_gb": 0,
                                        "free_gb": 0, "percent": 0}):
            self.assertNotIn("Swap in use", self._report(health.PRESSURE_NORMAL))


def _read_bot_health_check_interval() -> int:
    """bot.py::_HEALTH_CHECK_INTERVAL, read from the AST of the real file.

    Read rather than assumed, and read structurally rather than by grepping the
    source text, so a rename surfaces as a failure here instead of silently
    skipping the comparison this class exists to make.
    """
    import ast

    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "_HEALTH_CHECK_INTERVAL" not in names:
            continue
        value = node.value
        if isinstance(value, ast.Constant):
            return int(value.value)
        if (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult)
                and isinstance(value.left, ast.Constant)
                and isinstance(value.right, ast.Constant)):
            return int(value.left.value) * int(value.right.value)
        raise AssertionError(f"unhandled shape for _HEALTH_CHECK_INTERVAL: "
                             f"{ast.dump(value)}")
    raise AssertionError("bot.py no longer defines _HEALTH_CHECK_INTERVAL at "
                         "module level; this comparison needs re-pointing")


class CooldownVersusCheckCadenceTests(unittest.IsolatedAsyncioTestCase):
    """A stable key is necessary for the cooldown. It is not sufficient.

    Fixing _alert_key() made the 4-hour guard CORRECT — it had been keying on
    the reading, so it could never match itself. It did not make the guard
    ACTIVE: bot.py runs check_critical() once every 4 hours and the window is
    also 4 hours, so the window has already expired by the time the next check
    could possibly repeat an alert. A persistent condition therefore speaks on
    every check, before the fix and after it.

    That is worth pinning rather than leaving as prose, because "there is a
    4-hour cooldown" reads like a throttle and currently is not one. These tests
    replay bot.py::_health_monitor_loop's real cadence on a virtual clock.
    """

    _PERSISTENT = "WARNING: Memory pressure warn — 1.2 GB RAM free, 7.2 GB swapped out"

    # bot.py's own loop shape, mirrored here.
    _LOOP_SLEEP = 300
    _STARTUP_DELAY = 5 * 60
    _CHECK_COST = 3.0   # top samples ~1s, plus curl probes and the pressure burst
    _SEND_COST = 0.5

    async def _replay(self, *, days, cooldown, check_interval,
                      message_for_check=None, alert_key=None):
        """Return (check_times, send_times) over `days` of the real loop."""
        health._alert_cooldowns.clear()
        # NOT zero. run_health_check reads the last-sent time with
        # _alert_cooldowns.get(key, 0), so a virtual clock starting near zero
        # reads the missing entry as "sent a moment ago" and swallows the first
        # alert. Real wall-clock time is ~1.7e9, where that sentinel is
        # harmless. Starting the replay there keeps the model faithful.
        clock = [1_700_000_000.0]
        check_times: list[float] = []
        send_times: list[float] = []

        async def send_fn(uid, text):
            send_times.append(clock[0])
            return True

        def fake_check_critical(bot_dir=None):
            clock[0] += self._CHECK_COST   # the check costs time before it answers
            n = len(check_times)
            check_times.append(clock[0])
            msg = (message_for_check(n) if message_for_check
                   else self._PERSISTENT)
            return [msg]

        patches = [
            patch.object(health, "_ALERT_COOLDOWN_SECONDS", cooldown),
            patch.object(health.time, "time", lambda: clock[0]),
            patch.object(health, "check_critical", fake_check_critical),
        ]
        if alert_key is not None:
            patches.append(patch.object(health, "_alert_key", alert_key))

        for pt in patches:
            pt.start()
        try:
            horizon = clock[0] + days * 86400
            clock[0] += self._STARTUP_DELAY
            last_check = None
            while clock[0] < horizon:
                now = clock[0]
                if last_check is None or now - last_check >= check_interval:
                    last_check = now
                    before = len(send_times)
                    await health.run_health_check(send_fn, [1], None)
                    if len(send_times) > before:
                        clock[0] += self._SEND_COST
                clock[0] += self._LOOP_SLEEP
        finally:
            for pt in patches:
                pt.stop()
        return check_times, send_times

    @staticmethod
    def _expected_sends(check_times, cooldown):
        """Second, independent implementation of the cooldown decision."""
        count, last = 0, None
        for t in check_times:
            if last is None or t - last >= cooldown:
                count += 1
                last = t
        return count

    def test_the_shipped_window_does_not_span_a_second_check(self):
        interval = _read_bot_health_check_interval()
        self.assertLessEqual(
            health._ALERT_COOLDOWN_SECONDS, interval,
            "The alert cooldown now spans more than one health-check interval, "
            "which means it finally throttles something. That is the policy "
            "change described on _ALERT_COOLDOWN_SECONDS in core/health.py — a "
            "deliberate one, not a bug. Update this test and the one below "
            "together with it."
        )

    async def test_a_persistent_condition_speaks_on_every_check(self):
        interval = _read_bot_health_check_interval()
        checks, sends = await self._replay(
            days=3, cooldown=health._ALERT_COOLDOWN_SECONDS,
            check_interval=interval,
        )
        self.assertGreater(len(checks), 10)          # the replay really ran
        self.assertEqual(len(sends), len(checks))    # and every check spoke
        self.assertEqual(len(sends),
                         self._expected_sends(checks, health._ALERT_COOLDOWN_SECONDS))

    async def test_a_window_spanning_two_checks_does_throttle(self):
        # The mechanism is sound; it is the value that is inert. Proving this
        # is what stops the test above being read as "cooldowns do not work".
        interval = _read_bot_health_check_interval()
        checks, sends = await self._replay(
            days=3, cooldown=2 * interval, check_interval=interval,
        )
        self.assertEqual(len(sends), self._expected_sends(checks, 2 * interval))
        self.assertLess(len(sends), len(checks))
        self.assertGreater(len(sends), 0)

    async def test_the_stable_key_is_what_makes_a_longer_window_work(self):
        # Positive control for the fix itself, at a window long enough to bite.
        # Same drifting readings, same 24-hour window, only the key differs.
        interval = _read_bot_health_check_interval()

        def drifting(n):
            # The HISTORICAL swap wording, which is the shape that defeated the
            # old key: the reading sits BEFORE the em dash, so splitting on the
            # dash left it inside the key. Deliberately not the replacement
            # pressure wording — that one puts every number after the dash, so
            # the old key would have handled it and the control would prove
            # nothing. (Checked: it returns 2 sends either way.)
            return f"WARNING: Swap at {89.5 + n * 0.1:.1f}% — {7.2:.1f}/8.0 GB"

        def old_key(alert_msg):
            # The pre-fix implementation, verbatim.
            parts = alert_msg.split("—")
            return parts[0].strip() if parts else alert_msg

        _, fixed = await self._replay(
            days=3, cooldown=24 * 3600, check_interval=interval,
            message_for_check=drifting,
        )
        checks, broken = await self._replay(
            days=3, cooldown=24 * 3600, check_interval=interval,
            message_for_check=drifting, alert_key=old_key,
        )
        self.assertEqual(len(broken), len(checks))   # old key: never suppressed
        self.assertLess(len(fixed), len(broken))     # new key: actually throttles


if __name__ == "__main__":
    unittest.main()
