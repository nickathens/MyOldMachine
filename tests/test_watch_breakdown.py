#!/usr/bin/env python3
"""watch: breakdown.py, how a reference video is built (shots, camera, look, music).

Linux bot port 2026-10-18. Every fixture is made here with ffmpeg and PIL, with
a known answer: cuts at known times, a pan and a push in of known direction, a
flash beside a cut into a bright card, click tracks at exact tempos, a phone
video stored on its side, a still shot with motion in a band, a sound track that
starts after the picture and a picture that starts after the sound. Media tests
skip when ffmpeg or the analysis libraries (numpy, OpenCV, PySceneDetect,
librosa) are absent, which is the case on CI; the pure tests (tempo when numpy
is there, time shift, report text, the size of a turned video, the picture's
start and end, the install line, the pin on the verbatim ReelMimic block)
always run.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"


def _load_breakdown():
    """breakdown.py imports its siblings download and frames by their bare names
    (another skill has a download.py too), so it is loaded with both names
    resolving to watch's own files, and sys.modules and sys.path are put back."""
    names = ("download", "frames")
    saved_mods = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("watch_breakdown_under_test", str(SCRIPTS / "breakdown.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved_mods)
        sys.path[:] = saved_path
    return mod


breakdown = _load_breakdown()

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAVE_LIBS = not breakdown.missing_modules()
MEDIA = unittest.skipUnless(HAVE_FFMPEG and HAVE_LIBS, "ffmpeg and numpy/cv2/scenedetect/librosa required")

# sha256 of the verbatim ReelMimic block (between the two rulers). Each function
# in it was checked byte for byte against analyze.py at commit 9fb7a07c on
# 2026-10-18. If this fails, the block was edited in place: pull the function
# again from upstream instead, then update this pin in the same commit.
VERBATIM_SHA256 = "efc55d4d6fa8540725d2bf0ee99430307bacb5eddbb3ce0efc7e802816f019fa"


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def clicks(path: str, bpm: float, seconds: float, stereo: str = "same") -> None:
    """A click every beat at `bpm`. stereo='inverted' puts -x on the right channel."""
    p = 60.0 / bpm
    x = f"0.8*sin(2*PI*1500*t)*exp(-60*mod(t\\,{p}))"
    expr = f"{x}|-({x})" if stereo == "inverted" else f"{x}|{x}"
    ff("-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={seconds}", path)


class Fixtures:
    """Media made once per test run, shared by the media test classes."""
    root: Path | None = None

    @classmethod
    def make(cls) -> Path:
        if cls.root is not None:
            return cls.root
        import numpy as np
        from PIL import Image, ImageFilter
        root = Path(tempfile.mkdtemp(prefix="bdtest-"))
        rng = np.random.default_rng(7)
        # detail at several scales, like a photograph, so optical flow can lock on:
        # noise at 1/128, 1/32 and 1/8 of the size, blown up and summed
        acc = np.zeros((480, 1280, 3), dtype="float32")
        for div, weight in ((128, 0.5), (32, 0.3), (8, 0.2)):
            small = (rng.random((max(2, 480 // div), 1280 // div, 3)) * 255).astype("uint8")
            acc += weight * np.asarray(Image.fromarray(small).resize((1280, 480), Image.BICUBIC), dtype="float32")
        tex = Image.fromarray(acc.clip(0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(1))
        tex.save(root / "tex.png")
        tex.crop((0, 0, 640, 480)).save(root / "tex_small.png")
        # three different shots, 2 s each, cuts at 2 and 4, with a 120 bpm click track:
        # the texture, a light grey card, the texture's other half turned blue and dark
        clicks(str(root / "c120_6.wav"), 120, 6)
        ff("-loop", "1", "-i", str(root / "tex.png"), "-f", "lavfi", "-i", "color=c=0xE0E0E0:s=320x240:r=24:d=2",
           "-i", str(root / "c120_6.wav"), "-filter_complex",
           "[0]split[a][b];[a]crop=640:480:0:0,scale=320:240,fps=24,trim=duration=2,setpts=PTS-STARTPTS[s1];"
           "[b]crop=640:480:640:0,scale=320:240,hue=h=200,eq=brightness=-0.25,fps=24,trim=duration=2,"
           "setpts=PTS-STARTPTS[s3];[s1][1][s3]concat=n=3:v=1:a=0,format=yuv420p[v]",
           "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(root / "cuts.mp4"))
        # three solid colours for the look check
        ff("-f", "lavfi", "-i", "color=c=0xC01010:s=320x240:r=24:d=2",
           "-f", "lavfi", "-i", "color=c=0x10C010:s=320x240:r=24:d=2",
           "-f", "lavfi", "-i", "color=c=0x1010C0:s=320x240:r=24:d=2",
           "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(root / "solids.mp4"))
        # the texture panned: the window slides right, so the content moves left
        ff("-loop", "1", "-i", str(root / "tex.png"), "-vf",
           "crop=480:360:x='t*60':y=40,scale=320:240", "-t", "3", "-r", "24",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "pan.mp4"))
        # the texture zoomed into: a push in
        ff("-loop", "1", "-i", str(root / "tex_small.png"), "-vf",
           "zoompan=z='1+0.006*on':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=320x240:fps=24",
           "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "zoom.mp4"))
        # the texture held still: one shot, no move
        ff("-loop", "1", "-i", str(root / "tex_small.png"), "-vf", "scale=320:240", "-t", "3", "-r", "24",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "still.mp4"))
        # brightness stories for the flash finder, all 24 fps
        dark = "color=c=0x282828:s=320x240:r=24"
        white = "color=c=white:s=320x240:r=24"
        ff("-f", "lavfi", "-i", f"{dark}:d=2", "-f", "lavfi", "-i", f"{white}:d=0.1667",
           "-f", "lavfi", "-i", f"{dark}:d=2", "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0",
           "-pix_fmt", "yuv420p", str(root / "flash.mp4"))
        for name, level in (("flash_grey", "0xD0D0D0"), ("flash_dim", "0xA8A8A8")):
            ff("-f", "lavfi", "-i", f"{dark}:d=2", "-f", "lavfi", "-i", f"color=c={level}:s=320x240:r=24:d=0.1667",
               "-f", "lavfi", "-i", f"{dark}:d=2", "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0",
               "-pix_fmt", "yuv420p", str(root / f"{name}.mp4"))
        ff("-f", "lavfi", "-i", f"{dark}:d=2", "-f", "lavfi", "-i", f"{white}:d=2",
           "-filter_complex", "[0][1]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(root / "step_up.mp4"))
        # under a second: too short for the 64 frame sheet, which then is not drawn
        ff("-loop", "1", "-i", str(root / "tex_small.png"), "-vf", "scale=320:240", "-t", "0.8", "-r", "24",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "short.mp4"))
        ff("-f", "lavfi", "-i", f"{white}:d=2", "-f", "lavfi", "-i", f"{dark}:d=2",
           "-filter_complex", "[0][1]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", str(root / "step_down.mp4"))
        # a letterboxed picture: texture 960x316 inside a 960x540 black frame, so the
        # analysis copy is smaller than the source (640 wide) and the crop has to be
        # mapped back to source pixels
        ff("-loop", "1", "-i", str(root / "tex.png"), "-vf",
           "crop=1000:330:x='t*60':y=100,scale=960:316,pad=960:540:0:112:black", "-t", "3", "-r", "24",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "letterbox.mp4"))
        # a phone screen recording: a moving picture strip between two busy, still bands
        ff("-loop", "1", "-i", str(root / "tex.png"), "-loop", "1", "-i", str(root / "tex_small.png"),
           "-filter_complex",
           "[0]crop=640:360:x='t*60':y=60,scale=720:406[mid];"
           "[1]scale=720:1280[ui];[ui][mid]overlay=0:437,format=yuv420p[v]",
           "-map", "[v]", "-t", "3", "-r", "24", "-c:v", "libx264", str(root / "phone.mp4"))
        # an odd width and height (yuv444p allows it): the analysis copy must round
        # to even sizes or libx264 refuses it
        ff("-loop", "1", "-i", str(root / "tex_small.png"), "-vf", "scale=321:241", "-t", "2", "-r", "24",
           "-c:v", "libx264", "-pix_fmt", "yuv444p", str(root / "odd.mp4"))
        # a phone video shot upright: stored as a 640x360 picture plus a 90 degree
        # display rotation, so it plays as 360x640
        ff("-loop", "1", "-i", str(root / "tex.png"), "-vf", "crop=640:360:x='t*60':y=40", "-t", "2", "-r", "24",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "upright_stored.mp4"))
        ff("-display_rotation", "90", "-i", str(root / "upright_stored.mp4"), "-c", "copy", str(root / "upright.mp4"))
        # one still landscape shot with all its motion in a band across the middle,
        # like a locked off wide shot of traffic: not a phone screen recording;
        # and the same in a square frame
        ff("-f", "lavfi", "-i", "color=c=0x406080:s=640x360:r=24:d=3", "-loop", "1", "-i", str(root / "tex.png"),
           "-filter_complex", "[1]crop=640:120:x='t*80':y=60[b];[0][b]overlay=0:120,format=yuv420p",
           "-t", "3", "-c:v", "libx264", str(root / "band.mp4"))
        ff("-f", "lavfi", "-i", "color=c=0x406080:s=360x360:r=24:d=3", "-loop", "1", "-i", str(root / "tex.png"),
           "-filter_complex", "[1]crop=360:120:x='t*80':y=60[b];[0][b]overlay=0:120,format=yuv420p",
           "-t", "3", "-c:v", "libx264", str(root / "band_square.mp4"))
        # a tone at 1.2 times full scale, stored as float: the sound cut must keep it
        # (a 16 bit copy of it peaks at exactly 1.0)
        ff("-f", "lavfi", "-i", "aevalsrc='1.2*sin(2*PI*220*t)':s=44100:d=2",
           "-c:a", "pcm_f32le", str(root / "hot.wav"))
        # the sound starts 0.75 s after the picture, so its clicks fall on 0.75, 1.25,
        # 1.75 ... of the source; 0.75 is not a whole number of beats, so a track
        # pulled back to 0 lands its beats between the clicks
        ff("-f", "lavfi", "-i", "color=c=0x303030:s=320x240:r=24:d=7", "-itsoffset", "0.75",
           "-i", str(root / "c120_6.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", str(root / "late_sound.mp4"))
        # the picture starts 0.25 s after 120 bpm clicks that run 7 s from 0: a red
        # card then a blue one, cut at 3.0 of the picture and so at 3.25 of the
        # source, half a beat off the clicks; the picture ends at 6.25, the sound at 7
        clicks(str(root / "c120_7.wav"), 120, 7)
        ff("-f", "lavfi", "-i", "color=c=0xC01010:s=320x240:r=24:d=3",
           "-f", "lavfi", "-i", "color=c=0x1010C0:s=320x240:r=24:d=3",
           "-filter_complex", "[0][1]concat=n=2:v=1:a=0,format=yuv420p", "-c:v", "libx264",
           str(root / "cards.mp4"))
        ff("-itsoffset", "0.25", "-i", str(root / "cards.mp4"), "-i", str(root / "c120_7.wav"),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(root / "late_picture.mp4"))
        # the flash story with its picture 0.25 s after a silent sound track
        ff("-itsoffset", "0.25", "-i", str(root / "flash.mp4"), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-t", "4.5", str(root / "late_flash.mp4"))
        # sound only fixtures
        clicks(str(root / "c126.wav"), 126, 12)
        clicks(str(root / "c100.wav"), 100, 12)
        clicks(str(root / "c120_inv.wav"), 120, 12, stereo="inverted")
        ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "6", str(root / "silence.wav"))
        ff("-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-ac", "2", str(root / "tone.wav"))
        cls.root = root
        return root

    @classmethod
    def cleanup(cls) -> None:
        if cls.root is not None:
            shutil.rmtree(cls.root, ignore_errors=True)
            cls.root = None


def tearDownModule():
    Fixtures.cleanup()


def _verbatim_block() -> str:
    text = (SCRIPTS / "breakdown.py").read_text(encoding="utf-8")
    start = text.index("# Verbatim from ReelMimic")
    start = text.index("\n", text.index("# ====", start)) + 1
    end = text.index("# ===========================================================================\n# Local adapter")
    return text[start:end]


class TestVerbatimPin(unittest.TestCase):
    def test_block_is_unedited(self):
        got = hashlib.sha256(_verbatim_block().encode("utf-8")).hexdigest()
        self.assertEqual(got, VERBATIM_SHA256,
                         "the verbatim ReelMimic block changed: re-pull from upstream, do not edit in place")

    def test_block_holds_exactly_the_borrowed_functions(self):
        import ast
        names = [n.name for n in ast.parse(_verbatim_block()).body if isinstance(n, ast.FunctionDef)]
        self.assertEqual(names, ["run", "probe", "detect_crop", "ui_strip_crop", "shots", "sheets",
                                 "_shot_details_part", "audio", "peak_candidates"])


@unittest.skipUnless(HAVE_NUMPY, "numpy required")
class TestRefineTempo(unittest.TestCase):
    HOP = 512 / 22050

    def _beats(self, bpm: float, n: int = 40) -> list[float]:
        # beat times as the tracker gives them: snapped to whole 512 sample hops
        period = 60.0 / bpm
        return [round(round(k * period / self.HOP) * self.HOP, 3) for k in range(n)]

    def test_off_grid_tempo_comes_back_exact(self):
        # 126 bpm is 20.5 hops a beat: the median interval reads 129.2 or 123.0
        self.assertAlmostEqual(breakdown.refine_tempo(self._beats(126)), 126.0, delta=0.3)
        self.assertAlmostEqual(breakdown.refine_tempo(self._beats(87)), 87.0, delta=0.3)

    def test_a_skipped_beat_does_not_drag_the_tempo(self):
        beats = self._beats(126)
        del beats[20]  # one interval doubles
        self.assertAlmostEqual(breakdown.refine_tempo(beats), 126.0, delta=0.3)

    def test_too_few_beats_give_no_tempo(self):
        self.assertIsNone(breakdown.refine_tempo(self._beats(126, n=7)))
        self.assertIsNone(breakdown.refine_tempo([]))


class TestShiftTimes(unittest.TestCase):
    def _rep(self):
        return {"shots": [[0.0, 2.0], [2.0, 3.5]],
                "shot_details": [{"shot": 1, "start": 0.0, "end": 2.0}, {"shot": 2, "start": 2.0, "end": 3.5}],
                "flashes": [1.5],
                "audio": {"first_beat": 0.25, "beats": [0.25, 0.75], "beat_seconds": 0.5, "bpm": 120.0,
                          "best_30s_starts": [{"start": 0.25, "density": 1.0}],
                          "peak_candidates": [{"t": 2.0, "rise": 1.0, "level_after": 1.0, "kind": "lift"}]}}

    def test_every_time_moves_and_nothing_else_does(self):
        rep = self._rep()
        breakdown.shift_times(rep, 100.0)
        self.assertEqual(rep["shots"], [[100.0, 102.0], [102.0, 103.5]])
        self.assertEqual([(d["start"], d["end"]) for d in rep["shot_details"]], [(100.0, 102.0), (102.0, 103.5)])
        self.assertEqual(rep["flashes"], [101.5])
        au = rep["audio"]
        self.assertEqual(au["first_beat"], 100.25)
        self.assertEqual(au["beats"], [100.25, 100.75])
        self.assertEqual(au["best_30s_starts"][0]["start"], 100.25)
        self.assertEqual(au["peak_candidates"][0]["t"], 102.0)
        self.assertEqual((au["beat_seconds"], au["bpm"]), (0.5, 120.0))   # durations stay put
        self.assertEqual(au["peak_candidates"][0]["rise"], 1.0)

    def test_zero_offset_and_missing_music_fields(self):
        rep = self._rep()
        breakdown.shift_times(rep, 0.0)
        self.assertEqual(rep, self._rep())
        rep = self._rep()
        rep["audio"] = {"present": False}
        breakdown.shift_times(rep, 5.0)   # no music keys: must not raise
        self.assertEqual(rep["flashes"], [6.5])

    def test_picture_times_move_by_the_lead_too(self):
        # a picture that starts after the sound: the shots, details and flashes
        # count from its first frame, the music from the range start
        rep = self._rep()
        breakdown.shift_times(rep, 100.0, 0.25)
        self.assertEqual(rep["shots"], [[100.25, 102.25], [102.25, 103.75]])
        self.assertEqual([(d["start"], d["end"]) for d in rep["shot_details"]], [(100.25, 102.25), (102.25, 103.75)])
        self.assertEqual(rep["flashes"], [101.75])
        self.assertEqual(rep["audio"]["beats"], [100.25, 100.75])
        self.assertEqual(rep["audio"]["peak_candidates"][0]["t"], 102.0)
        # the whole file: no range start, and the picture still moves
        rep = self._rep()
        breakdown.shift_times(rep, 0.0, 0.25)
        self.assertEqual(rep["shots"], [[0.25, 2.25], [2.25, 3.75]])
        self.assertEqual(rep["flashes"], [1.75])
        self.assertEqual(rep["audio"], self._rep()["audio"])


class TestTimeFormat(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(breakdown.ts(0), "0:00.00")
        self.assertEqual(breakdown.ts(59.996), "1:00.00")
        self.assertEqual(breakdown.ts(83.456), "1:23.46")
        self.assertEqual(breakdown.ts(3725.5), "1:02:05.50")
        self.assertEqual(breakdown.ts(-1), "0:00.00")


class TestRender(unittest.TestCase):
    def _rep(self, **audio):
        details = [{"shot": 1, "start": 0.0, "end": 2.0,
                    "camera": {"kind": ["push_in", "move_left"], "pace": "slow"},
                    "look": {"brightness": 0.5, "contrast": 0.2, "dark_ratio": 0.1,
                             "dominant": ["#111111", "#222222", "#333333", "#444444"]}}]
        au = {"present": True, "silent": False, "bpm": 123.05, "tempo_bpm": 126.0,
              "beats": [0.5 + i * 0.476 for i in range(12)], "first_beat": 0.5,
              "peak_candidates": [{"t": 3.0, "kind": "drop"}], "phase_inverted": False}
        au.update(audio)
        return {"source": "clip.mp4", "video": {"duration": 6.0, "width": 1920, "height": 1080, "fps": 24.0},
                "range": None, "crop": None, "shots": [[0.0, 2.0], [2.0, 6.0]],
                "pacing": {"count": 2, "mean_shot_s": 3.0, "shortest_s": 2.0, "longest_s": 4.0,
                           "mean_shot_beats": 6.3},
                "audio": au, "shot_details": details, "flashes": [],
                "look_summary": {"low_key_shots": 0.0, "moving_camera_shots": 1.0, "mean_brightness": 0.5,
                                 "camera_kinds": ["move_left", "push_in"]}}

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="bdrender-"))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_table_has_one_row_per_shot_and_beats(self):
        md = breakdown.render(self._rep(), self.work)
        rows = [line for line in md.splitlines() if line.startswith("| ") and not line.startswith("| #")]
        self.assertEqual(len(rows), 2)
        self.assertIn("| 1 | 0:00.00 | 2.00s | 4.2 | push in, move left | slow |", rows[0])
        self.assertIn("not read", rows[1])   # shot 2 has no details
        self.assertIn("126 bpm, from 12 beats between 0:00.50 and 0:05.74", md)
        self.assertIn("0:03.00 drop", md)

    def test_music_states(self):
        cases = {
            "none (no sound track)": {"present": False},
            "silent": {"silent": True},
            "no beat found": {"no_beat": True},
            "could not be measured (RuntimeError: boom)": {"error": "RuntimeError: boom"},
            "no reliable tempo: the beat tracker found only 6 beats": {"tempo_bpm": None,
                                                                        "beats": [0.1, 0.8, 1.5, 2.2, 2.9, 3.6]},
        }
        for want, audio in cases.items():
            with self.subTest(state=want):
                self.assertIn(want, breakdown.render(self._rep(**audio), self.work))

    def test_no_tempo_no_beats_column(self):
        rep = self._rep(tempo_bpm=None, beats=[0.1, 0.8])
        rep["pacing"].pop("mean_shot_beats")
        md = breakdown.render(rep, self.work)
        header = next(line for line in md.splitlines() if line.startswith("| # |"))
        self.assertNotIn("beats", header)
        self.assertNotIn("beats a shot", md)

    def test_counts_are_counted_not_rounded_back(self):
        # 73 moving shots of 150: a share rounded to 0.49 and multiplied back gives 74
        base = self._rep()["shot_details"][0]
        det = []
        for i in range(150):
            d = json.loads(json.dumps(base))
            d["shot"] = i + 1
            d["camera"]["kind"] = ["push_in"] if i < 73 else ["static"]
            d["look"]["dark_ratio"] = 0.9 if i < 37 else 0.1
            det.append(d)
        rep = self._rep()
        rep["shot_details"] = det
        rep["shots"] = [[i * 1.0, i + 1.0] for i in range(150)]
        rep["pacing"]["count"] = 150
        rep["look_summary"].update(moving_camera_shots=round(73 / 150, 2), low_key_shots=round(37 / 150, 2))
        md = breakdown.render(rep, self.work)
        self.assertIn("moving in 73 of 150 shots", md)
        self.assertIn("in 37 of 150 shots", md)

    def test_phase_note_range_and_crop(self):
        rep = self._rep(phase_inverted=True)
        rep["range"] = {"start": 60.0, "end": 66.0}
        rep["crop"] = {"w": 640, "h": 268, "x": 0, "y": 46, "note": "letterbox"}
        rep["crop_source_px"] = {"w": 1920, "h": 804, "x": 0, "y": 138}
        md = breakdown.render(rep, self.work)
        self.assertIn("the stereo channels cancel", md)
        self.assertIn("**Range:** 1:00.00 to 1:06.00 (6.0s)", md)
        self.assertIn("1920x804 at 0,138", md)

    def test_sheets_listed_only_when_present(self):
        md = breakdown.render(self._rep(), self.work)
        self.assertNotIn("sheet_1fps.jpg", md)
        (self.work / "sheet_1fps.jpg").write_bytes(b"x")
        md = breakdown.render(self._rep(), self.work)
        self.assertIn("sheet_1fps.jpg", md)
        self.assertNotIn("sheet_scenes.jpg", md)

    def test_sheet_labels_say_where_they_count_from(self):
        # the labels count from the analysis copy's first frame; on a picture
        # that starts 0.25 s after the sound the line called them source times
        (self.work / "sheet_scenes.jpg").write_bytes(b"x")

        def sheet_line(rep):
            return next(line for line in breakdown.render(rep, self.work).splitlines() if "sheet_scenes" in line)
        rep = self._rep()
        rep["analysed"] = {"starts_at": 0.25}
        self.assertIn("labelled with its time in seconds, counted from 0:00.25 in the source", sheet_line(rep))
        rep["analysed"] = {"starts_at": 0.0}
        self.assertTrue(sheet_line(rep).endswith("labelled with its time in seconds"), sheet_line(rep))
        rep = self._rep()
        rep["range"] = {"start": 60.0, "end": 66.0}
        self.assertIn("counted from 1:00.00 in the source", sheet_line(rep))

    def test_no_dashes_or_arrows_in_the_adapter_prose(self):
        # the house writing rule: no em or en dashes and no arrows in report prose
        md = breakdown.render(self._rep(), self.work)
        for ch in ("—", "–", "→"):
            self.assertNotIn(ch, md)


@MEDIA
class TestPicture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Fixtures.make()

    def _details(self, name: str, spans):
        import cv2
        cv2.setRNGSeed(0)
        return breakdown._shot_details_part(str(self.root / name), spans, 0)

    def test_shots_found_at_the_cuts(self):
        cuts = breakdown.shots(str(self.root / "cuts.mp4"))
        self.assertEqual(len(cuts), 3)
        for (a, b), (wa, wb) in zip(cuts, [(0, 2), (2, 4), (4, 6)]):
            self.assertAlmostEqual(a, wa, delta=0.05)
            self.assertAlmostEqual(b, wb, delta=0.05)

    def test_pan_reads_as_move_right(self):
        d = self._details("pan.mp4", [[0.0, 3.0]])[0]
        self.assertIn("move_right", d["camera"]["kind"])
        self.assertNotIn("move_left", d["camera"]["kind"])

    def test_zoom_reads_as_push_in(self):
        d = self._details("zoom.mp4", [[0.0, 3.0]])[0]
        self.assertIn("push_in", d["camera"]["kind"])

    def test_still_reads_as_static(self):
        d = self._details("still.mp4", [[0.0, 3.0]])[0]
        self.assertEqual(d["camera"]["kind"], ["static"])

    def test_look_of_solid_shots(self):
        det = self._details("solids.mp4", [[0.0, 2.0], [2.0, 4.0], [4.0, 6.0]])
        firsts = [d["look"]["dominant"][0] for d in det]
        r, g, b = (tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in firsts)
        self.assertGreater(r[0], 150)
        self.assertGreater(g[1], 150)
        self.assertGreater(b[2], 150)

    def test_flash_found_between_dark_frames(self):
        got = breakdown.flash_frames(str(self.root / "flash.mp4"))
        self.assertEqual(len(got), 1, got)
        self.assertAlmostEqual(got[0], 2.0, delta=0.25)

    def test_the_brightness_bar(self):
        # 0xD0 (0.82) clears ReelMimic's 0.75 bar, 0xA8 (0.66) does not
        self.assertEqual(len(breakdown.flash_frames(str(self.root / "flash_grey.mp4"))), 1)
        self.assertEqual(breakdown.flash_frames(str(self.root / "flash_dim.mp4")), [])

    def test_a_cut_into_or_out_of_a_bright_shot_is_not_a_flash(self):
        # ReelMimic's flashes() reported both of these (its only two hits on a 54 s ad)
        self.assertEqual(breakdown.flash_frames(str(self.root / "step_up.mp4")), [])
        self.assertEqual(breakdown.flash_frames(str(self.root / "step_down.mp4")), [])


@MEDIA
class TestMusic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Fixtures.make()

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="bdmusic-")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def _music(self, name: str, **kw):
        path = str(self.root / name)
        return breakdown.music(path, breakdown.probe(path), self.out, **kw)

    def test_click_tempos_come_back_exact(self):
        for name, bpm in (("c126.wav", 126.0), ("c100.wav", 100.0)):
            with self.subTest(bpm=bpm):
                au = self._music(name)
                self.assertAlmostEqual(au["tempo_bpm"], bpm, delta=0.5)

    def test_phase_inverted_stereo_still_measured(self):
        au = self._music("c120_inv.wav")
        self.assertTrue(au["phase_inverted"])
        self.assertAlmostEqual(au["tempo_bpm"], 120.0, delta=0.5)

    def test_silence_tone_and_no_track(self):
        self.assertTrue(self._music("silence.wav").get("silent"))
        self.assertTrue(self._music("tone.wav").get("no_beat"))   # audio() divides by zero here
        self.assertEqual(self._music("still.mp4"), {"present": False})

    def test_the_sound_cut_keeps_samples_past_full_scale(self):
        # an AAC decode runs past full scale; a 16 bit cut clipped it and moved
        # the build and hit scores on real ads
        import numpy as np
        dst = os.path.join(self.out, "cut.wav")
        breakdown.cut_audio(str(self.root / "hot.wav"), dst, None, None)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", dst, "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        self.assertGreater(float(np.abs(np.frombuffer(raw, "<f4")).max()), 1.1)

    def test_other_failures_are_reported_not_raised(self):
        with mock.patch.object(breakdown, "audio", side_effect=RuntimeError("decoder fell over")):
            au = self._music("c126.wav")
        self.assertEqual(au["error"], "RuntimeError: decoder fell over")

    def test_range_cut_measures_only_the_range_and_cleans_up(self):
        au = self._music("c126.wav", start=4.0, end=10.0)
        self.assertLessEqual(max(au["beats"]), 6.1)      # range time, 0 to 6
        self.assertFalse(os.path.exists(os.path.join(self.out, "sound.wav")))
        self._music("c126.wav")                           # a whole file is cut to the clock too
        self.assertFalse(os.path.exists(os.path.join(self.out, "sound.wav")))


@MEDIA
class TestAnalyse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Fixtures.make()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="bdwork-"))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_end_to_end_on_three_shots(self):
        rep = breakdown.analyse(str(self.root / "cuts.mp4"), self.work)
        self.assertEqual(rep["pacing"]["count"], 3)
        self.assertAlmostEqual(rep["audio"]["tempo_bpm"], 120.0, delta=1.0)
        self.assertAlmostEqual(rep["pacing"]["mean_shot_beats"], 4.0, delta=0.15)   # 2 s at 120 bpm
        self.assertTrue((self.work / "sheet_1fps.jpg").exists())
        self.assertTrue((self.work / "sheet_scenes.jpg").exists())
        self.assertEqual(json.loads((self.work / "report.json").read_text())["pacing"]["count"], 3)
        md = breakdown.render(rep, self.work)
        rows = [line for line in md.splitlines() if line.startswith("| ") and not line.startswith("| #")]
        self.assertEqual(len(rows), 3)

    def test_range_times_are_source_times(self):
        rep = breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=3.0, end=6.0)
        self.assertEqual(rep["range"], {"start": 3.0, "end": 6.0})
        self.assertEqual(len(rep["shots"]), 2)
        self.assertAlmostEqual(rep["shots"][0][0], 3.0, delta=0.05)
        self.assertAlmostEqual(rep["shots"][1][0], 4.0, delta=0.05)
        self.assertAlmostEqual(rep["shot_details"][1]["start"], 4.0, delta=0.05)
        self.assertGreaterEqual(min(rep["audio"]["beats"]), 3.0)
        # shot 1 began at 2.0, before the range: marked, and out of the pacing
        self.assertEqual(rep["partial_shots"], [1])
        self.assertAlmostEqual(rep["pacing"]["shortest_s"], 2.0, delta=0.05)
        self.assertIn("cut by the range", breakdown.render(rep, self.work))
        # a range ending inside the last shot cuts it too
        mid = breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=1.0, end=5.0)
        self.assertEqual(mid["partial_shots"], [1, 3])
        self.assertAlmostEqual(mid["pacing"]["mean_shot_s"], 2.0, delta=0.05)
        self.assertIn("Shots 1 and 3 run past the range", mid["pacing"]["note"])
        # two shots, both cut: nothing whole is left, and the note must not claim otherwise
        both = breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=1.0, end=3.0)
        self.assertEqual(both["partial_shots"], [1, 2])
        self.assertIn("Every shot runs past the range", both["pacing"]["note"])
        past = breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=3.0, end=99.0)
        self.assertAlmostEqual(past["range"]["end"], 6.0, delta=0.1)

    def test_no_cut_is_one_shot(self):
        rep = breakdown.analyse(str(self.root / "still.mp4"), self.work)
        self.assertEqual(rep["pacing"]["count"], 1)
        self.assertIn("the whole stretch is one shot", rep["pacing"]["note"])
        self.assertAlmostEqual(rep["shots"][0][1], 3.0, delta=0.1)

    def test_letterbox_is_cropped_away(self):
        rep = breakdown.analyse(str(self.root / "letterbox.mp4"), self.work)
        self.assertIsNotNone(rep["crop"])
        self.assertAlmostEqual(rep["crop_source_px"]["h"], 316, delta=8)
        self.assertAlmostEqual(rep["crop_source_px"]["y"], 112, delta=8)
        self.assertLessEqual(rep["analysed"]["width"], breakdown.PROXY_WIDTH)
        self.assertAlmostEqual(rep["analysed"]["height"] / rep["analysed"]["width"], 316 / 960, delta=0.03)

    def test_phone_recording_strip_is_found(self):
        rep = breakdown.analyse(str(self.root / "phone.mp4"), self.work)
        self.assertIsNotNone(rep["crop"], "the moving strip between the still bands was not found")
        self.assertAlmostEqual(rep["crop_source_px"]["y"], 437, delta=24)
        self.assertAlmostEqual(rep["crop_source_px"]["h"], 406, delta=24)

    def test_an_old_sheet_never_survives(self):
        (self.work / breakdown.MARKER).touch()          # the folder of an earlier run
        (self.work / "sheet_1fps.jpg").write_bytes(b"an earlier run's sheet")
        # a run killed while measuring the music leaves its sound cut behind; this
        # file has no sound track, so only the clearing can remove it
        (self.work / "sound.wav").write_bytes(b"an earlier run's sound")
        rep = breakdown.analyse(str(self.root / "short.mp4"), self.work)
        self.assertFalse((self.work / "sheet_1fps.jpg").exists())
        self.assertFalse((self.work / "sound.wav").exists())
        self.assertNotIn("sheet_1fps.jpg", breakdown.render(rep, self.work))

    def test_a_folder_with_other_files_is_refused_untouched(self):
        (self.work / "audio.wav").write_bytes(b"the client's mix")
        with self.assertRaises(SystemExit) as cm:
            breakdown.analyse(str(self.root / "still.mp4"), self.work)
        self.assertIn("give --out-dir an empty or new folder", str(cm.exception))
        self.assertEqual((self.work / "audio.wav").read_bytes(), b"the client's mix")
        with self.assertRaises(SystemExit):
            breakdown.main([str(self.root / "still.mp4"), "--out-dir", str(self.work)])
        self.assertEqual((self.work / "audio.wav").read_bytes(), b"the client's mix")

    def test_url_run_in_a_fresh_folder(self):
        # a link downloads into the folder before analyse() claims it, so main()
        # must claim it first; the download is faked here, no network
        def fake_download(source, out_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.root / "still.mp4", out_dir / "video.mp4")
            return {"video_path": str(out_dir / "video.mp4"), "info": {"title": "x"}}
        fresh = self.work / "fresh"
        with mock.patch.object(breakdown, "download", side_effect=fake_download), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(breakdown.main(["https://example.com/v", "--out-dir", str(fresh)]), 0)
        self.assertIn("# watch: breakdown", out.getvalue())
        self.assertTrue((fresh / "report.json").exists())

    def test_odd_sized_source(self):
        rep = breakdown.analyse(str(self.root / "odd.mp4"), self.work)
        self.assertEqual(rep["analysed"]["width"] % 2, 0)
        self.assertEqual(rep["pacing"]["count"], 1)

    def test_an_upright_phone_video_reads_upright(self):
        # the report gave the stored 640x360, not the 360x640 that plays
        rep = breakdown.analyse(str(self.root / "upright.mp4"), self.work)
        self.assertEqual((rep["video"]["width"], rep["video"]["height"]), (360, 640))
        self.assertLess(rep["analysed"]["width"], rep["analysed"]["height"])
        self.assertIn("360x640", breakdown.render(rep, self.work))

    def test_a_still_landscape_shot_with_a_moving_band_is_not_cropped(self):
        # read as a phone screen recording, it was measured inside the band only
        for name in ("band.mp4", "band_square.mp4"):
            with self.subTest(name=name):
                self.assertIsNone(breakdown.analyse(str(self.root / name), self.work)["crop"])

    def test_a_late_sound_track_keeps_its_place(self):
        # the clicks fall on 0.75 + k * 0.5 of the source; a track pulled back to
        # 0 put every beat 0.75 s early, between the clicks
        def on_the_clicks(beats):
            self.assertGreaterEqual(min(beats), 0.7, beats)
            for b in beats:
                self.assertLess(abs((b - 0.5) % 0.5 - 0.25), 0.08, beats)
        on_the_clicks(breakdown.analyse(str(self.root / "late_sound.mp4"), self.work)["audio"]["beats"])
        # a range that starts before the sound does
        on_the_clicks(breakdown.analyse(str(self.root / "late_sound.mp4"), self.work,
                                        start=0.25, end=6.5)["audio"]["beats"])

    def test_a_late_picture_keeps_its_place(self):
        # the cut is at 3.25 of the source, half a beat off clicks on 0, 0.5, 1.0 ...
        # OpenCV and PySceneDetect count from the first frame, so it read 3.00, on
        # a beat. A 0.5 s gap at 120 bpm is a whole beat, which hid the slip.
        def cut(rep):
            self.assertEqual(len(rep["shots"]), 2, rep["shots"])
            self.assertAlmostEqual(rep["shot_details"][1]["start"], rep["shots"][1][0], delta=0.01)
            return rep["shots"][1][0]
        whole = breakdown.analyse(str(self.root / "late_picture.mp4"), self.work)
        self.assertAlmostEqual(whole["shots"][0][0], 0.25, delta=0.03)
        self.assertAlmostEqual(cut(whole), 3.25, delta=0.05)
        for b in whole["audio"]["beats"]:
            self.assertLess(abs((b + 0.25) % 0.5 - 0.25), 0.08, whole["audio"]["beats"])
        self.assertIn("counted from 0:00.25 in the source", breakdown.render(whole, self.work))
        # a range that starts before the first frame cuts no shot there; its end
        # at 6.0 cuts the blue card, which runs to 6.25
        early = breakdown.analyse(str(self.root / "late_picture.mp4"), self.work, start=0.1, end=6.0)
        self.assertAlmostEqual(cut(early), 3.25, delta=0.05)
        self.assertEqual(early["partial_shots"], [2])
        # and the analysis stops at the range end, not 0.15 s past it
        self.assertAlmostEqual(early["shots"][1][1], 6.0, delta=0.03)
        # a range that ends in the sound after the picture has ended cuts no shot
        # there; its start at 1.0 cuts the red card, which began at 0.25
        late = breakdown.analyse(str(self.root / "late_picture.mp4"), self.work, start=1.0, end=6.75)
        self.assertAlmostEqual(cut(late), 3.25, delta=0.05)
        self.assertEqual(late["partial_shots"], [1])

    def test_a_late_picture_moves_its_flash_too(self):
        rep = breakdown.analyse(str(self.root / "late_flash.mp4"), self.work)
        self.assertEqual(len(rep["flashes"]), 1, rep["flashes"])
        self.assertAlmostEqual(rep["flashes"][0], 2.25, delta=0.12)

    def test_same_input_same_answer(self):
        a = breakdown.analyse(str(self.root / "pan.mp4"), self.work)["shot_details"]
        b = breakdown.analyse(str(self.root / "pan.mp4"), self.work)["shot_details"]
        self.assertEqual(a, b)


@MEDIA
class TestGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Fixtures.make()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="bdguard-"))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_too_long_asks_for_a_range(self):
        with mock.patch.object(breakdown, "MAX_SECONDS", 2):
            with self.assertRaises(SystemExit) as cm:
                breakdown.analyse(str(self.root / "cuts.mp4"), self.work)
        self.assertIn("--start and --end", str(cm.exception))

    def test_a_range_inside_the_limit_is_allowed(self):
        with mock.patch.object(breakdown, "MAX_SECONDS", 2):
            rep = breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=1.0, end=3.0)
        self.assertEqual(rep["pacing"]["count"], 2)

    def test_start_past_the_end(self):
        with self.assertRaises(SystemExit):
            breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=60.0)

    def test_empty_range_is_refused_before_any_work(self):
        with self.assertRaises(SystemExit) as cm:
            breakdown.analyse(str(self.root / "cuts.mp4"), self.work, start=4.0, end=3.0)
        self.assertIn("the range is empty", str(cm.exception))

    def test_sound_only_file_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            breakdown.analyse(str(self.root / "c126.wav"), self.work)
        self.assertIn("no picture", str(cm.exception))

    def test_cli_guards(self):
        with self.assertRaises(SystemExit) as cm:
            breakdown.main([str(self.root / "cuts.mp4"), "--start", "5", "--end", "4", "--out-dir", str(self.work)])
        self.assertIn("--end must be greater", str(cm.exception))
        with mock.patch.object(breakdown, "NEEDS", ("numpy", "no_such_module_xyz")):
            with self.assertRaises(SystemExit) as cm:
                breakdown.main([str(self.root / "cuts.mp4"), "--out-dir", str(self.work)])
        self.assertIn("no_such_module_xyz", str(cm.exception))


class TestReadProbe(unittest.TestCase):
    def test_a_quarter_turn_either_way_swaps_the_size(self):
        stored = {"duration": 3.0, "width": 640, "height": 360, "fps": 30.0, "has_audio": False, "channels": None}
        for rot, want in ((90, (360, 640)), (-90, (360, 640)), (270, (360, 640)), (-270, (360, 640)),
                          (0, (640, 360)), (180, (640, 360)), (-180, (640, 360))):
            with self.subTest(rotation=rot), \
                    mock.patch.object(breakdown, "probe", return_value=dict(stored)), \
                    mock.patch.object(breakdown, "display_rotation", return_value=rot) as rotation:
                info = breakdown.read_probe("clip.mp4")
                self.assertEqual((info["width"], info["height"]), want)
                rotation.assert_called_once_with("clip.mp4")


class TestPictureSpan(unittest.TestCase):
    def _span(self, stdout: str, dur: float = 7.0):
        with mock.patch.object(breakdown, "run", return_value=mock.Mock(stdout=stdout)):
            return breakdown.picture_span("clip.mp4", dur)

    def test_start_and_end_on_the_files_clock(self):
        def probe_json(stream, fmt):
            return json.dumps({"streams": [stream], "format": fmt})
        # the picture 0.25 s after the sound, 6 s long, in a 7 s file
        self.assertEqual(self._span(probe_json({"start_time": "0.250000", "duration": "6.000000"},
                                               {"start_time": "0.000000"})), (0.25, 6.25))
        # a file whose clock starts at 1.4 (MPEG-TS): the picture starts with it
        self.assertEqual(self._span(probe_json({"start_time": "1.400000", "duration": "5.000000"},
                                               {"start_time": "1.400000"}), 5.0), (0.0, 5.0))
        # Matroska keeps no length per stream: the end is the file's
        self.assertEqual(self._span(probe_json({"start_time": "0.250000"}, {"start_time": "0.000000"})),
                         (0.25, 7.0))
        # nothing readable: the whole file, from 0
        self.assertEqual(self._span(""), (0.0, 7.0))
        self.assertEqual(self._span(probe_json({"start_time": "N/A"}, {})), (0.0, 7.0))


class TestOwnEnvironment(unittest.TestCase):
    """The breakdown's libraries go in ~/.venvs/watch, never in the bot's .venv:
    the script's error named sys.executable, which inside a bot turn is the
    bot's own Python, and SKILL.md said "in the bot's Python"."""

    def setUp(self):
        self.venv = Path(tempfile.mkdtemp(prefix="bdvenv-")) / "watch"
        self.addCleanup(shutil.rmtree, self.venv.parent, True)

    def _error(self) -> str:
        with mock.patch.object(breakdown, "NEEDS", ("no_such_module_xyz",)), \
                mock.patch.object(breakdown, "VENV", self.venv), \
                self.assertRaises(SystemExit) as cm:
            breakdown.main(["clip.mp4"])
        return str(cm.exception)

    def test_missing_libraries_point_to_the_breakdowns_own_environment(self):
        msg = self._error()
        self.assertIn("no_such_module_xyz", msg)
        self.assertIn(f"python3 -m venv {self.venv} && {self.venv}/bin/pip install scenedetect librosa", msg)
        self.assertIn(f"{self.venv}/bin/python {breakdown.SCRIPT_DIR / 'breakdown.py'}", msg)
        self.assertNotIn(f"{sys.executable} -m pip", msg)
        # the environment is there, this Python is not it: run it there
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin" / "python").touch()
        msg = self._error()
        self.assertIn(f"Run it with the breakdown's own Python, with the same arguments: {self.venv}/bin/python", msg)
        self.assertNotIn("pip install", msg)
        # running in the environment, which lacks the library: install it there
        with mock.patch.object(breakdown.sys, "prefix", str(self.venv)):
            msg = self._error()
        self.assertIn(f"Install them in this environment: {self.venv}/bin/pip install scenedetect librosa", msg)

    def test_the_skill_installs_into_its_own_environment_only(self):
        import re
        skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("~/.venvs/watch/bin/python skills/watch/scripts/breakdown.py", skill)
        pips = re.findall(r"(\S*)pip install scenedetect", skill)
        self.assertTrue(pips)
        self.assertEqual(set(pips), {"~/.venvs/watch/bin/"}, "a pip install that is not the breakdown's own")
        deps = json.loads((SCRIPTS.parent / "deps.json").read_text(encoding="utf-8"))
        self.assertFalse({"scenedetect", "librosa"} & set(deps["pip"]))
        self.assertIn(".venvs/watch/bin/python", deps["check"]["watch-breakdown-venv"])


class TestDefaultFolder(unittest.TestCase):
    def test_the_default_folder_is_one_the_watch_cleanup_knows(self):
        # MOM's watch hooks clear /tmp/watch-* folders two hours after their last
        # use; a breakdown-* folder was never cleared there
        seen = {}

        def fake_analyse(video, work, start, end, source=None):
            seen["work"] = Path(work)
            return {}
        with mock.patch.object(breakdown, "download", return_value={"video_path": "clip.mp4"}), \
                mock.patch.object(breakdown, "analyse", side_effect=fake_analyse), \
                mock.patch.object(breakdown, "render", return_value=""), \
                mock.patch.object(breakdown, "missing_modules", return_value=[]), \
                mock.patch.object(breakdown.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                contextlib.redirect_stdout(io.StringIO()):
            breakdown.main(["https://example.com/v"])
        self.addCleanup(shutil.rmtree, seen["work"], True)
        self.assertTrue(seen["work"].name.startswith("watch-"), seen["work"])
        self.assertEqual(seen["work"].parent.resolve(), Path(tempfile.gettempdir()).resolve())


class TestMissingModules(unittest.TestCase):
    def test_reports_only_what_is_missing(self):
        self.assertEqual(breakdown.missing_modules(("json", "no_such_module_xyz")), ["no_such_module_xyz"])
        with mock.patch.object(breakdown, "NEEDS", ("json",)):
            self.assertEqual(breakdown.missing_modules(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
