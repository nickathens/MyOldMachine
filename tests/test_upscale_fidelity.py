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
from unittest import mock

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

    def test_metrics_in_every_mode(self):
        """--metrics printed nothing for --mode plain and lanczos (Linux bot sweep 2026-10-07)."""
        src = self.d / "m.png"
        Image.fromarray((np.random.default_rng(1).random((24, 32, 3)) * 255).astype(np.uint8)).save(src)
        for mode in ("plain", "lanczos", "hybrid"):
            r = run(HYBRID, src, self.d / f"m_{mode}.png", "--mode", mode, "--metrics")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("downscale back PSNR", r.stdout, mode)

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


FAKE_SYSTEMD_RUN = """#!/bin/sh
# stands in for systemd-run: logs its arguments, then runs what follows --
echo "$*" >> "$FAKE_SCOPE_LOG"
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done
shift
case "$FAKE_SCOPE" in
    none) exit 1 ;;
    kill) [ "$1" = "true" ] && exit 0; kill -9 $$ ;;
esac
exec "$@"
"""


class MemoryCap(unittest.TestCase):
    """Linux bot sweep 2026-10-07: a 600x600 picture at 4x peaked at 4.48 GB,
    a 1080p frame at 4x needs about 5 GB, and both ran inside the bot's
    service, where an out of memory kill on Linux stops the whole service.
    Each script now re-runs itself in a capped user scope; macOS runs as
    before."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        fake = self.d / "bin" / "systemd-run"
        fake.parent.mkdir()
        fake.write_text(FAKE_SYSTEMD_RUN)
        fake.chmod(0o755)
        self.log = self.d / "scope.log"
        self.env = {k: v for k, v in os.environ.items() if k != "UPSCALE_ISOLATED"}
        self.env.update(PATH=f"{fake.parent}{os.pathsep}{os.environ['PATH']}", FAKE_SCOPE_LOG=str(self.log))

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, scope, script, *args):
        return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                              timeout=300, env={**self.env, "FAKE_SCOPE": scope})

    def test_estimate_tracks_the_measured_peaks(self):
        spec = importlib.util.spec_from_file_location("memory_cap", SCRIPTS / "memory_cap.py")
        cap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cap)
        # measured on the Linux bot's machine 2026-10-07 (hybrid; the legacy wrapper
        # peaked lower: 2.11 GB at 4x and 1.20 GB at 2x with its 256 tile)
        for (w, h, scale, tile, neural), peak in (((600, 600, 4, 512, True), 4.48),
                                                  ((600, 600, 4, 256, True), 2.12),
                                                  ((600, 600, 2, 512, True), 1.73),
                                                  ((1920, 1080, 4, 512, False), 1.73)):
            est = cap.estimate_gb(w, h, scale, tile, neural)
            self.assertGreaterEqual(est, peak - 0.05, (w, h, scale, tile, neural))
            self.assertLess(est, peak + 0.6, (w, h, scale, tile, neural))

    def test_the_scripts_run_again_inside_a_capped_scope(self):
        src = self.d / "a.png"
        Image.new("RGB", (24, 16), (90, 120, 150)).save(src)
        jobs = [(HYBRID, ("--mode", "lanczos"))]
        if WEIGHTS.exists():
            jobs.append((LEGACY, ("--scale", "2")))
        for script, args in jobs:
            self.log.write_text("")
            out = self.d / f"{script.stem}.png"
            r = self.run_with("exec", script, src, out, *args)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertTrue(out.exists())
            calls = self.log.read_text().splitlines()
            self.assertTrue(any("MemoryMax=" in c and str(script) in c for c in calls),
                            f"{script.name} ran outside a capped scope: {calls}")

    def test_a_kill_by_the_cap_is_explained(self):
        src = self.d / "a.png"
        Image.new("RGB", (24, 16)).save(src)
        r = self.run_with("kill", HYBRID, src, self.d / "k.png", "--mode", "lanczos")
        self.assertEqual(r.returncode, 1)
        self.assertIn("memory cap", r.stderr)

    def test_macos_runs_as_before(self):
        spec = importlib.util.spec_from_file_location("memory_cap_platform", SCRIPTS / "memory_cap.py")
        cap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cap)
        env = {k: v for k, v in os.environ.items() if k != "UPSCALE_ISOLATED"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(cap.sys, "platform", "darwin"):
                self.assertIsNone(cap.unprotected_refusal(1920, 1080, 4, 512))
            with mock.patch.object(cap.sys, "platform", "linux"):
                self.assertIn("memory-capped", cap.unprotected_refusal(1920, 1080, 4, 512))

    @unittest.skipUnless(sys.platform.startswith("linux"), "the refusal is Linux only")
    def test_too_big_without_a_scope_is_refused(self):
        src = self.d / "big.png"
        Image.new("RGB", (1920, 1080), (90, 120, 150)).save(src)
        scripts = [HYBRID] + ([LEGACY] if WEIGHTS.exists() else [])
        for script in scripts:
            out = self.d / f"big_{script.stem}.png"
            r = self.run_with("none", script, src, out, "--scale", "4", "--tile", "512")
            self.assertNotEqual(r.returncode, 0, f"{script.name} ran a 5 GB job beside the bot")
            self.assertIn("memory-capped", r.stderr)
            self.assertFalse(out.exists())


FACE_WEIGHTS = [Path.home() / ".cache" / "realesrgan" / name for name in
                ("GFPGANv1.3.pth", "gfpgan/weights/detection_Resnet50_Final.pth",
                 "gfpgan/weights/parsing_parsenet.pth")]


@unittest.skipUnless(WEIGHTS.exists() and all(p.exists() for p in FACE_WEIGHTS),
                     "needs the cached RealESRGAN and GFPGAN weights")
class FaceEnhance(unittest.TestCase):
    """--face died on a card the CUDA wheels carry no kernels for (a GTX
    970): GFPGAN chose CUDA on its own. It also saves its detector's weights
    under the relative 'gfpgan/weights', so they went into whatever directory
    the run started in (Linux bot sweep 2026-10-07)."""

    def test_face_mode_runs_and_leaves_the_working_directory_alone(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d, "in.png")
            Image.new("RGB", (48, 48), (180, 140, 120)).save(src)
            cwd = Path(d, "cwd")
            cwd.mkdir()
            r = subprocess.run([sys.executable, str(LEGACY), str(src), str(Path(d, "out.png")),
                                "--scale", "2", "--face"], capture_output=True, text=True,
                               timeout=600, cwd=cwd)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertEqual(Image.open(Path(d, "out.png")).size, (96, 96))
            self.assertEqual(list(cwd.iterdir()), [], "weights were downloaded into the working directory")


if __name__ == "__main__":
    unittest.main()
