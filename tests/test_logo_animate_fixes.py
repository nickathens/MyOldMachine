"""logo-animate fixes from the Linux bot review 2026-09-27.

- render_overlay.py read a transparent logo's empty pixels (stored as black)
  as foreground, and headless Chrome painted only the top of its window, so
  the bottom of every render was cut: a perfect fit scored IoU 0.13;
- fit_ribbon_centerline.py had the same transparency defect;
- the three HTML builders dropped text after a nested element and the spaces
  between words ("Hello <tspan>World</tspan> again" -> "HelloWorld");
- svg_path_audit.py never counted the straight line a Z draws home.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('PIL', 'numpy', 'playwright') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "logo-animate" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"la_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tmp(test):
    d = Path(tempfile.mkdtemp(prefix="logo-anim-"))
    test.addCleanup(shutil.rmtree, d, True)
    return d


MIXED = ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">'
         '<text id="w" x="10" y="60">Hello <tspan>World</tspan> again</text>'
         '\n  <g>\n    <rect width="1" height="1"/>\n  </g>\n</svg>')


class TextSurvivesTheRebuild(unittest.TestCase):
    def test_every_builder_keeps_text_in_order(self):
        for name in ("svg_to_js_html", "animate_svg_html", "animate_svg_showcase"):
            with self.subTest(builder=name):
                data = _load(name).node_to_data(ET.fromstring(MIXED))
                text = data["children"][0]
                self.assertEqual(text["tag"], "text")
                self.assertEqual(text["children"][0], "Hello ")
                self.assertEqual(text["children"][1]["children"], ["World"])
                self.assertEqual(text["children"][2], " again")
                # indentation between elements is not carried
                self.assertTrue(all(not isinstance(c, str) for c in data["children"]))

    def test_the_page_renders_the_words(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        d = _tmp(self)
        (d / "w.svg").write_text(MIXED, encoding="utf-8")
        subprocess.run([sys.executable, str(SCRIPTS / "svg_to_js_html.py"), str(d / "w.svg"),
                        "--out", str(d / "w.html")], check=True, capture_output=True)
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto((d / "w.html").as_uri())
            self.assertEqual(pg.evaluate("document.querySelector('#w').textContent"),
                             "Hello World again")
            b.close()


class PathAuditCountsTheClose(unittest.TestCase):
    def test_z_draws_a_segment_home(self):
        audit = _load("svg_path_audit")
        segs, closed = audit.parse_path("M0 0 L10 0 L10 10 Z")
        self.assertTrue(closed)
        self.assertEqual(len(segs), 3)
        self.assertEqual(segs[-1]["p3"], (0.0, 0.0))
        report = audit.build_report(segs, closed, None, 8.0)
        self.assertIn(3, [w["join_after_segment"] for w in report["join_angle_warnings"]])

    def test_a_path_already_home_gets_no_extra_segment(self):
        audit = _load("svg_path_audit")
        segs, _ = audit.parse_path("M0 0 C 0 -10 20 -10 20 0 C 20 10 0 10 0 0 Z")
        self.assertEqual(len(segs), 2)


def _transparent_disc(path: Path, size=200, box=(50, 50, 150, 150)):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse(box, fill=(20, 60, 200, 255))
    im.save(path)


class OverlayOnTransparentLogo(unittest.TestCase):
    def test_a_perfect_fit_scores_as_one(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright not installed (the Chrome fallback)")
        d = _tmp(self)
        _transparent_disc(d / "src.png")
        (d / "fit.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">'
            '<circle cx="100" cy="100" r="50" fill="#143cc8"/></svg>', encoding="utf-8")
        run = subprocess.run([sys.executable, str(SCRIPTS / "render_overlay.py"), str(d / "fit.svg"),
                              str(d / "src.png"), "--out", str(d / "o.png"),
                              "--report", str(d / "r.json")], capture_output=True, text=True,
                             # past the script's own 120 s, so a hung browser
                             # is killed by the script rather than orphaned
                             timeout=180)
        self.assertEqual(run.returncode, 0, run.stderr[-500:])
        report = json.loads((d / "r.json").read_text())
        self.assertGreater(report["iou"], 0.95, report)


class ChromeWaitsOnNoKeyring(unittest.TestCase):
    """Review of #187: on macOS headless Chrome waited on the login keychain
    and no screenshot came (nothing in 60 s on the Mac mini, 2.2 s with the
    flag). --use-mock-keychain is the macOS twin of --password-store=basic."""

    def test_both_flags_reach_the_browser(self):
        from unittest import mock

        from PIL import Image
        overlay = _load("render_overlay")
        seen = []

        def browser(argv, **kwargs):
            seen.append(argv)
            shot = next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))
            Image.new("RGB", (200, 600), "white").save(shot)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        d = _tmp(self)
        (d / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"/>',
                                 encoding="utf-8")
        with mock.patch.object(overlay.subprocess, "run", side_effect=browser):
            overlay.render_svg(d / "a.svg", 200, 200, "chrome")
        self.assertIn("--use-mock-keychain", seen[0])
        self.assertIn("--password-store=basic", seen[0])



SESSION_BUS = Path(f"/run/user/{os.getuid()}/bus")


@unittest.skipUnless(SESSION_BUS.exists(), "needs a desktop session bus")
class OverlayWithTheDesktopSessionBus(unittest.TestCase):
    """A systemd unit, a scheduled job or a desktop terminal carries the
    session bus. Chrome then waited on the GNOME keyring and the overlay never
    came, where the bot's own environment (no bus) rendered in seconds (audit
    pass 3, 2026-09-27)."""

    def test_the_render_finishes(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright not installed (the Chrome fallback)")
        d = _tmp(self)
        _transparent_disc(d / "src.png")
        (d / "fit.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">'
            '<circle cx="100" cy="100" r="50" fill="#143cc8"/></svg>', encoding="utf-8")
        # A GNOME desktop makes Chrome pick the keyring as its password store,
        # and the bus is how it reaches it; either alone leaves Chrome on its
        # basic store, which is why the bot's own turns never hung.
        env = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={SESSION_BUS}",
               "XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "DESKTOP_SESSION": "ubuntu"}
        proc = subprocess.Popen([sys.executable, str(SCRIPTS / "render_overlay.py"), str(d / "fit.svg"),
                                 str(d / "src.png"), "--out", str(d / "o.png")],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            _, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)    # Chrome too, not only the script
            proc.wait()
            self.fail("the render hung on the desktop keyring")
        self.assertEqual(proc.returncode, 0, err[-500:])
        self.assertTrue((d / "o.png").exists())

class RibbonFitOnTransparentLogo(unittest.TestCase):
    def test_measures_the_ring_not_the_canvas(self):
        from PIL import Image, ImageDraw
        d = _tmp(self)
        im = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        dr.ellipse((40, 40, 200, 200), fill=(0, 0, 0, 255))
        dr.ellipse((56, 56, 184, 184), fill=(0, 0, 0, 0))   # ring 16 px thick
        im.save(d / "ring.png")
        pts = [[120 + 72 * math.cos(a), 120 + 72 * math.sin(a)]
               for a in (i * 2 * math.pi / 12 for i in range(12))]
        (d / "seeds.json").write_text(json.dumps({"points": pts}))
        run = subprocess.run([sys.executable, str(SCRIPTS / "fit_ribbon_centerline.py"),
                              str(d / "ring.png"), "--seeds", str(d / "seeds.json"),
                              "--out-dir", str(d / "out"), "--recenter", "1"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-500:])
        report = json.loads((d / "out" / "fit_report.json").read_text())
        self.assertGreater(report["pass0"]["valid_frac"], 0.9, report)
        self.assertLess(abs(report["pass1"]["width_max"] - 16), 3, report)


if __name__ == "__main__":
    unittest.main()
