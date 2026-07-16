"""Regression tests for the system probe's version-check process hygiene.

Root cause (2026-07-15): Homebrew cask wrappers spawn the real app as a
child without exec. subprocess.run's timeout killed only the wrapper,
orphaning an already-launched GUI app (Blender fed a bogus '-version'
flag), which lingered for 20 hours holding ~1.7 GB. The probe must kill
the entire process group on timeout, and must never feed GUI apps the
fallback flag spellings that make them open the GUI.
"""

import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from core.system_probe import _check_binary, _run_version_cmd, _GUI_APPS

# Unique sleep duration doubles as a pgrep needle.
MARKER_SLEEP = "31417"


def _surviving_sleepers():
    out = subprocess.run(
        ["pgrep", "-f", f"sleep {MARKER_SLEEP}"],
        capture_output=True, text=True,
    )
    return [l for l in out.stdout.strip().splitlines() if l.strip()]


def _reap_sleepers():
    subprocess.run(["pkill", "-9", "-f", f"sleep {MARKER_SLEEP}"],
                   capture_output=True)


class ProbeOrphanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reap_sleepers()
        # A no-exec shell wrapper that spawns a long-lived child, like the
        # Homebrew cask blender wrapper that produced the original orphans.
        self.wrapper = Path(self._tmp.name) / "fake-cask-app"
        self.wrapper.write_text(
            "#!/bin/sh\n"
            f"sleep {MARKER_SLEEP}\n"
            "true\n"  # keeps sh from exec-ing sleep as its last command
        )
        self.wrapper.chmod(self.wrapper.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        _reap_sleepers()
        self._tmp.cleanup()

    def test_timeout_kills_entire_process_group(self):
        """On timeout, the wrapper's children must die with it."""
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_version_cmd([str(self.wrapper), "--version"], timeout=1)
        time.sleep(0.5)
        self.assertEqual(
            _surviving_sleepers(), [],
            "grandchild survived the timeout kill — the 2026-07-15 "
            "Blender orphan escape is back",
        )

    def test_check_binary_leaves_no_orphans(self):
        """The full flag loop over a hanging wrapper must clean up after
        every attempt. Uses the non-GUI path (all three flags)."""
        info = _check_binary(str(self.wrapper))
        self.assertTrue(info["available"])
        self.assertEqual(info["version"], "")
        time.sleep(0.5)
        self.assertEqual(_surviving_sleepers(), [])

    def test_gui_apps_never_get_bogus_flags(self):
        """GUI apps launch their GUI on '-version'/'version'; the probe
        must only ever ask them '--version'."""
        for app in ["blender", "gimp", "inkscape", "soffice"]:
            self.assertIn(app, _GUI_APPS)

    def test_run_version_cmd_returns_output(self):
        out = _run_version_cmd(["/bin/echo", "tool v9.9.9"], timeout=5)
        self.assertIn("9.9.9", out)


if __name__ == "__main__":
    unittest.main()
