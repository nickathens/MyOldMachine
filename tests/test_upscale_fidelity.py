"""upscale skill fixes, Linux bot review 2026-09-27.

Both scripts lost what the pixels meant: EXIF orientation ignored (portrait
photos came back sideways), 16-bit RGB masters flattened to 8-bit even on the
depth keeping route (Pillow reads them as 8-bit RGB), ICC profiles dropped, and
the legacy wrapper printed "Saved" and exited 0 when nothing was written. Each
test fails on the code before the fixes. The neural runs use tiny images and
take a second or two on the CPU.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'cv2', 'numpy') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import glob
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "upscale" / "scripts"
HYBRID = SCRIPTS / "hybrid_upscale.py"
LEGACY = SCRIPTS / "upscale.py"
WEIGHTS = Path.home() / ".cache" / "realesrgan" / "RealESRGAN_x2plus.pth"
RGB_ICC = sorted(glob.glob("/usr/share/color/icc/colord/AdobeRGB1998.ic[cm]"))


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True,
                          text=True, timeout=600)


def phone_jpeg(path):
    """64x32 as stored, a blue band on the left; EXIF 6 displays it 32x64."""
    im = Image.new("RGB", (64, 32), (200, 30, 30))
    im.paste((30, 30, 200), (0, 0, 16, 32))
    exif = Image.Exif()
    exif[0x0112] = 6
    im.save(path, exif=exif.tobytes(), quality=95)


class Orientation(unittest.TestCase):
    def test_orient_matches_pillow_for_every_tag(self):
        spec = importlib.util.spec_from_file_location("faithful_io", SCRIPTS / "faithful_io.py")
        fio = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fio)
        base = np.arange(5 * 3 * 3, dtype=np.uint8).reshape(5, 3, 3)
        with tempfile.TemporaryDirectory() as d:
            for tag in range(1, 9):
                path = os.path.join(d, f"o{tag}.jpg")
                exif = Image.Exif()
                exif[0x0112] = tag
                Image.fromarray(base).save(path, exif=exif.tobytes(), quality=100, subsampling=0)
                with Image.open(path) as im:
                    stored = np.asarray(im)
                    want = np.asarray(ImageOps.exif_transpose(im))
                self.assertEqual(fio.exif_orientation(path), tag)
                np.testing.assert_array_equal(fio.orient(stored, tag), want, err_msg=f"tag {tag}")


@unittest.skipUnless(WEIGHTS.exists(), "needs the cached RealESRGAN x2plus weights")
class Scripts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_scripts_follow_exif_orientation(self):
        src = self.d / "phone.jpg"
        phone_jpeg(src)
        for script, name in ((HYBRID, "h.png"), (LEGACY, "l.png")):
            out = self.d / name
            r = run(script, src, out, "--scale", "2")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            with Image.open(out) as im:
                self.assertEqual(im.size, (64, 128), f"{script.name} came back sideways")
                # the band that sits on the left as stored is on top once rotated
                r_, g_, b_ = im.convert("RGB").getpixel((32, 4))
                self.assertGreater(b_, r_, f"{script.name}: {im.getpixel((32, 4))}")

    def test_16_bit_colour_is_kept_or_refused(self):
        import cv2
        src = self.d / "deep.png"
        cv2.imwrite(str(src), (np.random.default_rng(0).random((20, 30, 3)) * 65535).astype(np.uint16))
        out = self.d / "deep_x2.png"
        r = run(HYBRID, src, out, "--mode", "lanczos")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        got = cv2.imread(str(out), cv2.IMREAD_UNCHANGED)
        self.assertEqual(got.dtype, np.uint16, "the depth keeping route flattened a 16-bit master")
        self.assertEqual(got.shape, (40, 60, 3))
        refused = run(HYBRID, src, self.d / "n.png", "--mode", "hybrid")
        self.assertNotEqual(refused.returncode, 0, "the 8-bit neural path took a 16-bit master silently")

    @unittest.skipUnless(RGB_ICC, "needs an RGB ICC profile from colord")
    def test_icc_profile_survives(self):
        icc = Path(RGB_ICC[0]).read_bytes()
        src = self.d / "tagged.png"
        Image.new("RGB", (48, 32), (40, 160, 90)).save(src, icc_profile=icc)
        for script, args in ((HYBRID, ("--mode", "lanczos")), (HYBRID, ("--mode", "hybrid")), (LEGACY, ("--scale", "2"))):
            out = self.d / f"t_{script.stem}_{args[-1]}.png"
            r = run(script, src, out, *args)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            with Image.open(out) as im:
                self.assertEqual(im.info.get("icc_profile"), icc, f"{script.name} {args} dropped the profile")

    def test_legacy_write_failure_exits_nonzero(self):
        src = self.d / "phone.jpg"
        phone_jpeg(src)
        ro = self.d / "ro"
        ro.mkdir()
        ro.chmod(0o555)
        try:
            r = run(LEGACY, src, ro / "x.png", "--scale", "2")
        finally:
            ro.chmod(0o755)
        self.assertNotEqual(r.returncode, 0, "printed Saved for a file that was never written")
        self.assertFalse((ro / "x.png").exists())

    def test_odd_and_zero_tiles_run(self):
        src = self.d / "odd.png"
        Image.new("RGB", (41, 23), (90, 120, 150)).save(src)
        for tile in ("0", "15"):
            out = self.d / f"t{tile}.png"
            r = run(HYBRID, src, out, "--scale", "2", "--tile", tile)
            self.assertEqual(r.returncode, 0, f"--tile {tile}: {r.stderr[-800:]}")
            with Image.open(out) as im:
                self.assertEqual(im.size, (82, 46))

    def test_a_non_png_name_is_refused(self):
        src = self.d / "a.png"
        Image.new("RGB", (8, 8)).save(src)
        r = run(HYBRID, src, self.d / "a_x2.jpg", "--mode", "lanczos")
        self.assertNotEqual(r.returncode, 0, "wrote PNG bytes under a .jpg name")



class StalledDownload(unittest.TestCase):
    """A weights server that stops answering fails the run instead of hanging it (audit pass 2)."""

    def test_a_silent_server_times_out(self):
        import socket

        with socket.socket() as srv, tempfile.TemporaryDirectory() as home:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)    # the handshake completes, and nothing is ever sent back
            port = srv.getsockname()[1]
            code = (
                f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                "import hybrid_upscale as h\n"
                "h.DOWNLOAD_TIMEOUT = 1\n"
                f"h.WEIGHTS[4] = ('stall.pth', 'http://127.0.0.1:{port}/stall.pth')\n"
                "try:\n"
                "    h.load_model(4, 'cpu')\n"
                "except OSError as e:\n"
                "    print('failed:', type(e).__name__)\n"
            )
            try:
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                                   env={**os.environ, "HOME": home})
            except subprocess.TimeoutExpired:
                self.fail("the download hung on a server that never answered")
            self.assertIn("failed:", r.stdout, r.stderr[-800:])
            self.assertFalse(Path(home, ".cache", "realesrgan", "stall.pth").exists())


if __name__ == "__main__":
    unittest.main()
