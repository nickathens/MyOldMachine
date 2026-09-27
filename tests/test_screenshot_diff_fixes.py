"""screenshot-diff fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'numpy', 'scipy') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "screenshot-diff" / "scripts" / "screenshot_diff.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=60)


class ScreenshotDiff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        Image.new("RGB", (1920, 1080), "white").save(self.d / "a.png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_changed_button_is_a_difference(self):
        b = Image.new("RGB", (1920, 1080), "white")
        b.paste((220, 30, 30), (900, 500, 1000, 540))
        b.save(self.d / "b.png")
        r = run("compare", self.d / "a.png", self.d / "b.png", "-o", self.d / "d.png")
        self.assertEqual(r.returncode, 1, "a button turning red passed as a match: " + r.stdout)

    def test_a_taller_page_is_a_difference(self):
        Image.new("RGB", (1920, 1300), "white").save(self.d / "t.png")
        r = run("compare", self.d / "a.png", self.d / "t.png", "-o", self.d / "d.png")
        self.assertEqual(r.returncode, 1, "a page that grew 220 px passed as a match: " + r.stdout)

    def test_identical_images_match(self):
        self.assertEqual(run("compare", self.d / "a.png", self.d / "a.png").returncode, 0)


if __name__ == "__main__":
    unittest.main()
