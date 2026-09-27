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


if __name__ == "__main__":
    unittest.main()
