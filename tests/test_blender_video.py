"""blender-video rewrite, Linux bot review 2026-09-27.

The old script never rendered on Blender 5.2 (pre 5.0 output API, removed
new_effect arguments) and still exited 0; it never added sound; it ran every
clip at the scene's default rate; a cut left the start black; AgX shifted the
colours. These tests render for real, so they need Blender and ffmpeg and
take about a minute: they run only with BLENDER_TESTS=1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "blender-video" / "scripts" / "video_edit.py"
LIVE = os.environ.get("BLENDER_TESTS") == "1" and shutil.which("blender") and shutil.which("ffmpeg")


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(LIVE, "renders with Blender; set BLENDER_TESTS=1")
class Renders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="bv-test-"))
        cls.src = cls.d / "src.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x406080:size=320x180:rate=25:duration=4", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(cls.src)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def blender(self, *args):
        return subprocess.run(["blender", "--background", "--python", str(SCRIPT), "--", *map(str, args)],
                              capture_output=True, text=True, timeout=600)

    def test_cut_keeps_rate_sound_and_colour(self):
        out = self.d / "cut.mp4"
        run = self.blender("--input", self.src, "--cut", "1-3", "--output", out)
        self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
        info = probe(out)
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertIn("audio", kinds, "the cut lost its sound")
        self.assertEqual(kinds["video"]["r_frame_rate"], "25/1")
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.1)
        frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        r, g, b = (sum(frame[i::3]) / (len(frame) / 3) for i in range(3))
        # the first frame is picture, not black, and the colour is not tone mapped
        self.assertAlmostEqual(r, 0x40, delta=5)
        self.assertAlmostEqual(g, 0x60, delta=5)
        self.assertAlmostEqual(b, 0x80, delta=5)

    def test_speed_keeps_the_sound(self):
        out = self.d / "fast.mp4"
        run = self.blender("--input", self.src, "--speed", "2", "--output", out)
        self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
        info = probe(out)
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.1)
        self.assertIn("audio", [s["codec_type"] for s in info["streams"]])

    def test_failure_exits_nonzero(self):
        run = self.blender("--input", self.d / "missing.mp4", "--output", self.d / "x.mp4")
        self.assertEqual(run.returncode, 1)


def frame_seconds(path):
    """The source second each frame shows, read back from a clip whose
    brightness rises 25 levels a second (limited range, so 16 at 0.64 s)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    size = 320 * 180
    return [(sum(raw[i:i + size]) / size / 1.164 + 16) / 25 for i in range(0, len(raw), size)]


@unittest.skipUnless(LIVE, "renders with Blender; set BLENDER_TESTS=1")
class SweepRest20261007(unittest.TestCase):
    """Linux bot sweep 2026-10-07."""

    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="bv-sweep-"))
        cls.timed = cls.d / "timed.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=black:s=320x180:r=25:d=8,format=yuv420p,geq=lum='min(250,25*T)':cb=128:cr=128",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.timed)], check=True)
        cls.banded = cls.d / "banded.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=320x180:r=25:d=2,drawbox=x=0:y=0:w=80:h=180:color=red:t=fill",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.banded)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def blender(self, *args, cwd=None):
        return subprocess.run(["blender", "--background", "--python", str(SCRIPT), "--", *map(str, args)],
                              capture_output=True, text=True, timeout=600, cwd=cwd)

    def test_cut_with_speed_keeps_the_cut(self):
        """the end retiming key could not pass the keys Blender adds at
        a cut's ends, and the clip collapsed to one black frame."""
        for speed, seconds in (("2", 1.0), ("0.5", 4.0)):
            out = self.d / f"cut_x{speed}.mp4"
            run = self.blender("--input", self.timed, "--cut", "2-4", "--speed", speed, "--output", out)
            self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
            info = probe(out)
            self.assertAlmostEqual(float(info["format"]["duration"]), seconds, delta=0.1)
            self.assertIn("audio", [s["codec_type"] for s in info["streams"]])
            shown = frame_seconds(out)
            self.assertAlmostEqual(shown[0], 2.0, delta=0.15)
            self.assertAlmostEqual(shown[-1], 4.0, delta=0.15)

    def test_size_and_joined_clips_scale_to_fit(self):
        """new_movie's default fit is ORIGINAL, so a smaller frame
        showed the middle of the picture; the red left quarter vanished."""
        out = self.d / "small.mp4"
        run = self.blender("--input", self.banded, "--width", "160", "--height", "90", "--output", out)
        self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
        frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        left = frame[(45 * 160 + 10) * 3:(45 * 160 + 10) * 3 + 3]
        self.assertGreater(left[0], 200, f"no red at the left edge: {tuple(left)}")
        self.assertLess(left[2], 60, f"no red at the left edge: {tuple(left)}")

    def test_relative_output_is_written(self):
        """Blender cannot anchor a relative render path with no .blend
        file, so the SKILL's own `--output cut.mp4` failed."""
        run = self.blender("--input", self.banded, "--output", "rel.mp4", cwd=self.d)
        self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
        self.assertGreater((self.d / "rel.mp4").stat().st_size, 0)

    def test_concat_refuses_options_it_would_drop(self):
        """--concat ignored --text, --cut, fades and colour without a word."""
        run = self.blender("--concat", self.banded, self.banded, "--text", "Title",
                           "--output", self.d / "joined.mp4")
        self.assertEqual(run.returncode, 1)
        self.assertIn("--text", run.stdout + run.stderr)
        self.assertFalse((self.d / "joined.mp4").exists())

    def test_frame_cache_is_capped(self):
        """at the default 4096 MB frame cache a 10 second 1080p edit
        peaked at 2.7 GB (60 seconds: 4.9 GB); capped, 0.9 GB."""
        src = self.d / "hd.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=10",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
        peak = subprocess.run(
            [sys.executable, "-c", "import resource, subprocess, sys; "
             "r = subprocess.run(sys.argv[1:], capture_output=True); "
             "print(r.returncode, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)",
             "blender", "--background", "--python", str(SCRIPT), "--", "--input", str(src),
             "--output", str(self.d / "hd_out.mp4")],
            capture_output=True, text=True, timeout=600).stdout.split()
        self.assertEqual(peak[0], "0")
        self.assertLess(int(peak[1]), 1_500_000, f"peak {int(peak[1]) // 1024} MB")


if __name__ == "__main__":
    unittest.main()
