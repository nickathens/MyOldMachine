"""colorgrade fixes from the Linux bot review 2026-09-27.

- rgb_to_hsv summed two hue sectors whenever two channels tied for the maximum
  (yellow 120, cyan 360, magenta 600), on every LUT lattice and in 8-bit video;
- dctlgen's "proof" was not the DCTL's maths (smoothstep edges, a narrower
  window, a Lab rotation), and a hue band crossing 0/360 selected nothing in
  the DCTL while the proof showed a full matte;
- a LUT folder named after an input like "Director's cut [v2]" failed the render
  at the very end (ffmpeg filter syntax);
- --max-iterations 0 crashed after the whole measurement.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import shutil as _shutil  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('numpy',) if _ilu.find_spec(m) is None]
_NEEDS += [t for t in ('ffmpeg', 'ffprobe') if _shutil.which(t) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import colorsys
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

SKILL = Path(__file__).resolve().parent.parent / "skills" / "colorgrade"
sys.path.insert(0, str(SKILL / "scripts"))

import cgcore as C  # noqa: E402
import dctlgen  # noqa: E402
import cgvideo as V  # noqa: E402


class HueTies(unittest.TestCase):
    def test_matches_colorsys_on_8bit_colours_with_ties(self):
        rng = np.random.default_rng(3)
        q = (rng.integers(0, 256, (20000, 3)) / 255).astype(np.float32)
        q[::3, 1] = q[::3, 0]
        q[::5, 2] = q[::5, 1]
        q[::7, 0] = q[::7, 2]
        h, _, _ = C.rgb_to_hsv(q)
        ref = np.array([colorsys.rgb_to_hsv(*p)[0] * 360 for p in q], dtype=np.float32)
        chroma = q.max(1) - q.min(1) > 1e-6
        err = np.abs(((h - ref + 180) % 360) - 180)[chroma]
        self.assertLess(float(err.max()), 1e-3)
        self.assertLess(float(h.max()), 360.0)


def _hsv_pixel(hue, s=0.8, v=0.8):
    return colorsys.hsv_to_rgb((hue % 360) / 360.0, s, v)


class DctlProofIsTheDctl(unittest.TestCase):
    RED_BAND = dict(hueLowSoft=345.0, hueLow=350.0, hueHigh=10.0, hueHighSoft=15.0,
                    satLo=0.1, satHi=0.2, valLo=0.1, valHi=0.2,
                    winX=0.5, winY=0.5, winRX=2.0, winRY=2.0, winSoft=0.25)

    def _planes(self, hues):
        rgb = np.array([[_hsv_pixel(h) for h in hues]], dtype=np.float32)
        return C.rgb_to_hsv(rgb)

    def test_a_band_crossing_zero_selects_both_sides(self):
        h, s, v = self._planes([355.0, 0.0, 5.0, 30.0, 180.0])
        m = dctlgen.matte_from_params(h, s, v, self.RED_BAND)[0]
        self.assertTrue(np.allclose(m[:3], 1.0), m)
        self.assertTrue(np.allclose(m[3:], 0.0), m)

    def test_edges_are_the_dctl_linear_ramps(self):
        # a quarter of the way up the low ramp (345 -> 350) is 0.25 on the
        # DCTL's linear ramp; smoothstep gives 0.156 there (they agree at 0.5,
        # so the midpoint could not tell them apart)
        h, s, v = self._planes([346.25])
        m = dctlgen.matte_from_params(h, s, v, self.RED_BAND)[0, 0]
        self.assertAlmostEqual(float(m), 0.25, places=3)

    def test_window_fades_out_between_one_minus_and_one_plus_soft(self):
        p = dict(self.RED_BAND, winRX=0.25, winRY=0.25, winSoft=0.2)
        h, s, v = self._planes([0.0] * 100)
        m = dctlgen.matte_from_params(h, s, v, p)[0]
        # pixel centres run along one row; d = |x - 0.5| / 0.25
        x = (np.arange(100) + 0.5) / 100
        d = np.abs(x - 0.5) / 0.25
        want = 1.0 - np.clip((d - 0.8) / 0.4, 0.0, 1.0)
        # the row sits at y 0.5 exactly, so d is the horizontal distance only
        self.assertTrue(np.allclose(m, want, atol=1e-5))

    def test_colour_move_is_the_dctl_hsv_rotation(self):
        rng = np.random.default_rng(5)
        rgb = rng.random((64, 64, 3)).astype(np.float32)
        same = dctlgen.dctl_apply(rgb, np.ones(rgb.shape[:2], np.float32), 0.0, 1.0)
        self.assertLess(float(np.abs(same - rgb).max()), 1e-5)
        red = np.array([[[0.8, 0.1, 0.1]]], dtype=np.float32)
        green = dctlgen.dctl_apply(red, np.ones((1, 1), np.float32), 120.0, 1.0)
        self.assertTrue(np.allclose(green, [[[0.1, 0.8, 0.1]]], atol=1e-5), green)

    def test_the_dctl_reads_hues_relative_to_the_low_soft_handle(self):
        text = (SKILL / "dctl" / "LensIsolate.dctl").read_text(encoding="utf-8")
        self.assertIn("hueLowSoft + _fmod(h - hueLowSoft + 360.0f, 360.0f)", text)
        self.assertIn("ramp(hRel, hueLowSoft, hLo)", text)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class RenderWithAwkwardPaths(unittest.TestCase):
    def test_apostrophe_brackets_comma_colon_semicolon(self):
        root = Path(tempfile.mkdtemp(prefix="cg-path-"))
        self.addCleanup(shutil.rmtree, root, True)
        d = root / "Director's [cut], v1; take:2"
        d.mkdir()
        src = d / "src.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=25:duration=2", "-pix_fmt", "yuv420p",
                        str(src)], check=True)
        media = V.probe(str(src))
        shots = V.shots_from_cuts(media, [25])
        luts = {}
        for s in shots:
            p = d / f"shot_{s.index + 1:03d}.cube"
            C.write_cube(str(p), C.bake_lut(C.Grade(), 17), 17)
            luts[s.index] = str(p)
        out = d / "out.mp4"
        staged_before = set(Path(tempfile.gettempdir()).glob("cg_luts_*"))
        V.render(media, shots, luts, str(out))
        self.assertGreater(out.stat().st_size, 0)
        still = d / "still.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
                        str(d / "in.png")], check=True)
        V.render_still(str(d / "in.png"), luts[0], str(still))
        self.assertTrue(still.is_file())
        # the staging folders are gone again
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("cg_luts_*")), staged_before)
        self.assertTrue(os.path.isfile(luts[0]), "the real LUT must survive the cleanup")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class ZeroIterations(unittest.TestCase):
    def test_grade_runs_with_max_iterations_zero(self):
        root = Path(tempfile.mkdtemp(prefix="cg-iter-"))
        self.addCleanup(shutil.rmtree, root, True)
        src = root / "src.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=25:duration=1", "-pix_fmt", "yuv420p",
                        str(src)], check=True)
        run = subprocess.run([sys.executable, str(SKILL / "scripts" / "cg.py"), "grade", str(src),
                              "--single-shot", "--no-render", "--no-sheet", "--lut-size", "17",
                              "--max-iterations", "0", "--workdir", str(root / "w")],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
