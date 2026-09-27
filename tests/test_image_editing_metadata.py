"""image-editing: orientation, colour profile, dpi and transparency survive an edit.

Linux bot review 2026-09-27: every image.py command dropped the ICC profile and the
dpi (PIL's default), never applied the EXIF orientation (so a portrait phone
photo came back sideways), saved JPEG at PIL's quality 75, and converting a
transparent image to JPEG turned it black or failed (mode LA).
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'cv2', 'numpy') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "image-editing" / "scripts"
IMAGE = SCRIPTS / "image.py"


class Metadata(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="imgmeta-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        im = Image.new("RGB", (200, 100), (0, 0, 255))
        im.paste((255, 0, 0), (0, 0, 100, 100))
        exif = Image.Exif()
        exif[0x0112] = 6  # display rotated 90 degrees clockwise
        self.srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.src = self.d / "photo.jpg"
        im.save(self.src, exif=exif, dpi=(300, 300), icc_profile=self.srgb, quality=95)

    def run_image(self, *args):
        run = subprocess.run([sys.executable, str(IMAGE), *map(str, args)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_every_editing_command_keeps_orientation_profile_and_dpi(self):
        cases = {
            "resize": ["--width", "50"], "crop": ["--box", "0", "0", "100", "100"],
            "rotate": ["--angle", "0"], "filter": ["--filter", "blur"],
            "adjust": ["--brightness", "1.1"], "thumbnail": ["--size", "60"],
            "border": ["--width", "2"], "convert": [],
        }
        for cmd, extra in cases.items():
            with self.subTest(command=cmd):
                out = self.d / f"{cmd}.jpg"
                self.run_image(cmd, self.src, out, *extra)
                im = Image.open(out)
                shown = ImageOps.exif_transpose(im).size
                if cmd not in ("crop", "rotate"):
                    self.assertGreater(shown[1], shown[0], f"{cmd} came out sideways: {shown}")
                self.assertEqual(im.info.get("icc_profile"), self.srgb)
                self.assertEqual(tuple(round(v) for v in im.info.get("dpi", (0, 0))), (300, 300))

    def test_crop_box_is_read_on_the_upright_photo(self):
        # Upright the photo is 100 wide and 200 tall, red on top, blue below.
        # This box is blue when read upright; on the stored 200x100 pixels it
        # would sit half on red and half outside the image.
        out = self.d / "bottom.png"
        self.run_image("crop", self.src, out, "--box", "50", "150", "100", "190")
        r, g, b = Image.open(out).convert("RGB").getpixel((25, 20))
        self.assertGreater(b, 200)
        self.assertLess(r, 60)

    def test_grayscale_drops_the_rgb_profile_only(self):
        out = self.d / "g.jpg"
        self.run_image("filter", self.src, out, "--filter", "grayscale")
        im = Image.open(out)
        self.assertEqual(im.mode, "L")
        self.assertIsNone(im.info.get("icc_profile"))
        self.assertEqual(tuple(round(v) for v in im.info["dpi"]), (300, 300))

    def test_transparency_becomes_white_in_jpeg(self):
        src = self.d / "logo.png"
        im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        im.paste((200, 0, 0, 255), (15, 15, 25, 25))
        im.save(src)
        la = self.d / "la.png"
        Image.new("LA", (10, 10), (128, 0)).save(la)
        self.run_image("convert", src, self.d / "logo.jpg")
        self.run_image("convert", la, self.d / "la.jpg")
        corner = Image.open(self.d / "logo.jpg").convert("RGB").getpixel((0, 0))
        self.assertTrue(all(c > 230 for c in corner), corner)

    def test_jpeg_edits_are_high_quality(self):
        big = self.d / "noise.jpg"
        import random
        rnd = random.Random(1)
        noise = Image.new("RGB", (256, 256))
        noise.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                       for _ in range(256 * 256)])
        noise.save(big, quality=100)
        self.run_image("rotate", big, self.d / "r.jpg", "--angle", "0")
        self.run_image("convert", big, self.d / "q75.jpg", "--quality", "75")
        self.assertGreater((self.d / "r.jpg").stat().st_size, (self.d / "q75.jpg").stat().st_size)

    def test_16_bit_colour_is_reported_and_its_loss_named(self):
        # Pillow opens a 16-bit RGB PNG as plain 8-bit "RGB"; info said so and
        # every edit flattened the master without a word
        import cv2
        import numpy as np
        deep = self.d / "deep.png"
        cv2.imwrite(str(deep), (np.random.default_rng(0).random((20, 30, 3)) * 65535).astype(np.uint16))
        info = subprocess.run([sys.executable, str(IMAGE), "info", deep], capture_output=True, text=True, timeout=60)
        self.assertIn('"bits_per_channel": 16', info.stdout)
        run = subprocess.run([sys.executable, str(IMAGE), "resize", deep, self.d / "r.png", "--width", "60"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("16 bits per channel", run.stderr)


if __name__ == "__main__":
    unittest.main()
