"""Unit tests for the idle GUI-application sweep in utils.gui_app_closer.

Photoshop and friends can never match the headless latch the process reaper
uses, so they get their own track. That track is allowed to close a real app
holding a real document, which makes its gates the whole point: these tests
drive every one of them from the refusing side as well as the permitting side.

Nothing here launches or signals an application. The process table, the
keyboard idle, the unsaved-work probe and the quit are all injected.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import gui_app_closer as gac  # noqa: E402

TTL_MIN = 60
TTL = TTL_MIN * 60.0

# Real command lines, copied from `ps -axo command=` on the machine this was
# written for. The Photoshop one is the app; the rest must never match it.
PHOTOSHOP = ("/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app/"
             "Contents/MacOS/Adobe Photoshop 2026")
RESOLVE = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/Resolve"
CC_HELPER = ("/Applications/Utilities/Adobe Creative Cloud/ACC/Creative Cloud.app/"
             "Contents/MacOS/../Frameworks/Creative Cloud UI Helper.app/"
             "Contents/MacOS/Creative Cloud UI Helper")
FINDER_SYNC = ("/Applications/Utilities/Adobe Sync/CoreSync/Core Sync.app/Contents/"
               "PlugIns/ACCFinderSync.appex/Contents/MacOS/ACCFinderSync")
BOT = "/opt/homebrew/Cellar/python@3.12/3.12.14/bin/python3.12 bot.py"


def row(pid, cpu_time, command, rss=900000):
    """One `ps -axo pid=,etime=,%cpu=,rss=,time=,command=` line."""
    return f"{pid} 02:52:43 2.4 {rss} {cpu_time} {command}"


class ParseCpuTimeTests(unittest.TestCase):
    def test_minutes_seconds_hundredths(self):
        self.assertAlmostEqual(gac.parse_cpu_time("9:32.89"), 572.89, places=2)

    def test_hours(self):
        self.assertAlmostEqual(gac.parse_cpu_time("1:02:03"), 3723.0, places=2)

    def test_days(self):
        self.assertAlmostEqual(gac.parse_cpu_time("2-03:04:05"), 183845.0, places=2)

    def test_junk_is_none_not_zero(self):
        # Zero would read as "this process has done nothing", which is the
        # dangerous direction: None makes the caller skip the row instead.
        for junk in ("", "   ", "abc", "1:2:3:4", "x:y"):
            self.assertIsNone(gac.parse_cpu_time(junk), junk)


class BundleNameTests(unittest.TestCase):
    def test_app_whose_binary_matches_its_bundle(self):
        self.assertEqual(gac.bundle_name(PHOTOSHOP), "Adobe Photoshop 2026")

    def test_app_whose_binary_differs_from_its_bundle(self):
        # Resolve's executable is "Resolve" inside "DaVinci Resolve.app". Reading
        # the binary name instead of the bundle would miss it entirely.
        self.assertEqual(gac.bundle_name(RESOLVE), "DaVinci Resolve")

    def test_nested_helper_reports_itself_not_its_parent(self):
        # A helper bundled inside an app must report its own name, so a needle
        # aimed at the parent never sweeps up the child.
        self.assertEqual(gac.bundle_name(CC_HELPER), "Creative Cloud UI Helper")

    def test_appex_plugin_matches_nothing(self):
        # ".appex/Contents/MacOS/" is not ".app/Contents/MacOS/", so a Finder
        # sync plugin is not an app as far as this is concerned -- which is the
        # outcome that matters: it can never be selected.
        self.assertIsNone(gac.bundle_name(FINDER_SYNC))

    def test_non_app_process(self):
        self.assertIsNone(gac.bundle_name(BOT))
        self.assertIsNone(gac.bundle_name(""))


class SampleAppsTests(unittest.TestCase):
    def test_selects_only_managed_apps(self):
        lines = [row(1, "9:32.89", PHOTOSHOP), row(2, "0:10.00", CC_HELPER),
                 row(3, "1:00.00", BOT), row(4, "5:00.00", RESOLVE)]
        got = gac.sample_apps(lines, gac.DEFAULT_GUI_APPS, set())
        self.assertEqual([r["pid"] for r in got], [1, 4])
        self.assertEqual(got[0]["needle"], "Adobe Photoshop")
        self.assertAlmostEqual(got[0]["cpu_seconds"], 572.89, places=2)

    def test_skips_own_pids(self):
        lines = [row(11, "9:32.89", PHOTOSHOP)]
        self.assertEqual(gac.sample_apps(lines, gac.DEFAULT_GUI_APPS, {11}), [])

    def test_unparseable_cpu_time_is_skipped(self):
        lines = [row(1, "nonsense", PHOTOSHOP)]
        self.assertEqual(gac.sample_apps(lines, gac.DEFAULT_GUI_APPS, set()), [])


class IdleTrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = gac.IdleTracker(busy_percent=15.0)

    def _observe(self, cpu, at):
        return self.t.observe(
            gac.sample_apps([row(1, cpu, PHOTOSHOP)], gac.DEFAULT_GUI_APPS, set()),
            now=at)[0]

    def test_first_sighting_starts_at_zero(self):
        # An app must be watched for a whole window before it can be closed.
        self.assertEqual(self._observe("9:00.00", 0.0)["idle_seconds"], 0.0)

    def test_idle_app_accumulates(self):
        # The measured idle rate of a real Photoshop with a document open:
        # 1.54s of CPU per 30s of wall clock, i.e. 5.1% of one core.
        self._observe("9:00.00", 0.0)
        r = self._observe("9:01.54", 30.0)
        self.assertEqual(r["idle_seconds"], 30.0)
        r = self._observe("9:03.08", 60.0)
        self.assertEqual(r["idle_seconds"], 60.0)

    def test_real_work_resets_the_clock(self):
        self._observe("9:00.00", 0.0)
        self._observe("9:01.54", 30.0)      # idle
        r = self._observe("9:21.54", 60.0)  # 20s of CPU in 30s = 67%: working
        self.assertEqual(r["idle_seconds"], 0.0)

    def test_a_short_burst_still_counts_as_work(self):
        # 5 seconds of real work inside one 30s sample is ~17% plus the 5%
        # idle floor, which must clear the 15% line or a mid-job app could be
        # closed between steps.
        self._observe("9:00.00", 0.0)
        r = self._observe("9:06.54", 30.0)
        self.assertEqual(r["idle_seconds"], 0.0)

    def test_pid_reuse_resets_rather_than_inheriting(self):
        self._observe("9:00.00", 0.0)
        self._observe("9:01.54", 3600.0)
        r = self._observe("0:01.00", 3630.0)  # CPU went backwards: new process
        self.assertEqual(r["idle_seconds"], 0.0)

    def test_samples_taken_too_close_together_are_not_a_measurement(self):
        # A third of a second of CPU across a one-second gap is 33% and would
        # wipe an hour-old idle clock. Below the floor the sample is held, and
        # the next one measures across the whole span.
        self._observe("9:00.00", 0.0)
        self._observe("9:01.54", 600.0)
        self.assertEqual(self._observe("9:01.87", 601.0)["idle_seconds"], 601.0)
        # ...and the held sample is still counted later: 1.87s of CPU across
        # the 30s from 600 to 630 is 6%, so it stays idle.
        self.assertEqual(self._observe("9:03.08", 630.0)["idle_seconds"], 630.0)

    def test_peek_reports_without_disturbing_the_clock(self):
        self._observe("9:00.00", 0.0)
        self._observe("9:01.54", 600.0)
        rows = gac.sample_apps([row(1, "9:20.00", PHOTOSHOP)],
                               gac.DEFAULT_GUI_APPS, set())
        peeked = self.t.peek(rows, now=601.0)
        self.assertEqual(peeked[0]["idle_seconds"], 601.0)
        # The 20s CPU jump in that row must not have been folded in.
        self.assertEqual(self.t._state[1]["cpu"], 541.54)
        self.assertEqual(self._observe("9:01.60", 630.0)["idle_seconds"], 630.0)

    def test_peek_on_an_untracked_app_reads_zero(self):
        rows = gac.sample_apps([row(9, "1:00.00", PHOTOSHOP)],
                               gac.DEFAULT_GUI_APPS, set())
        self.assertEqual(self.t.peek(rows, now=5.0)[0]["idle_seconds"], 0.0)

    def test_exited_app_is_forgotten(self):
        self._observe("9:00.00", 0.0)
        self.t.observe([], now=30.0)
        self.assertEqual(self.t._state, {})


class ProbeParsingTests(unittest.TestCase):
    """The probes decide whether work can be lost, so their parsing is pinned.

    _tell is replaced, so no Apple Event is ever sent.
    """

    def setUp(self):
        self._real_tell = gac._tell
        self.addCleanup(lambda: setattr(gac, "_tell", self._real_tell))

    def _answer(self, ok, out):
        gac._tell = lambda app, body, timeout=20.0: (ok, out)

    def test_photoshop_clean_and_dirty(self):
        self._answer(True, "unsaved=0")
        self.assertEqual(gac._probe_photoshop("Adobe Photoshop 2026")[0], "clean")
        self._answer(True, "unsaved=2")
        self.assertEqual(gac._probe_photoshop("Adobe Photoshop 2026")[0], "dirty")

    def test_photoshop_no_answer_is_unknown(self):
        self._answer(False, "Application isn't running")
        self.assertEqual(gac._probe_photoshop("Adobe Photoshop 2026")[0], "unknown")

    def test_photoshop_unexpected_shape_is_unknown(self):
        self._answer(True, "something changed in the DOM")
        self.assertEqual(gac._probe_photoshop("Adobe Photoshop 2026")[0], "unknown")

    def test_illustrator_list_forms(self):
        for out, expected in (("{}", "clean"), ("", "clean"),
                              ("false", "clean"), ("false, false", "clean"),
                              ("true", "dirty"), ("false, true", "dirty"),
                              ("{false, true}", "dirty"),
                              ("missing value", "unknown")):
            self._answer(True, out)
            self.assertEqual(gac._probe_illustrator("Adobe Illustrator")[0],
                             expected, out)

    def test_after_effects_only_a_bare_false_is_clean(self):
        for out, expected in (("false", "clean"), ("true", "dirty"),
                              ("undefined", "unknown"), ("", "unknown"),
                              ("Error", "unknown")):
            self._answer(True, out)
            self.assertEqual(gac._probe_after_effects("Adobe After Effects 2026")[0],
                             expected, out)

    def test_probe_exception_is_unknown_not_clean(self):
        def boom(*a, **k):
            raise RuntimeError("apple event failed")
        gac._PROBES["Adobe Photoshop"] = boom
        self.addCleanup(lambda: gac._PROBES.__setitem__(
            "Adobe Photoshop", gac._probe_photoshop))
        self.assertEqual(gac.probe_unsaved("Adobe Photoshop", "x")[0], "unknown")

    def test_app_with_no_probe_is_never_closeable(self):
        self.assertEqual(gac.probe_unsaved("Some New App", "x")[0], "unknown")


class AppleScriptEscapingTests(unittest.TestCase):
    """The probes carry JavaScript inside AppleScript, and the JavaScript has
    quotes of its own. Getting that wrong does not crash: osascript answers
    with a syntax error, the probe reads "unknown", and the feature silently
    never closes anything. This runs the real osascript, so it catches it."""

    def test_literals_round_trip_through_osascript(self):
        if sys.platform != "darwin":
            self.skipTest("macOS osascript")
        import subprocess
        for text in (gac._PS_JS,
                     "(app.project ? app.project.dirty : true).toString()",
                     'has "double quotes" in it',
                     "has a backslash \\ in it",
                     "plain"):
            lit = gac._as_applescript_string(text)
            r = subprocess.run(["osascript", "-e", f"return {lit}"],
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0,
                             f"{lit} did not compile: {r.stderr}")
            self.assertEqual(r.stdout.rstrip("\n"), text)


class SweepTests(unittest.TestCase):
    """Every gate, from both sides."""

    def setUp(self):
        self.config = {"close_idle_gui_apps": True,
                       "close_idle_gui_app_minutes": TTL_MIN}
        self.quit_calls = []

        async def quitter(bundle, pid, grace=0.0):
            self.quit_calls.append((bundle, pid))
            return True
        self.quitter = quitter
        self.tracker = gac.IdleTracker()
        # Two samples an hour apart with a believable idle CPU rate: 5.1% of a
        # core over 3600s is 184 seconds of CPU.
        self.tracker.observe(
            gac.sample_apps([row(1, "9:00.00", PHOTOSHOP)], gac.DEFAULT_GUI_APPS, set()),
            now=0.0)
        self.lines = [row(1, "12:04.00", PHOTOSHOP)]

    def _sweep(self, **kw):
        kw.setdefault("config", self.config)
        kw.setdefault("ps_lines", self.lines)
        kw.setdefault("tracker", self.tracker)
        kw.setdefault("human_idle_probe", lambda: TTL + 60)
        kw.setdefault("prober", lambda needle, bundle: ("clean", ""))
        kw.setdefault("quitter", self.quitter)
        kw.setdefault("now", TTL + 1)
        if sys.platform != "darwin":
            self.skipTest("macOS-only sweep")
        return asyncio.run(gac.sweep(**kw))

    def test_closes_when_every_gate_passes(self):
        closed = self._sweep()
        self.assertEqual([c["bundle"] for c in closed], ["Adobe Photoshop 2026"])
        self.assertEqual(self.quit_calls, [("Adobe Photoshop 2026", 1)])

    def test_not_closed_while_someone_is_at_the_machine(self):
        self.assertEqual(self._sweep(human_idle_probe=lambda: 120.0), [])
        self.assertEqual(self.quit_calls, [])

    def test_not_closed_when_keyboard_idle_is_unreadable(self):
        # None must read as "someone might be here", never as "nobody is".
        self.assertEqual(self._sweep(human_idle_probe=lambda: None), [])
        self.assertEqual(self.quit_calls, [])

    def test_not_closed_with_unsaved_work(self):
        self.assertEqual(
            self._sweep(prober=lambda n, b: ("dirty", "1 unsaved document")), [])
        self.assertEqual(self.quit_calls, [])

    def test_not_closed_when_the_answer_is_unknown(self):
        self.assertEqual(self._sweep(prober=lambda n, b: ("unknown", "no answer")), [])
        self.assertEqual(self.quit_calls, [])

    def test_not_closed_before_the_window_is_up(self):
        self.assertEqual(self._sweep(now=TTL - 1), [])
        self.assertEqual(self.quit_calls, [])

    def test_not_closed_when_the_app_has_been_working(self):
        # Same hour, but the CPU says it did 40 minutes of work in it.
        self.assertEqual(self._sweep(ps_lines=[row(1, "49:00.00", PHOTOSHOP)]), [])
        self.assertEqual(self.quit_calls, [])

    def test_toggle_off_does_nothing(self):
        cfg = dict(self.config, close_idle_gui_apps=False)
        self.assertEqual(self._sweep(config=cfg), [])
        self.assertEqual(self.quit_calls, [])

    def test_force_overrides_the_toggle_but_not_the_gates(self):
        cfg = dict(self.config, close_idle_gui_apps=False)
        self.assertEqual(len(self._sweep(config=cfg, force=True)), 1)
        self.quit_calls.clear()
        self.assertEqual(self._sweep(config=cfg, force=True, human_idle_probe=lambda: 10.0), [])

    def test_an_app_that_refuses_to_quit_is_not_reported_as_closed(self):
        async def stubborn(bundle, pid, grace=0.0):
            self.quit_calls.append((bundle, pid))
            return False
        self.assertEqual(self._sweep(quitter=stubborn), [])
        self.assertEqual(len(self.quit_calls), 1)

    def test_an_app_that_cannot_be_closed_is_not_retried_next_tick(self):
        # A modal that swallows the quit, or a document left unsaved, must cost
        # one attempt per window and not one every 30 seconds: the retry storm
        # would block the janitor loop and hammer the app with Apple Events.
        probes = []

        def prober(needle, bundle):
            probes.append(bundle)
            return ("dirty", "1 unsaved document")

        self.assertEqual(self._sweep(prober=prober), [])
        self.assertEqual(len(probes), 1)
        # One tick later (30s), with the app still idle, it must be skipped.
        self.assertEqual(
            self._sweep(prober=prober, now=TTL + 31,
                        ps_lines=[row(1, "12:05.54", PHOTOSHOP)]), [])
        self.assertEqual(len(probes), 1, "the app was probed again too soon")
        # A full window later it is tried once more.
        self.assertEqual(
            self._sweep(prober=prober, now=2 * TTL + 2,
                        ps_lines=[row(1, "15:04.00", PHOTOSHOP)]), [])
        self.assertEqual(len(probes), 2)

    def test_a_refused_quit_also_backs_off(self):
        async def stubborn(bundle, pid, grace=0.0):
            self.quit_calls.append((bundle, pid))
            return False
        self.assertEqual(self._sweep(quitter=stubborn), [])
        self.assertEqual(
            self._sweep(quitter=stubborn, now=TTL + 31,
                        ps_lines=[row(1, "12:05.54", PHOTOSHOP)]), [])
        self.assertEqual(len(self.quit_calls), 1)

    def test_a_bad_minutes_value_falls_back_to_the_default(self):
        cfg = dict(self.config, close_idle_gui_app_minutes="not a number")
        self.assertEqual(self._sweep(config=cfg, now=TTL - 1), [])


class LiveFormatTests(unittest.TestCase):
    """The parser's assumption about `ps` column order, checked against the
    real thing rather than against the rows above."""

    def test_ps_rows_parse_on_this_machine(self):
        if sys.platform != "darwin":
            self.skipTest("macOS ps")
        lines = asyncio.run(gac.list_gui_process_lines())
        self.assertTrue(lines, "ps returned nothing")
        parsed = 0
        for line in lines:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            if gac.parse_cpu_time(parts[4]) is not None and parts[0].isdigit():
                parsed += 1
        self.assertGreater(parsed, len(lines) * 0.9,
                           "the CPU-time column is not where the parser expects it")


if __name__ == "__main__":
    unittest.main()
