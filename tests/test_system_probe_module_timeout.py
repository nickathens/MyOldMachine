"""Regression tests for the system probe's python-module import check.

Root cause (2026-08-07): the check gave every module a flat 5s to import.
basic_pitch (TensorFlow, 1.1 GB of dylibs) and realesrgan (PyTorch, 400 MB)
were still loading when the cut-off hit -- the macOS unified log shows both
killed at exactly 5.0s while Gatekeeper was validating library signatures --
so the nightly sanity check reported two installed, working skills as
degraded. A missing module fails instantly with ImportError; only a slow one
can time out, so a timeout must earn a warm retry before it counts as gone.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.system_probe import _check_python_module

# Longer than the first import budget, shorter than the retry's.
FIRST_IMPORT_SLEEP = 8


class ModuleImportTimeoutTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._prev_path = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(self.tmp)

    def tearDown(self):
        if self._prev_path is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = self._prev_path
        self._tmp.cleanup()

    def _write_module(self, name: str, body: str) -> None:
        (self.tmp / f"{name}.py").write_text(body, encoding="utf-8")

    def test_slow_first_import_is_retried_not_reported_missing(self):
        """Cold-then-warm import: slow enough to blow the first budget, fine after."""
        marker = self.tmp / "cold.marker"
        self._write_module("slowmod", (
            "import time\n"
            "from pathlib import Path\n"
            f"marker = Path({str(marker)!r})\n"
            "if not marker.exists():\n"
            "    marker.write_text('warm')\n"
            f"    time.sleep({FIRST_IMPORT_SLEEP})\n"
        ))

        # Sanity: the module really does exceed the first budget when cold.
        self.assertFalse(marker.exists())

        self.assertTrue(
            _check_python_module("slowmod"),
            "a module that imports slowly once must not be reported missing",
        )
        self.assertTrue(marker.exists(), "the cold import should have run")

    def test_missing_module_fails_fast_without_paying_the_retry(self):
        """No import budget is spent on a module that is genuinely absent."""
        started = time.monotonic()
        self.assertFalse(_check_python_module("definitely_not_a_real_module_xyz"))
        self.assertLess(
            time.monotonic() - started, FIRST_IMPORT_SLEEP,
            "ImportError is immediate; it must not wait out any timeout",
        )

    def test_hanging_import_is_bounded_and_reported_missing(self):
        """An import that never finishes still ends, and still counts as missing."""
        self._write_module("hangmod", "import time\ntime.sleep(600)\n")

        with patch("core.system_probe._MODULE_IMPORT_TIMEOUTS", (0.5, 1.0)):
            started = time.monotonic()
            self.assertFalse(_check_python_module("hangmod"))
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 20, "the retry ladder must stay bounded")
        self.assertFalse(
            self._hangmod_survivors(),
            "the timed-out import must not be left running",
        )

    def _hangmod_survivors(self):
        out = subprocess.run(
            ["pgrep", "-f", "import hangmod"],
            capture_output=True, text=True,
        )
        mine = str(os.getpid())
        return [
            pid for pid in out.stdout.split()
            if pid.strip() and pid.strip() != mine
        ]

    def test_installed_module_still_reads_as_present(self):
        """The happy path is unchanged: a real, fast module imports cleanly."""
        self.assertTrue(_check_python_module("json"))


if __name__ == "__main__":
    unittest.main()
