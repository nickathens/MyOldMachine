"""gimp batch.py fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "gimp" / "scripts" / "batch.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=120)


@unittest.skipUnless(shutil.which("convert"), "needs ImageMagick")
class Batch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_width_keeps_the_aspect_ratio(self):
        Image.new("RGB", (1000, 2000), (90, 120, 160)).save(self.d / "p.jpg")
        run("resize", self.d / "p.jpg", self.d / "o.jpg", "--width", "800")
        self.assertEqual(Image.open(self.d / "o.jpg").size, (800, 1600))

    def test_a_thumbnail_of_a_phone_photo_is_upright(self):
        im = Image.new("RGB", (400, 200), (200, 30, 30))
        exif = Image.Exif()
        exif[0x0112] = 6
        im.save(self.d / "phone.jpg", exif=exif.tobytes())
        run("thumbnail", self.d / "phone.jpg", self.d / "t.jpg", "--size", "100")
        self.assertEqual(Image.open(self.d / "t.jpg").size, (50, 100))

    def test_transparency_becomes_white_in_jpeg(self):
        Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(self.d / "logo.png")
        run("convert", self.d / "logo.png", self.d / "logo.jpg")
        self.assertTrue(all(c > 240 for c in Image.open(self.d / "logo.jpg").convert("RGB").getpixel((1, 1))))

    def test_batch_convert_writes_the_files(self):
        (self.d / "in").mkdir()
        Image.new("RGB", (20, 20)).save(self.d / "in" / "a.png")
        run("convert", self.d / "in", self.d / "out", "--batch")
        self.assertTrue((self.d / "out" / "a.jpg").exists())


if __name__ == "__main__":
    unittest.main()
