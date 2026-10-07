"""postproduction, ported from the Linux bot's sweep of 2026-10-07.

subs.py:

- A Greek SRT in Windows-1253, ISO-8859-7 or UTF-16, the way Greek Windows
  tools and Notepad save it, stopped at "'utf-8' codec can't decode".
- Greek written decomposed (a base letter and a combining accent, as some Mac
  tools export) counted each accented letter twice in the reading speed.
- The printed burn in command left every path bare, so a folder with a space
  in its name cut the command short.
- On the Mac the burn in could not run at all: Homebrew's ffmpeg has no
  libass, so no subtitles filter. It now names an ffmpeg that has one (the
  one on PATH, else Homebrew's keg-only ffmpeg-full) and says so plainly when
  none is installed. And every number it stated was wrong where it did run:
  libass read them in ffmpeg's 384x288 default script, so on 1920x1080 the
  45 px font burned 3.75 times too big and 202 px up. PlayResX and PlayResY
  now name the raster, and the margins include the outline.

supers.py: plan and audit never printed the glyphs a face lacks, so a
Greek line in a Latin-only face came back placed and "inside" safe.

audio.py: normalise wrote the track as 24 bit PCM into any container;
in an .mp4 that decoded 12.7 dB too loud and clipping, and the run reported
its own wrong measurement as the result, exit 0. spec.py probe now flags PCM
inside an MP4 wherever it meets one.

conform.py, archive.py, prove.py: both edl commands stopped at
"Invalid literal for Fraction: '29.97df'", and a colon written drop frame EDL
was read as non drop; sweep passed every gate for deleting the very file the
keep ledger names, and exited 0 on STOP; the printed seek command left the
path bare.

upres.py, _common.py: verify and temporal held every sampled frame
several times over with no guard (UHD: 3.5 GB at the default 8 frames, 8.9 GB
at 24, inside the bot's process); comp track's guard raised a MemoryError
main_guard did not catch, so its advice came as a traceback.
"""

import contextlib
import importlib.util
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "postproduction" / "scripts"
# CI installs no numpy; the compositing and upres tests need it.
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_CV2 = importlib.util.find_spec("cv2") is not None


# greek-law and postproduction each ship a `_common`, and the scripts import it
# and each other (spec, prove, _pix...) by bare name. Under `unittest discover`
# the greek-law `_common`, or a sibling bound to it, can already be in
# sys.modules, so this module pins its own for its loads and its tests, with
# fresh siblings, and puts back whatever was there.
SIBLINGS = sorted(p.stem for p in SCRIPTS.glob("*.py"))
_PP_COMMON = []
_SAVED = {}


def _pin():
    saved = {name: sys.modules.pop(name) for name in SIBLINGS if name in sys.modules}
    if not _PP_COMMON:
        spec = importlib.util.spec_from_file_location("_common", SCRIPTS / "_common.py")
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)
        _PP_COMMON.append(common)
    sys.modules["_common"] = _PP_COMMON[0]
    return saved


def _unpin(saved):
    for name in SIBLINGS:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


@contextlib.contextmanager
def _pinned_common():
    saved = _pin()
    try:
        yield
    finally:
        _unpin(saved)


def setUpModule():
    _SAVED.update(_pin())


def tearDownModule():
    _unpin(dict(_SAVED))
    _SAVED.clear()


def _load(name):
    spec = importlib.util.spec_from_file_location(f"pp_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with _pinned_common():
        spec.loader.exec_module(module)
    return module


subs = _load("subs")

SRT = "1\r\n00:00:01,000 --> 00:00:03,500\r\nΆλλη μια μέρα, κύριε Γιώργο.\r\n"


class Encodings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self, data):
        path = self.d / "s.srt"
        path.write_bytes(data)
        return subs.read(str(path))

    def test_greek_legacy_and_utf16_files_read_as_written(self):
        for codec, name in (("utf-8", "utf-8"), ("cp1253", "cp1253"), ("iso-8859-7", "iso-8859-7"),
                            ("utf-16", "utf-16")):
            with self.subTest(codec=codec):
                doc = self._read(SRT.encode(codec))
                self.assertEqual(doc["events"][0]["lines"], ["Άλλη μια μέρα, κύριε Γιώργο."])
                self.assertEqual(doc["encoding"], name)
                if name != "utf-8":
                    self.assertIn("not UTF-8", doc["note"])

    def test_a_western_legacy_file_is_not_read_as_greek(self):
        doc = self._read("1\n00:00:01,000 --> 00:00:03,000\nCafé, naïve résumé.\n".encode("cp1252"))
        self.assertEqual(doc["events"][0]["lines"], ["Café, naïve résumé."])
        self.assertEqual(doc["encoding"], "cp1252")


class ReadingSpeed(unittest.TestCase):
    def test_decomposed_greek_counts_each_letter_once(self):
        composed = {"events": [{"index": 1, "start": 1.0, "end": 3.0, "lines": ["Καλημέρα σας"]}],
                    "file": "x.srt", "format": "srt"}
        decomposed = {**composed, "events": [dict(composed["events"][0],
                                                  lines=[unicodedata.normalize("NFD", "Καλημέρα σας")])]}
        self.assertEqual(subs.check(composed, {})["rows"][0]["chars"], 12)
        self.assertEqual(subs.check(decomposed, {})["rows"][0]["chars"], 12)


class _NeedsBurnFfmpeg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not subs.burn_ffmpeg():
            raise unittest.SkipTest("needs an ffmpeg with the subtitles filter (libass)")


def _vf(command):
    argv = shlex.split(command)
    return argv[0], argv[argv.index("-vf") + 1]


class BurnInPixels(unittest.TestCase):
    def test_every_number_is_in_pixels_of_the_raster(self):
        res = subs.burn_command("s.srt", "v.mov", (1920, 1080), ffmpeg="/opt/x/ffmpeg")
        ffmpeg, vf = _vf(res["command"])
        self.assertEqual(ffmpeg, "/opt/x/ffmpeg")
        self.assertIn("force_style=PlayResX=1920\\,PlayResY=1080\\,FontSize=45\\,", vf)
        # The outline is drawn outside the text box, so each margin carries it.
        self.assertIn("MarginL=98\\,MarginR=98\\,MarginV=56\\,", vf)
        self.assertEqual((res["font_size_px"], res["margin_v_px"], res["margin_h_px"],
                          res["outline_px"], res["title_safe_inset_px"]), (45, 56, 98, 2, 54.0))

    def test_without_an_ffmpeg_that_can_burn_it_says_so(self):
        # Before: a command that died with "No such filter: 'subtitles'".
        with mock.patch.object(subs, "burn_ffmpeg", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                subs.burn_command("s.srt", "v.mov", (1920, 1080))
        self.assertIn("brew install ffmpeg-full", str(cm.exception))

    def test_the_ffmpeg_on_path_first_then_homebrews_full_build(self):
        keg = subs.FULL_FFMPEG[0]
        for has, want in ((lambda f: f == keg, keg), (lambda f: True, "/usr/bin/ffmpeg"),
                          (lambda f: False, None)):
            with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
                    mock.patch("os.access", return_value=True), \
                    mock.patch.object(subs, "_has_filter", side_effect=lambda f, name, has=has: has(f)):
                self.assertEqual(subs.burn_ffmpeg(), want)


def _ink_box(raw, w, h, tolerance=6):
    """Top, bottom, left and right of everything off the flat grey: text and outline."""
    base = raw[0]
    marks = raw.translate(bytes(0 if abs(v - base) <= tolerance else 1 for v in range(256)))
    rows = [r for r in range(h) if 1 in marks[r * w:(r + 1) * w]]
    lefts = [marks.find(1, r * w, (r + 1) * w) - r * w for r in rows]
    rights = [marks.rfind(1, r * w, (r + 1) * w) - r * w for r in rows]
    return rows[0], rows[-1], min(lefts), max(rights)


class BurnGeometry(_NeedsBurnFfmpeg):
    """The burned text is the size burn states, centred, inside title safe.

    Measured 2026-10-07 before the fix: the stated 45 px font on 1920x1080
    burned 156 px tall, outline included, with its bottom 195 px up.
    """
    LINES = {"one line": "Ηλέκτρα Hxg ρς",
             "wrapped": ("Αυτή είναι μια πάρα πολύ μεγάλη γραμμή υποτίτλων που δεν χωράει "
                         "ποτέ σε ένα πλάτος οθόνης χωρίς αναδίπλωση στο κέντρο")}

    def _burn(self, text, w, h, unwrapped=False):
        with tempfile.TemporaryDirectory() as d:
            srt = Path(d, "s.srt")
            srt.write_text(f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n", encoding="utf-8")
            res = subs.burn_command(str(srt), "unused.mov", (w, h))
            ffmpeg, vf = _vf(res["command"])
            if unwrapped:  # the width libass sets the text at, on one line, no margins
                m = res["margin_h_px"]
                vf = vf.replace("force_style=", "force_style=WrapStyle=2\\,", 1)
                vf = vf.replace(f"MarginL={m}\\,MarginR={m}\\,", "MarginL=0\\,MarginR=0\\,", 1)
            # The filter straight to raw grey: no encoder to add ringing round the ink.
            raw = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                  f"color=c=0x808080:s={w}x{h}:r=25:d=1", "-vf", vf,
                                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                 capture_output=True, check=True, timeout=120).stdout
        self.assertEqual(len(raw), w * h)
        return res, raw

    def test_the_ink_is_the_stated_size_and_inside_title_safe(self):
        for w, h in ((1920, 1080), (1280, 720)):
            for name, text in self.LINES.items():
                with self.subTest(raster=f"{w}x{h}", line=name):
                    res, raw = self._burn(text, w, h)
                    top, bottom, left, right = _ink_box(raw, w, h)
                    inset_v, inset_h = h * 0.05, w * 0.05
                    self.assertGreaterEqual(top, inset_v)
                    self.assertLessEqual(bottom, h - 1 - inset_v)
                    self.assertGreaterEqual(left, inset_h)
                    self.assertLessEqual(right, w - 1 - inset_h)
                    self.assertAlmostEqual((left + right) / 2, w / 2, delta=2)
                    # Sits on the title safe line, not hundreds of pixels up.
                    self.assertGreaterEqual(bottom, h - 1 - inset_v - 6)
                    if name == "one line":
                        size, outline = res["font_size_px"], res["outline_px"]
                        height = bottom - top + 1
                        self.assertLessEqual(height, 1.2 * size + 2 * outline)
                        self.assertGreaterEqual(height, 0.6 * size)

    def test_a_line_wider_than_title_safe_wraps_inside_it(self):
        # The fewest short words wider than title safe: still one line within
        # libass's default 10 px side margins, so only burn's own side
        # margins can wrap it back inside.
        w, h = 1920, 1080
        words = ["να", "το", "με", "σε"] * 20

        def width(n):
            _, raw = self._burn(" ".join(words[:n]), w, h, unwrapped=True)
            _, _, left, right = _ink_box(raw, w, h)
            return right - left + 1

        lo, hi = 1, len(words)
        self.assertGreater(width(hi), w * 0.9)
        while lo < hi:
            mid = (lo + hi) // 2
            if width(mid) > w * 0.9:
                hi = mid
            else:
                lo = mid + 1
        self.assertLess(width(lo), w - 20)
        _, raw = self._burn(" ".join(words[:lo]), w, h)
        _, _, left, right = _ink_box(raw, w, h)
        self.assertGreaterEqual(left, w * 0.05)
        self.assertLessEqual(right, w - 1 - w * 0.05)


class BurnCommand(_NeedsBurnFfmpeg):
    def test_the_printed_command_runs_on_an_awkward_folder(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d, "My Film [v2], final")
            folder.mkdir()
            srt, clip, out = folder / "Anna's subs.srt", folder / "clip.mp4", folder / "burned.mp4"
            srt.write_text(SRT, encoding="utf-8")
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
            cmd = subs.burn_command(str(srt), str(clip), (320, 180), out=str(out))["command"]
            run = subprocess.run(["bash", "-c", cmd + " -v error -y"], capture_output=True, text=True,
                                 timeout=120, cwd=d)
            self.assertEqual(run.returncode, 0, run.stderr[-800:])
            self.assertGreater(out.stat().st_size, 0)


LATIN_ONLY = Path("/usr/share/fonts/truetype/noto/NotoSansArmenian-Regular.ttf")


@unittest.skipUnless(LATIN_ONLY.exists(), "needs a face without Greek")
class SupersMissingGlyphs(unittest.TestCase):
    def test_plan_says_which_glyphs_the_face_lacks(self):
        import contextlib
        import io
        import json
        supers = _load("supers")
        with tempfile.TemporaryDirectory() as d:
            spec = Path(d, "s.json")
            spec.write_text(json.dumps({
                "font": str(LATIN_ONLY), "size": 80, "raster": [1920, 1080],
                "anchor": {"em_box_bottom_y": 960, "ink_left_x": 192},
                "blocks": [{"id": "b1", "lines": ["ΚΑΛΗΜΕΡΑ"]}]}), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                supers.main(["plan", str(spec)])
        self.assertIn("MISSING GLYPHS: ΚΑΛΗΜΕΡ.", out.getvalue())


class NormaliseLanding(unittest.TestCase):
    def test_the_landing_rule(self):
        audio = _load("audio")
        self.assertTrue(audio.landed({"integrated_lufs": -16.0, "true_peak_dbtp": -11.7}, -16.0, 2.0, -1.0))
        self.assertFalse(audio.landed({"integrated_lufs": -3.3, "true_peak_dbtp": 4.1}, -16.0, 2.0, -1.0))
        self.assertFalse(audio.landed({"integrated_lufs": -23.8, "true_peak_dbtp": -5.0}, -23.0, 0.0, -1.0))
        self.assertFalse(audio.landed({"integrated_lufs": -23.0, "true_peak_dbtp": -0.5}, -23.0, 1.0, -1.0))
        self.assertFalse(audio.landed({"integrated_lufs": None, "true_peak_dbtp": None}, -23.0, 1.0, -1.0))

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_an_mp4_delivery_lands_on_target_in_aac(self):
        audio = _load("audio")
        spec_mod = _load("spec")
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "in.mp4"), Path(d, "out.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=4",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=4,volume=-30dB",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)],
                           check=True)
            res = audio.normalise(str(src), spec_mod.load_profile("web_hd25"), str(out))
            self.assertTrue(res["landed"], res["verdict"])
            self.assertAlmostEqual(res["after"]["integrated_lufs"], -16.0, delta=0.5)
            self.assertEqual(spec_mod.probe(str(out))["audio"][0]["codec"], "aac")



class EdlRates(unittest.TestCase):
    EDL = ("TITLE: T\nFCM: DROP FRAME\n\n"
           "001  A001     V     C        00:00:59:28 00:01:00:04 00:00:00:00 00:00:00:04\n")

    def _edl(self, d):
        path = Path(d, "cut.edl")
        path.write_text(self.EDL, encoding="utf-8")
        return str(path)

    def test_df_rates_are_read_by_both_edl_commands(self):
        conform = _load("conform")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(conform.main(["edl", "read", self._edl(d), "--fps", "29.97df"]), 0)
            self.assertEqual(conform.main(["edl", "check", self._edl(d), "--fps", "29.97df"]), 0)

    def test_a_rate_without_df_takes_drop_frame_from_the_fcm_line(self):
        # 00:00:59:28 to 00:01:00:04 skips the labels ;00 and ;01 at minute 1:
        # 4 frames drop frame, 6 read as non drop
        conform = _load("conform")
        with tempfile.TemporaryDirectory() as d:
            rate, drop = conform.parse_rate("29.97")
            doc = conform.read_edl(self._edl(d), rate, None)
            res = conform.check_edl(doc, rate, doc["drop_frame"])
        self.assertTrue(doc["drop_frame"])
        self.assertEqual(res["rows"][0]["src_frames"], 4)


class SweepKeptAndCondemned(unittest.TestCase):
    def test_a_survivor_cannot_be_condemned_and_stop_exits_one(self):
        archive, prove = _load("archive"), _load("prove")
        with tempfile.TemporaryDirectory() as d:
            old, backup = Path(d, "old"), Path(d, "backup")
            old.mkdir()
            backup.mkdir()
            master = old / "master.mov"
            master.write_text("master bytes")
            shutil.copy2(master, backup / "master.mov")
            ledger = old / "SHA256.json"
            prove.write_ledger(prove.sha_files([str(master)]), str(ledger))
            rc = archive.main(["sweep", "--keep-ledger", str(ledger), "--condemn", str(master),
                               "--restore-from", f"master.mov={backup / 'master.mov'}", "--execute",
                               "--json"])
            self.assertEqual(rc, 1)
            self.assertTrue(master.exists())


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class ProbeAndSeek(unittest.TestCase):
    def test_pcm_inside_an_mp4_is_flagged(self):
        spec_mod = _load("spec")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "pcm.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=1",
                            "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-c:a", "pcm_s16le",
                            "-shortest", str(out)], check=True)
            tag = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                  "stream=codec_tag_string", "-of", "csv=p=0", str(out)],
                                 capture_output=True, text=True).stdout.strip()
            if tag != "ipcm":
                self.skipTest("this ffmpeg does not write PCM into an MP4 as ipcm")
            info = spec_mod.probe(str(out))
        self.assertTrue(any("PCM inside an MP4" in f for f in info["flags"]))

    def test_the_printed_seek_command_runs_on_an_awkward_folder(self):
        prove = _load("prove")
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d, "My Film [v2], final")
            folder.mkdir()
            clip = folder / "clip.mov"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=2",
                            "-c:v", "prores_ks", str(clip)], check=True)
            cmd = prove.seek_for_frame(str(clip), 10)["ffmpeg"]
            run = subprocess.run(["bash", "-c", cmd + " -v error -y"], capture_output=True, text=True,
                                 timeout=120, cwd=d)
            self.assertEqual(run.returncode, 0, run.stderr[-500:])



class MemoryGuards(unittest.TestCase):
    @unittest.skipUnless(HAVE_NUMPY, "needs numpy")
    def test_upres_sizes_its_frames_before_decoding(self):
        import sys
        sys.path.insert(0, str(SCRIPTS))
        try:
            upres = _load("upres")
            import _pix  # bound to this skill's _common: setUpModule pinned it
        finally:
            sys.path.remove(str(SCRIPTS))
        a = {"width": 1920, "height": 1080}
        b = {"width": 3840, "height": 2160}
        saved = _pix.memory_headroom
        _pix.memory_headroom = lambda: 7 << 30
        try:
            upres._check_fits(a, b, 8, "verify")          # about 3.5 GB: fits in half of 7
            with self.assertRaises(MemoryError) as caught:
                upres._check_fits(a, b, 24, "verify")      # about 8.5 GB
        finally:
            _pix.memory_headroom = saved
        self.assertIn("--frames", str(caught.exception))

    def test_a_memory_guard_message_is_not_a_traceback(self):
        import contextlib
        import io
        common = _load("_common")

        def guarded(_argv):
            raise MemoryError("tracking 300 frames holds 30 GB")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            common.main_guard(guarded)
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("tracking 300 frames", err.getvalue())


# The compositing engine, read 2026-10-07 evening. The matrix fault in
# _pix's reader and writer is tested in tests/test_colour_matrix.py.
# Each script puts its own folder on sys.path, so the module does not.


@unittest.skipUnless(HAVE_NUMPY, "needs numpy")
class HomographyFarSide(unittest.TestCase):
    """apply_h clamped a vanishing w to sign(w) * 1e-12 + 1e-12, which is
    0 for a tiny NEGATIVE w: a division by zero, inf for the point."""

    def test_a_tiny_negative_w_stays_finite(self):
        import numpy as np
        G = _load("_geom")
        H = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, -1e-13]])
        with np.errstate(all="raise"):
            q = G.apply_h(H, [[2.0, 3.0]])
        self.assertTrue(np.all(np.isfinite(q)))
        self.assertLess(q[0, 0], 0)   # the far side keeps its sign


@unittest.skipUnless(HAVE_NUMPY, "needs numpy")
class TriangulateAnyTwoBackings(unittest.TestCase):
    """triangulate divided by the difference of channel SUMS, so a blue
    and a green pass of the same total brightness left every pixel unsolved.
    Smith and Blinn's projection onto the backing difference solves it."""

    def test_blue_and_green_of_equal_sum(self):
        import numpy as np
        P, M = _load("_pix"), _load("_matte")
        h, w = 4, 6
        alpha = np.linspace(0, 1, h * w).reshape(h, w).astype(np.float32)
        fg = np.full((h, w, 3), (0.6, 0.3, 0.2), np.float32)
        blue = np.array([0.1, 0.2, 0.8], np.float32)
        green = np.array([0.1, 0.8, 0.2], np.float32)

        def plate(bk):
            return P.Image(fg * alpha[..., None] + (1 - alpha[..., None]) * bk, "linear", "t", "t")

        def backing(bk):
            return P.Image(np.broadcast_to(bk, (h, w, 3)).copy(), "linear", "t", "t")

        r = M.triangulate(plate(blue), plate(green), backing(blue), backing(green))
        self.assertEqual(r["unsolved_px"], 0)
        self.assertLess(float(np.abs(r["alpha"] - alpha).max()), 1e-5)


@unittest.skipUnless(shutil.which("ffmpeg") and HAVE_NUMPY and HAVE_CV2, "needs ffmpeg, numpy and OpenCV")
class CadenceTrueTimeLandsOnTheLurch(unittest.TestCase):
    """the cadence found the lurch step j -> j + 1 at phase j but stretched
    step j + 1 in the true time vector, so smoothing against it put every
    lurch one frame late."""

    def test_long_steps_are_the_measured_ones(self):
        import cv2
        import numpy as np
        T = _load("_track")
        rng = np.random.default_rng(3)
        W, H, N = 320, 180, 41
        base = cv2.GaussianBlur(rng.random((H, W * 4)).astype(np.float32), (0, 0), 1.2)
        steps = np.array([8.0 if j % 5 == 2 else 4.0 for j in range(N - 1)])
        x = np.concatenate([[0.0], np.cumsum(steps)])
        raw = b"".join(
            np.repeat((np.clip(cv2.warpAffine(base, np.float32([[1, 0, -x[k]], [0, 1, 0]]), (W, H),
                                              flags=cv2.INTER_CUBIC), 0, 1) * 255
                       ).astype(np.uint8)[..., None], 3, axis=2).tobytes() for k in range(N))
        with tempfile.TemporaryDirectory() as tmp:
            clip = str(Path(tmp, "pan.mp4"))
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", "24", "-i", "-", "-c:v", "libx264", "-qp", "0",
                            "-pix_fmt", "yuv444p", clip], input=raw, check=True)
            r = T.cadence(clip, scale=1.0)
        self.assertEqual(r["verdict"], "CONFORMED")
        d = np.diff(np.array(r["true_time_normalised"]))
        long_steps = [j for j in range(len(d)) if d[j] > 1.5 * np.median(d)]
        self.assertEqual(long_steps[:4], [2, 7, 12, 17])


@unittest.skipUnless(HAVE_NUMPY, "needs numpy")
class HoldoutNeedsDetections(unittest.TestCase):
    """without --detections, holdout compared the track's own quads (its
    warps applied to its corners) with those same warps, and a track of random
    warps passed at 1e-13 px."""

    def test_no_detections_is_unproven(self):
        import contextlib
        import io
        import json
        import numpy as np
        comp = _load("comp")
        G = _load("_geom")
        rng = np.random.default_rng(1)
        corners = np.array([[100, 80], [500, 70], [520, 400], [90, 410]], float)
        warps = {str(i): (np.eye(3) + rng.normal(0, 0.05, (3, 3)) * [[1, 1, 40], [1, 1, 40], [0, 0, 0]]).tolist()
                 for i in range(12)}
        track = {"warps": warps, "corners": corners.tolist(),
                 "quads": {k: G.apply_h(np.array(v), corners).tolist() for k, v in warps.items()}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "track.json")
            path.write_text(json.dumps(track))
            args = comp.build_parser().parse_args(["holdout", "--track", str(path), "--json"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                args.fn(args)
        self.assertEqual(json.loads(buf.getvalue())["verdict"], "UNPROVEN")


if __name__ == "__main__":
    unittest.main()
