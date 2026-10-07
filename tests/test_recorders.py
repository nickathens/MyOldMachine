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


GSAP_STUB = """
window.gsap = {registerPlugin: function() {}, set: function() {}, to: function() { return {}; },
  timeline: function() { var t = {to: function() { return t; }}; return t; }};
window.ScrollTrigger = {create: function() {}};
window.ScrollToPlugin = {};
"""


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


@unittest.skipUnless(_playwright(), "playwright not installed")
class AutoplayChecks(unittest.TestCase):
    """Linux bot sweep 2026-10-07: with GSAP missing (no network, CDN down) the
    autoplay never armed, and the recorder filmed the still cover for its
    whole 600 s wait, then reported success. Headless, no display needed."""

    @classmethod
    def setUpClass(cls):
        cls.rp = _load(RECORD_PRES, "rp_autoplay")
        cls.tmp = Path(tempfile.mkdtemp(prefix="rec-autoplay-"))
        spec = {"title": "t", "cover": {"brand": "B", "title": "Cover", "duration": 4.0},
                "sections": [{"type": "divider", "number": "01", "title": "One"}, {"type": "hr"}]}
        (cls.tmp / "t.json").write_text(__import__("json").dumps(spec), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "skills/presentations/scripts/create_presentation.py"),
                        "--json", str(cls.tmp / "t.json"), "--output", str(cls.tmp / "t.html")],
                       check=True, capture_output=True, timeout=120)
        (cls.tmp / "plain.html").write_text("<html><body>no controller</body></html>")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def _on_page(self, name, gsap, check):
        with _playwright()() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()

                def cdn(route):
                    if gsap and route.request.url.endswith("/gsap.min.js"):
                        route.fulfill(status=200, content_type="text/javascript", body=GSAP_STUB)
                    elif gsap:
                        route.fulfill(status=200, content_type="text/javascript", body="")
                    else:
                        route.abort()
                page.route("**/cdn.jsdelivr.net/**", cdn)
                page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
                page.goto((self.tmp / name).as_uri() + "?autoplay=1&delay=1", wait_until="load")
                page.wait_for_timeout(300)
                return check(page)
            finally:
                browser.close()

    def test_missing_gsap_is_named_before_anything_is_filmed(self):
        problem = self._on_page("t.html", False, self.rp.autoplay_problem)
        self.assertIsNotNone(problem)
        self.assertIn("GSAP", problem)

    def test_an_armed_deck_passes(self):
        self.assertIsNone(self._on_page("t.html", True, self.rp.autoplay_problem))

    def test_a_page_without_the_controller_is_refused(self):
        problem = self._on_page("plain.html", True, self.rp.autoplay_problem)
        self.assertIn("no autoplay controller", problem)

    def test_the_wait_is_sized_from_the_deck(self):
        # cover 4.0 s, divider 2.5 s, hr 1.0 s, each plus 4.5 s of scrolling,
        # plus the 1 s opening delay
        seconds = self._on_page("t.html", True, lambda page: self.rp.expected_seconds(page, 1.0))
        self.assertAlmostEqual(seconds, 1.0 + 4.0 + 2.5 + 1.0 + 3 * 4.5, places=3)


if __name__ == "__main__":
    unittest.main()
