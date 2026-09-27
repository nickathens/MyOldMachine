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

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def _load_video():
    spec = _ilu.spec_from_file_location("video_editing_script", SCRIPT)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _planes(buf, fmt, dtype):
    """Y, U and V of every frame of raw planar video."""
    rows = H // 2 if fmt.startswith("yuv420") else H
    luma, chroma = W * H, (W // 2) * rows
    frames = np.frombuffer(buf, dtype).reshape(-1, luma + 2 * chroma)
    return (frames[:, :luma].reshape(-1, H, W),
            frames[:, luma:luma + chroma].reshape(-1, rows, W // 2),
            frames[:, luma + chroma:].reshape(-1, rows, W // 2))


class TextWithoutDrawtext(unittest.TestCase):
    """drawtext needs libfreetype, and the Mac mini's Homebrew ffmpeg 9.0.2 has
    none: every text command failed there. The text is drawn with Pillow and
    laid on by overlay, forced here so a machine that has drawtext tests the
    same path. overlay's format=auto took the whole frame through RGBA and its
    default cut 10 bits to 8, so the graph is run to raw frames and compared
    bit for bit. Review of #187."""

    @classmethod
    def setUpClass(cls):
        if _ilu.find_spec("PIL") is None:
            raise unittest.SkipTest("needs Pillow")
        cls.d = Path(tempfile.mkdtemp(prefix="vedit-nodt-"))
        cls.video = _load_video()
        cls.sources = {}
        for fmt, codec in (("yuv420p", ["-c:v", "libx264", "-crf", "12"]),
                           ("yuv420p10le", ["-c:v", "libx264", "-crf", "12"]),
                           ("yuv422p10le", ["-c:v", "prores_ks", "-profile:v", "3"])):
            src = cls.d / f"{fmt}.{'mov' if 'prores_ks' in codec else 'mp4'}"
            ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=25:duration=1", "-pix_fmt", fmt, *codec, src)
            cls.sources[fmt] = src

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def args(self, src, out):
        return argparse.Namespace(input=str(src), output=str(out), text="Γειά σου\\nCrème",
                                  position="bottom", fontsize=40, color="white", font=None, outline=True)

    def graph_frames(self, src, fmt):
        """The graph cmd_text builds, run to raw frames instead of an encode."""
        got = []

        def run(args, output, single_file=True):
            args = list(map(str, args))
            inputs = [part for i, a in enumerate(args) if a == "-i" for part in ("-i", args[i + 1])]
            graph = args[args.index("-filter_complex") + 1]
            got.append(subprocess.run(["ffmpeg", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[v]",
                                       "-f", "rawvideo", "-pix_fmt", fmt, "-"], capture_output=True, check=True).stdout)

        with mock.patch.object(self.video, "_ffmpeg_has_filter", return_value=False), \
             mock.patch.object(self.video, "run", side_effect=run):
            self.video.cmd_text(self.args(src, self.d / "unused.mp4"))
        return got[0]

    def test_only_the_text_changes_and_the_format_is_kept(self):
        for fmt, src in self.sources.items():
            with self.subTest(fmt=fmt):
                dtype = np.uint16 if fmt.endswith("10le") else np.uint8
                raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", fmt, "-"],
                                     capture_output=True, check=True).stdout
                before, after = _planes(raw, fmt, dtype), _planes(self.graph_frames(src, fmt), fmt, dtype)
                for name, a, b in zip("YUV", before, after):
                    changed = np.nonzero((a != b).any(axis=(0, 2)))[0]
                    self.assertTrue(len(changed), f"{name}: no text was drawn")
                    self.assertGreaterEqual(changed.min(), a.shape[1] // 2,
                                            f"{name}: rows above the text changed, from row {changed.min()}")

    def test_a_10_bit_clip_comes_out_10_bit(self):
        out = self.d / "deep_text.mp4"
        with mock.patch.object(self.video, "_ffmpeg_has_filter", return_value=False):
            self.video.cmd_text(self.args(self.sources["yuv420p10le"], out))
        self.assertEqual(streams(out)["video"]["pix_fmt"], "yuv420p10le")

    def test_overlay_names_the_sources_format(self):
        cases = {"yuv420p": "yuv420", "nv12": "yuv420", "yuv420p10le": "yuv420p10",
                 "yuv422p10le": "yuv422p10", "yuv444p": "yuv444", "p010le": "yuv420p10"}
        for pix, want in cases.items():
            self.assertEqual(self.video._overlay_format({"pix_fmt": pix}), want, pix)
        self.assertEqual(self.video._overlay_position("(w-text_w)/2"), "(W-w)/2")
        self.assertEqual(self.video._overlay_position("h-text_h-18"), "H-h-18")


class NoRgbPipe(unittest.TestCase):
    def test_the_script_does_not_use_moviepy(self):
        self.assertNotIn("moviepy", SCRIPT.read_text().split('"""', 2)[-1])


if __name__ == "__main__":
    unittest.main()
