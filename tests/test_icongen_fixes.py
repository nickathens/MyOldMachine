"""icon-gen fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "icon-gen" / "scripts" / "icongen.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=300)


class Icons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        wide = Image.new("RGBA", (400, 200), (200, 30, 30, 255))
        wide.save(self.d / "wide.png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_wide_art_is_padded_not_stretched(self):
        run("resize", self.d / "wide.png", self.d / "w.png", "128")
        icon = Image.open(self.d / "w.png")
        self.assertEqual(icon.getpixel((64, 2))[3], 0, "stretched to fill the square")

    def test_ios_icons_have_no_alpha(self):
        run("ios", self.d / "wide.png", self.d / "ios")
        self.assertEqual(Image.open(self.d / "ios" / "ios-180x180.png").mode, "RGB")

    @unittest.skipUnless(shutil.which("inkscape"), "needs Inkscape")
    def test_svg_input_works(self):
        svg = self.d / "logo.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                       '<circle cx="50" cy="50" r="40" fill="#1e88e5"/></svg>')
        r = run("resize", svg, self.d / "s.png", "64")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertGreater(Image.open(self.d / "s.png").getpixel((32, 32))[2], 200)


if __name__ == "__main__":
    unittest.main()
