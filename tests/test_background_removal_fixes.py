"""background-removal fixes, Linux bot review 2026-09-27. Each fails on the code before them.

Needs rembg and its u2net model already downloaded (~/.u2net/u2net.onnx);
skips rather than download 170 MB.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'numpy', 'rembg') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "background-removal" / "scripts" / "rembg_wrapper.py"
MODEL = Path.home() / ".u2net" / "u2net.onnx"
ADOBE_RGB = Path("/usr/share/color/icc/colord/AdobeRGB1998.icc")


def ready():
    try:
        import rembg  # noqa: F401
    except ImportError:
        return False
    return MODEL.exists()


def subject():
    """A red disc on a soft gradient: about 14 percent of the frame is the subject."""
    yy, xx = np.mgrid[0:400, 0:600]
    im = Image.fromarray(np.dstack([90 + xx * 0.1, 110 + yy * 0.1, 130 + 0 * xx]).astype(np.uint8))
    d = ImageDraw.Draw(im)
    d.ellipse((200, 100, 400, 300), fill=(200, 40, 40))
    d.rectangle((270, 60, 330, 110), fill=(30, 30, 30))
    return im


@unittest.skipUnless(ready(), "needs rembg and ~/.u2net/u2net.onnx")
class BackgroundRemoval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("rembg_wrapper", SCRIPT)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_jpeg_output_is_a_jpeg(self):
        subject().save(self.d / "in.png")
        self.mod.remove_background(str(self.d / "in.png"), str(self.d / "out.jpg"))
        out = Image.open(self.d / "out.jpg")
        self.assertEqual(out.format, "JPEG", "PNG bytes under a .jpg name")
        self.assertEqual(out.getpixel((0, 0)), (255, 255, 255))

    @unittest.skipUnless(ADOBE_RGB.exists(), "needs colord's AdobeRGB1998.icc")
    def test_colour_profile_is_carried(self):
        icc = ADOBE_RGB.read_bytes()
        subject().save(self.d / "in.jpg", quality=95, icc_profile=icc)
        self.mod.remove_background(str(self.d / "in.jpg"), str(self.d / "out.png"))
        self.assertEqual(Image.open(self.d / "out.png").info.get("icc_profile"), icc)

    def test_batch_keeps_inputs_that_differ_only_by_extension(self):
        src = self.d / "in"
        src.mkdir()
        subject().save(src / "photo.jpg", quality=95)
        subject().transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(src / "photo.png")
        self.mod.batch_remove(str(src), str(self.d / "out"))
        self.assertEqual(len(list((self.d / "out").iterdir())), 2, "one output overwrote the other")

    def test_batch_loads_the_model_once(self):
        import rembg
        import rembg.bg
        real = rembg.bg.new_session
        calls = []

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        src = self.d / "in"
        src.mkdir()
        for i in range(3):
            subject().rotate(i * 90, expand=True).save(src / f"p{i}.png")
        if hasattr(self.mod, "_sessions"):
            self.mod._sessions.clear()
        with mock.patch.object(rembg.bg, "new_session", counting), mock.patch.object(rembg, "new_session", counting):
            self.mod.batch_remove(str(src), str(self.d / "out"))
        self.assertEqual(len(calls), 1, f"the model loaded {len(calls)} times for 3 images")

    def test_alpha_matting_runs_on_a_copy_of_at_most_two_megapixels(self):
        # Linux bot sweep 2026-10-07: closed-form matting at full size passed
        # 6 GB in six seconds on a 12 MP photo (OOM-killed in a capped
        # scope; inside the bot's service it can take the bot). The spy does the
        # plain cutout so the red run cannot blow up.
        import rembg
        real = rembg.remove
        seen = []

        def spy(img, *args, alpha_matting=False, **kwargs):
            seen.append((img.size, alpha_matting))
            return real(img, *args, **kwargs)

        src = self.d / "big.jpg"
        subject().resize((3000, 2000)).save(src, quality=90)
        with mock.patch.object(rembg, "remove", spy):
            self.mod.remove_background(str(src), str(self.d / "cut.png"), alpha_matting=True)
        (w, h), matting = seen[-1]
        self.assertTrue(matting)
        self.assertLessEqual(w * h, 2_000_000)
        out = Image.open(self.d / "cut.png")
        self.assertEqual(out.size, (3000, 2000))
        self.assertEqual(out.mode, "RGBA")

    def test_a_batch_into_its_own_folder_keeps_png_originals(self):
        # Linux bot sweep 2026-10-07: photo.png came out as photo.png in the
        # same folder, the cutout written over the original
        src = self.d / "in"
        src.mkdir()
        subject().save(src / "photo.png")
        before = (src / "photo.png").read_bytes()
        with self.assertRaises(SystemExit):
            self.mod.batch_remove(str(src), str(src))
        self.assertEqual((src / "photo.png").read_bytes(), before)

    def test_16_bit_grey_keeps_the_subject_only(self):
        grey = np.asarray(subject().convert("L")).astype(np.uint16) * 257
        Image.fromarray(grey).save(self.d / "grey16.png")
        self.mod.remove_background(str(self.d / "grey16.png"), str(self.d / "out.png"))
        alpha = np.asarray(Image.open(self.d / "out.png").convert("RGBA"))[..., 3]
        # Pillow's convert clipped the 16 bit values at 255: most of the frame went white and was kept
        self.assertLess((alpha > 128).mean(), 0.25)


if __name__ == "__main__":
    unittest.main()
