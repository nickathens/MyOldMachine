"""The x11grab recorders.

Linux bot review 2026-09-27: on a HiDPI desktop (4096x2160 at 200%) the
presentation recorder captured the dock, the top bar, the browser's tabs and
a 2x corner of the page. It now goes fullscreen at scale 1 and checks a grey
frame first. The media recorder records a virtual display in MOM, so only its
failure exits are covered here: they used to exit 0.

The live tests open a browser on display :0, so they run only with
LIVE_RECORDING=1.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD_VIDEO = ROOT / "skills" / "media" / "scripts" / "record_video.py"
RECORD_PRES = ROOT / "skills" / "presentations" / "scripts" / "record_presentation.py"
LIVE = os.environ.get("LIVE_RECORDING") == "1"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(importlib.util.find_spec("numpy"), "the frame gate needs numpy")
class CaptureAreaGate(unittest.TestCase):
    def test_grey_passes_and_an_overlay_is_measured(self):
        for mod in (_load(RECORD_PRES, "rp_gate"),):
            with self.subTest(module=mod.__name__):
                w, h = 200, 100
                grey = bytes([mod.GATE_GREY] * (w * h * 3))
                self.assertEqual(mod.foreign_fraction(grey), 0.0)
                # a white banner over the top-right tenth of the area
                frame = bytearray(grey)
                for y in range(0, 10):
                    for x in range(0, w):
                        i = (y * w + x) * 3
                        frame[i:i + 3] = b"\xff\xff\xff"
                self.assertAlmostEqual(mod.foreign_fraction(bytes(frame)), 0.1, places=3)
                # nothing captured is never "clear"
                self.assertEqual(mod.foreign_fraction(b""), 1.0)
                self.assertEqual(mod.foreign_fraction(b"\x7f\x7f"), 1.0)
                # small noise within tolerance is still grey
                noisy = bytes([mod.GATE_GREY + 10] * (w * h * 3))
                self.assertEqual(mod.foreign_fraction(noisy), 0.0)


class Arguments(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("ffmpeg"),
                         "the media recorder is Linux only and checks for ffmpeg first")
    def test_screen_without_display_exits_one(self):
        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run([sys.executable, str(RECORD_VIDEO), "--screen",
                                  str(Path(tmp) / "o.mp4"), "--duration", "1"],
                                 capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("DISPLAY", run.stderr)

    def test_presentation_odd_size_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".html") as f:
            run = subprocess.run([sys.executable, str(RECORD_PRES), "--html", f.name,
                                  "--width", "1921"], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)


MARKER = """<!doctype html><html><head><title>markerprobe</title><style>
html,body{margin:0;height:100%;background:#ff0000;overflow:hidden}
#tl{position:fixed;top:0;left:0;width:100px;height:100px;background:#00ff00}
#br{position:fixed;bottom:0;right:0;width:100px;height:100px;background:#0000ff}
</style></head><body><div id="tl"></div><div id="br"></div></body></html>"""


@unittest.skipUnless(LIVE, "opens a browser on display :0; set LIVE_RECORDING=1")
class LiveCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rec-live-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "m.html").write_text(MARKER)

    def _frame(self, video: Path):
        from PIL import Image
        png = self.tmp / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(video),
                        "-frames:v", "1", str(png)], check=True)
        return Image.open(png).convert("RGB")

    def test_failure_exits_nonzero(self):
        run = subprocess.run([sys.executable, str(RECORD_PRES), "--html",
                              str(self.tmp / "m.html"), "--output", str(self.tmp / "x.mp4"),
                              "--width", "8000", "--height", "8000"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("smaller than", run.stderr)


if __name__ == "__main__":
    unittest.main()
