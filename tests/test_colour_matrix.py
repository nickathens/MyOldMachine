#!/usr/bin/env python3
"""RGB to video without a named colour matrix (Linux bot sweep 2026-10-07).

ffmpeg 6.1 converts RGB to YUV with BT.601 unless the filter graph names
another matrix, and decodes UNTAGGED YUV with BT.601 too, while players take
an untagged HD file for BT.709. So a frame that went YUV, RGB, YUV untouched
came back regraded:

- postproduction's comp reader and writer, and colorgrade's render (an
  identity LUT), both wrote BT.601 into a file tagged BT.709: 22.3 dB on
  luma for an untouched saturated test frame, 50.5 dB with the matrix named;
- colorgrade's analysis read an untagged HD source with BT.601 while the
  render was meant to treat it as BT.709;
- the two screen recorders encoded x11grab's RGB with BT.601 and tagged
  nothing, so an HD player showed the brand gold C9A84C as (204, 164, 71).
  MOM's media recorder grabs an Xvfb display, so its test runs the real
  _record with the display swapped for a gold lavfi source.

Every test here fails on the code before the fix.
"""

# numpy is a skill library CI does not install, so this module skips there
# instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

if _ilu.find_spec("numpy") is None:
    raise _unittest.SkipTest("needs numpy")

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
FFMPEG = shutil.which("ffmpeg")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_clip(path, size, tagged, matrix="bt709"):
    """A saturated test pattern encoded with `matrix`, tagged or not.

    Untagged BT.601 is what ffmpeg itself writes when nobody names a matrix;
    untagged BT.709 is what a player assumes for an untagged HD file.
    """
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc2=size={size}:rate=25:duration=0.12",
           "-vf", f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-crf", "4"]
    if tagged:
        cmd += ["-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-color_range", "tv"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=color_space",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    assert ("bt709" in probe) == tagged, probe


def _psnr_y(a, b):
    out = subprocess.run([FFMPEG, "-i", str(a), "-i", str(b), "-lavfi", "[0:v][1:v]psnr",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(out.split("PSNR y:")[1].split()[0])


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class CompositeWriterKeepsThePlate(unittest.TestCase):
    """postproduction _pix: read_frames into write_clip with nothing in between,
    called the way comp.py warp calls it (the source's colour passed on)."""

    def test_untouched_frames_come_back_tagged_as_they_went_in(self):
        # greek-law ships a `_common` too, imported by the same bare name: pin
        # this skill's for the load, and load _pix fresh under it.
        scripts = SKILLS / "postproduction" / "scripts"
        saved = sys.modules.get("_common")
        sys.path.insert(0, str(scripts))
        try:
            sys.modules["_common"] = _load(scripts / "_common.py", "_common")
            P = _load(scripts / "_pix.py", "pp_pix_matrix")
        finally:
            sys.path.remove(str(scripts))
            if saved is not None:
                sys.modules["_common"] = saved
            else:
                sys.modules.pop("_common", None)
        with tempfile.TemporaryDirectory() as tmp:
            for size, tagged in (("640x360", True), ("1280x720", False)):
                with self.subTest(size=size, tagged=tagged):
                    src = Path(tmp, f"src_{size}_{tagged}.mp4")
                    out = Path(tmp, f"out_{size}_{tagged}.mp4")
                    # comp mirrors its source, so an untagged plate is read and
                    # written back the way ffmpeg wrote it: BT.601.
                    _make_clip(src, size, tagged, "bt709" if tagged else "bt601")
                    info = P.clip_info(str(src))
                    frames = [img for _, img in P.read_frames(str(src))]
                    P.write_clip(str(out), iter(frames), Fraction(25, 1), crf=0,
                                 colour=info["colour"])
                    self.assertGreater(_psnr_y(src, out), 45.0)
                    self.assertEqual(P.clip_info(str(out))["colour"]["matrix"],
                                     info["colour"]["matrix"])

    def test_comp_warp_passes_the_source_colour(self):
        text = (SKILLS / "postproduction" / "scripts" / "comp.py").read_text(encoding="utf-8")
        self.assertIn('colour=info["colour"]', text)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ColorgradeIdentityRendersUntouched(unittest.TestCase):
    """colorgrade cgvideo.render with an identity LUT must not regrade."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SKILLS / "colorgrade" / "scripts"))
        import cgvideo
        cls.V = cgvideo

    def _identity(self, path, n=17):
        with open(path, "w") as fh:
            fh.write(f"LUT_3D_SIZE {n}\n")
            for b in range(n):
                for g in range(n):
                    for r in range(n):
                        fh.write(f"{r / (n - 1):.6f} {g / (n - 1):.6f} {b / (n - 1):.6f}\n")

    def test_identity_lut(self):
        V = self.V
        with tempfile.TemporaryDirectory() as tmp:
            lut = Path(tmp, "identity.cube")
            self._identity(lut)
            for size, tagged in (("640x360", True), ("1280x720", False)):
                with self.subTest(size=size, tagged=tagged):
                    src = Path(tmp, f"src_{size}_{tagged}.mp4")
                    out = Path(tmp, f"graded_{size}_{tagged}.mp4")
                    _make_clip(src, size, tagged)
                    media = V.probe(str(src))
                    shots = [V.Shot(0, 0, media.nb_frames, 0.0, media.duration)]
                    V.render(media, shots, {0: str(lut)}, str(out), crf=0)
                    self.assertGreater(_psnr_y(src, out), 45.0)

    def test_analysis_reads_untagged_hd_as_bt709(self):
        V = self.V
        with tempfile.TemporaryDirectory() as tmp:
            tagged, untagged = Path(tmp, "t.mp4"), Path(tmp, "u.mp4")
            _make_clip(tagged, "1280x720", True)
            _make_clip(untagged, "1280x720", False)
            a = next(V.sample_frames(V.probe(str(tagged)), width=320))[1]
            b = next(V.sample_frames(V.probe(str(untagged)), width=320))[1]
            self.assertLessEqual(float(np.abs(a - b).max()), 1.5 / 255.0)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class RecordersEncodeBt709(unittest.TestCase):
    """The x11grab recorders: the matrix is named and the file says so."""

    GOLD = (0xC9, 0xA8, 0x4C)

    def _decode_709(self, path, w, h):
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=in_color_matrix=bt709:in_range=tv",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(h, w, 3)[h // 2, w // 2].astype(int)

    def _tags(self, path):
        return subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                               "stream=color_space,color_primaries,color_transfer",
                               "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True).stdout.strip().split(",")

    def test_gold_survives_the_presentation_recorder(self):
        w, h = 1280, 720
        frame = np.zeros((h, w, 4), np.uint8)
        frame[..., 0], frame[..., 1], frame[..., 2] = self.GOLD[2], self.GOLD[1], self.GOLD[0]
        mod = _load(SKILLS / "presentations" / "scripts" / "record_presentation.py", "rp_matrix")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "grab.mp4")
            cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr0",
                   "-s", f"{w}x{h}", "-r", "30", "-i", "-", "-frames:v", "1",
                   *mod.ENCODE_ARGS, str(out)]
            subprocess.run(cmd, input=frame.tobytes(), check=True, capture_output=True)
            self.assertEqual(self._tags(out), ["bt709"] * 3)
            got = self._decode_709(out, w, h)
            self.assertLessEqual(int(np.abs(got - np.array(self.GOLD)).max()), 2, got)

    def test_gold_survives_the_media_recorder(self):
        """The real _record, capture and post-process, with x11grab's display
        replaced by a gold source in the same RGB layout x11grab hands over."""
        from unittest import mock
        w, h = 1280, 720
        mod = _load(SKILLS / "media" / "scripts" / "record_video.py", "rv_matrix")
        gold = "0x%02X%02X%02X" % self.GOLD
        real_popen = subprocess.Popen

        def grab_from_gold(cmd, **kwargs):
            # subprocess.run (the post-process) comes through here too
            cmd = list(cmd)
            if "x11grab" in cmd:
                i = cmd.index("x11grab")
                j = cmd.index("-i", i)
                cmd[i - 1:j + 2] = ["-f", "lavfi", "-i",
                                    f"color=c={gold}:s={w}x{h}:r=30,format=bgr0"]
            return real_popen(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "recording.mp4")
            with mock.patch.object(mod.subprocess, "Popen", side_effect=grab_from_gold):
                ok = mod._record(":99", w, h, out, 1, 30, None)
            mod._cleanup_all()
            self.assertTrue(ok, "the recording failed")
            self.assertEqual(self._tags(out), ["bt709"] * 3)
            got = self._decode_709(out, w, h)
            self.assertLessEqual(int(np.abs(got - np.array(self.GOLD)).max()), 2, got)


if __name__ == "__main__":
    unittest.main()
