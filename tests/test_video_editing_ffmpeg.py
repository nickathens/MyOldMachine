"""video-editing on ffmpeg, Linux bot review 2026-09-27.

The moviepy version piped every frame through RGB: a cut measured 25.95 dB
against its source with luma 1.36 levels dark (ffmpeg at the same quality:
46 dB, no shift), lost the colour tags and wrote MP3 into MP4. It also
failed on odd widths, extracted 8 frames where --fps 2.5 over 4 s means 10,
and re-encoded audio that could be copied. Each test fails on that version.
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

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "video-editing" / "scripts" / "video.py"
W, H = 640, 360


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True)
    return {s["codec_type"]: s for s in json.loads(out.stdout)["streams"]}


def yuv(path, *pre):
    out = subprocess.run(["ffmpeg", "-v", "error", *pre, "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, W * H * 3 // 2).astype(float)


def pcm_md5(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return hashlib.md5(out).hexdigest()


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class VideoEditing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="vedit-"))
        cls.src = cls.d / "src.mp4"
        ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=25:duration=4", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
           "-c:a", "aac", "-shortest", cls.src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def run_video(self, *args):
        r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_a_cut_keeps_the_picture_the_tags_and_aac(self):
        out = self.d / "cut.mp4"
        self.run_video("cut", self.src, out, "--start", "1", "--end", "3")
        src, got = yuv(self.src, "-ss", "1", "-t", "2"), yuv(out)
        n = min(len(src), len(got))
        psnr = np.mean([10 * np.log10(255 ** 2 / ((src[i] - got[i]) ** 2).mean()) for i in range(0, n, 5)])
        shift = np.mean([got[i][:W * H].mean() - src[i][:W * H].mean() for i in range(0, n, 5)])
        self.assertGreater(psnr, 40, "the edit regraded the picture")
        self.assertLess(abs(shift), 0.3)
        s = streams(out)
        self.assertEqual(s["video"].get("color_space"), "bt709")
        self.assertEqual(s["audio"]["codec_name"], "aac")

    def test_an_odd_width_resizes(self):
        out = self.d / "rs.mp4"
        self.run_video("resize", self.src, out, "--width", "427")
        self.assertEqual(streams(out)["video"]["width"] % 2, 0)

    def test_fractional_frame_rate_extracts_the_right_count(self):
        frames = self.d / "frames"
        self.run_video("frames", self.src, frames, "--fps", "2.5")
        self.assertEqual(len(list(frames.glob("frame_*.png"))), 10)

    def test_a_rerun_replaces_its_own_frames_and_nothing_else(self):
        # audit pass 2: the cleanup took every frame_*.png, a user's own included
        frames = self.d / "rerun"
        frames.mkdir()
        (frames / "frame_hero.png").write_bytes(b"not ours")
        (frames / "frame_0099.png").write_bytes(b"the last run's tail")
        r = self.run_video("frames", self.src, frames, "--fps", "1")
        ours = sorted(p.name for p in frames.glob("frame_[0-9]*.png"))
        self.assertTrue((frames / "frame_hero.png").exists(), "deleted a file the command did not write")
        self.assertNotIn("frame_0099.png", ours)
        self.assertEqual(ours, [f"frame_{i:04d}.png" for i in range(len(ours))])
        self.assertIn(f"Extracted {len(ours)} frames", r.stdout)

    def test_audio_extraction_copies_when_it_can(self):
        out = self.d / "a.m4a"
        self.run_video("extract-audio", self.src, out)
        self.assertEqual(pcm_md5(out), pcm_md5(self.src), "the AAC was re-encoded, not copied")

    def test_merge_with_a_silent_clip_keeps_sound_in_step(self):
        silent = self.d / "silent.mp4"
        ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent)
        out = self.d / "merged.mp4"
        self.run_video("merge", self.src, silent, "-o", out)
        s = streams(out)
        self.assertEqual((s["video"]["width"], s["video"]["height"]), (W, H))
        self.assertAlmostEqual(float(s["audio"]["duration"]), float(s["video"]["duration"]), delta=0.05)


    def test_8_bit_nv12_stays_8_bit_and_10_bit_stays_10_bit(self):
        # Audit pass 1: a substring test read "nv12" as 12-bit and wrote 10-bit
        # H.264 (High 10), which browsers, Telegram and iPhones cannot play.
        nv12 = self.d / "nv12.avi"
        ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2", "-pix_fmt", "nv12", "-c:v", "rawvideo", nv12)
        out = self.d / "nv12.mp4"
        self.run_video("cut", nv12, out, "--start", "0", "--end", "1")
        self.assertEqual(streams(out)["video"]["pix_fmt"], "yuv420p")
        deep = self.d / "deep.mp4"
        ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2", "-pix_fmt", "yuv420p10le", "-c:v", "libx264", deep)
        out10 = self.d / "deep_text.mp4"
        self.run_video("text", deep, out10, "--text", "hi")
        self.assertEqual(streams(out10)["video"]["pix_fmt"], "yuv420p10le")


class NoRgbPipe(unittest.TestCase):
    def test_the_script_does_not_use_moviepy(self):
        self.assertNotIn("moviepy", SCRIPT.read_text().split('"""', 2)[-1])


if __name__ == "__main__":
    unittest.main()
