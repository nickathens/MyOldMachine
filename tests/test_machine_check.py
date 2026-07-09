"""Offline tests for utils/machine_check.py (report-only machine sanity check).

No network, no real probe, no Telegram. Every test drives synthetic system_caps.json
snapshots through a temporary data dir and asserts the diff / baseline / exit behavior.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import machine_check as mc


def _write_caps(data_dir: Path, tools=(), modules=(), skills=()):
    """Write a system_caps.json in the shape core/system_probe.py produces."""
    caps = {
        "binaries": {t: {"available": True, "path": f"/usr/bin/{t}", "version": "1.0"} for t in tools},
        "python_modules": {m: True for m in modules},
        "skills": {s: {"ready": True, "missing": None, "auto_install": True} for s in skills},
    }
    (data_dir / "system_caps.json").write_text(json.dumps(caps), encoding="utf-8")
    return caps


class MachineCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.baseline = self.data_dir / mc.BASELINE_NAME

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *argv):
        return mc.main([*argv, "--data-dir", str(self.data_dir)])

    # --- baseline lifecycle -------------------------------------------------

    def test_first_run_establishes_baseline_silently(self):
        _write_caps(self.data_dir, tools=["git", "ffmpeg"], modules=["httpx"], skills=["weather"])
        rc = self._run()
        self.assertEqual(rc, 0)
        self.assertTrue(self.baseline.exists())
        doc = json.loads(self.baseline.read_text())
        self.assertEqual(doc["tools"], ["ffmpeg", "git"])  # sorted
        self.assertEqual(doc["modules"], ["httpx"])
        self.assertEqual(doc["skills"], ["weather"])

    def test_no_drift_when_current_matches(self):
        _write_caps(self.data_dir, tools=["git", "ffmpeg"], modules=["httpx"], skills=["weather"])
        self._run()  # establish
        rc = self._run()  # check
        self.assertEqual(rc, 0)

    def test_no_drift_when_current_is_superset(self):
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])
        self._run()  # baseline = {git}
        _write_caps(self.data_dir, tools=["git", "blender"], modules=[], skills=[])
        rc = self._run()  # more tools than baseline is not drift
        self.assertEqual(rc, 0)

    # --- regression detection ----------------------------------------------

    def test_tool_regression_detected(self):
        _write_caps(self.data_dir, tools=["git", "soffice"], modules=[], skills=[])
        self._run()  # baseline includes soffice
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])  # soffice vanished
        rc = self._run()
        self.assertEqual(rc, 1)  # plain check exits 1 on drift

    def test_module_and_skill_regression(self):
        _write_caps(self.data_dir, tools=["git"], modules=["httpx", "openpyxl"], skills=["weather", "spreadsheet"])
        self._run()
        _write_caps(self.data_dir, tools=["git"], modules=["httpx"], skills=["weather"])
        current = mc.good_sets(mc.load_caps(self.data_dir))
        baseline = mc.load_baseline(self.data_dir)
        ev = mc.evaluate(current, baseline)
        self.assertTrue(ev["has_drift"])
        self.assertEqual(ev["regressions"]["modules"], ["openpyxl"])
        self.assertEqual(ev["regressions"]["skills"], ["spreadsheet"])
        self.assertEqual(ev["regressions"]["tools"], [])

    # --- --notify exit discipline + ping ------------------------------------

    def test_notify_exits_zero_on_drift(self):
        _write_caps(self.data_dir, tools=["git", "gimp"], modules=[], skills=[])
        self._run()
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])  # gimp gone
        with mock.patch.object(mc, "notify_admin", return_value=True) as spy:
            rc = mc.main(["--notify", "--user-id", "999", "--data-dir", str(self.data_dir)])
        self.assertEqual(rc, 0)  # must not exit non-zero, or scheduler double-alerts
        spy.assert_called_once()
        sent_msg = spy.call_args.args[0]
        self.assertIn("gimp", sent_msg)
        self.assertIn("Report only", sent_msg)

    def test_notify_silent_when_green(self):
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])
        self._run()
        with mock.patch.object(mc, "notify_admin", return_value=True) as spy:
            rc = mc.main(["--notify", "--user-id", "999", "--data-dir", str(self.data_dir)])
        self.assertEqual(rc, 0)
        spy.assert_not_called()

    # --- ratchet (high-water mark) -----------------------------------------

    def test_notify_ratchets_improvement_into_baseline(self):
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])
        self._run()  # baseline = {git}
        _write_caps(self.data_dir, tools=["git", "inkscape"], modules=[], skills=[])
        mc.main(["--notify", "--user-id", "999", "--data-dir", str(self.data_dir)])
        doc = json.loads(self.baseline.read_text())
        self.assertIn("inkscape", doc["tools"])  # improvement folded in and now protected

    def test_notify_keeps_regression_flagged_in_baseline(self):
        _write_caps(self.data_dir, tools=["git", "nb"], modules=[], skills=[])
        self._run()  # baseline = {git, nb}
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])  # nb gone
        with mock.patch.object(mc, "notify_admin", return_value=True):
            mc.main(["--notify", "--user-id", "999", "--data-dir", str(self.data_dir)])
        doc = json.loads(self.baseline.read_text())
        self.assertIn("nb", doc["tools"])  # union keeps it, so it stays flagged next night too

    def test_plain_check_does_not_mutate_baseline(self):
        _write_caps(self.data_dir, tools=["git", "rclone"], modules=[], skills=[])
        self._run()  # baseline = {git, rclone}
        before = self.baseline.read_text()
        _write_caps(self.data_dir, tools=["git", "rclone", "hugo"], modules=[], skills=[])
        self._run()  # plain check, improvement present
        self.assertEqual(self.baseline.read_text(), before)  # untouched without --notify

    # --- capture (re-bless) -------------------------------------------------

    def test_capture_resets_baseline_and_clears_drift(self):
        _write_caps(self.data_dir, tools=["git", "docker"], modules=[], skills=[])
        self._run()  # baseline = {git, docker}
        _write_caps(self.data_dir, tools=["git"], modules=[], skills=[])  # docker deliberately removed
        rc = self._run("--capture")
        self.assertEqual(rc, 0)
        doc = json.loads(self.baseline.read_text())
        self.assertEqual(doc["tools"], ["git"])  # docker dropped from baseline
        self.assertEqual(self._run(), 0)  # no drift after re-bless

    # --- benign / malformed guards -----------------------------------------

    def test_missing_caps_is_benign(self):
        # no system_caps.json written at all
        rc = self._run()
        self.assertEqual(rc, 0)
        self.assertFalse(self.baseline.exists())  # nothing to baseline yet

    def test_empty_caps_does_not_false_alarm(self):
        _write_caps(self.data_dir, tools=["git", "ffmpeg"])
        self._run()  # baseline has tools
        # a truncated/partial probe with no good capabilities at all
        (self.data_dir / "system_caps.json").write_text(
            json.dumps({"binaries": {}, "python_modules": {}, "skills": {}}), encoding="utf-8")
        before = self.baseline.read_text()
        rc = self._run()
        self.assertEqual(rc, 0)  # treated as no-data, not a wipeout regression
        self.assertEqual(self.baseline.read_text(), before)  # baseline untouched

    # --- renderers ----------------------------------------------------------

    def test_drift_ping_lists_each_dimension(self):
        from datetime import datetime
        regressions = {"tools": ["soffice"], "modules": ["openpyxl"], "skills": ["spreadsheet"]}
        body = mc.build_drift_ping(regressions, datetime(2026, 7, 9, 4, 40))
        self.assertIn("Tools missing: soffice", body)
        self.assertIn("Python modules missing: openpyxl", body)
        self.assertIn("Skills degraded: spreadsheet", body)
        self.assertIn("2026-07-09 04:40", body)

    def test_summary_green_line_reports_counts(self):
        _write_caps(self.data_dir, tools=["git", "ffmpeg"], modules=["httpx"], skills=["weather"])
        self._run()
        current = mc.good_sets(mc.load_caps(self.data_dir))
        baseline = mc.load_baseline(self.data_dir)
        summary = mc.build_summary(mc.evaluate(current, baseline), current)
        self.assertIn("Machine sanity: OK", summary)
        self.assertIn("2 tools", summary)
        self.assertIn("1 modules", summary)
        self.assertIn("1 skills", summary)


if __name__ == "__main__":
    unittest.main()
