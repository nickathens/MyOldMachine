"""inkscape vector.py fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'numpy') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "inkscape" / "scripts" / "vector.py"


def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                          timeout=300, cwd=cwd)


@unittest.skipUnless(shutil.which("inkscape"), "needs inkscape")
class Vector(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_convert_without_an_output_writes_a_png_beside_the_input(self):
        svg = self.d / "logo.svg"
        self.assertEqual(run("logo", "--title", "Mark", "-o", svg).returncode, 0)
        r = run("convert", "-i", svg, "--format", "png", "--dpi", "48")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        png = self.d / "logo.png"
        self.assertTrue(png.exists(), "wrote an SVG to /tmp instead")
        self.assertEqual(Image.open(png).format, "PNG")

    def test_a_long_title_stays_on_the_canvas(self):
        svg, png = self.d / "s.svg", self.d / "s.png"
        run("social", "--title", "Grand Opening of the Summer Festival 2026", "-o", svg)
        r = run("convert", "-i", svg, "-o", png, "--dpi", "48")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        img = np.asarray(Image.open(png).convert("L"), dtype=float)
        edge = max(1, img.shape[1] // 40)
        # white text reaching an edge column shows up as bright pixels there
        self.assertLess(max(img[:, :edge].max(), img[:, -edge:].max()), 120, "the title ran off the canvas")


class NothingIsLost(unittest.TestCase):
    """A failing Inkscape stands in for the real one, so no file is ever opened (audit pass 2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        bin_dir = self.d / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "inkscape"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), "convert", *map(str, args)],
                              capture_output=True, text=True, timeout=60, env=self.env)

    def test_convert_never_deletes_its_input(self):
        # No -o and a .png input: the default output name is the input's own.
        picture = self.d / "picture.png"
        picture.write_bytes(b"the only copy")
        r = self.convert("-i", picture)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(picture.exists(), "convert deleted its own input")
        self.assertEqual(picture.read_bytes(), b"the only copy")

    def test_a_failed_export_keeps_the_previous_output(self):
        svg, png = self.d / "logo.svg", self.d / "logo.png"
        svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        png.write_bytes(b"last week's export")
        r = self.convert("-i", svg, "-o", png)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(png.read_bytes(), b"last week's export", "a failed export destroyed the old file")
        self.assertEqual(sorted(p.name for p in self.d.iterdir()), ["bin", "logo.png", "logo.svg"])


if __name__ == "__main__":
    unittest.main()
