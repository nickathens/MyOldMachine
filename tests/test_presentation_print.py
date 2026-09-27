"""presentations: every animation preset prints, numeric section numbers build,
and a page whose GSAP never loads still shows its sections.

Linux bot review 2026-09-27. The print stylesheet forced opacity and transforms but
not the blur filter or clip-path that the smooth, blur and clip presets put on
elements until they scroll into view, so a PDF of such a treatment came out
blurred (smooth, blur) or with every paragraph invisible (clip): pdftotext
found none of the body text. A divider "number" given as a JSON number
crashed the build whenever the sidebar menu was on (html.escape(1)), and a
numeric video aspect_ratio crashed it too (float.split). Every section starts
at opacity 0 and only GSAP (from a CDN) reveals it, so offline the page was
blank. The menu's click handler used querySelector('#nav-1.1'), which throws.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "presentations" / "scripts" / "create_presentation.py"

TREATMENT = {
    "title": "Print test", "nav": "sidebar",
    "cover": {"brand": "TEST", "title": "Print check", "type": "Treatment"},
    "sections": [
        {"type": "divider", "number": 1, "title": "One"},
        {"type": "note", "paragraphs": ["This paragraph must be readable in the PDF."]},
        {"type": "quote", "text": "A big quote that must print"},
        {"type": "cards", "items": [{"name": "Card A", "desc": "visible text A"}]},
        {"type": "video", "url": "https://example.org/v.mp4", "aspect_ratio": 1.78},
    ],
}


def _build(tmp: Path, spec: dict, pdf: bool = False) -> subprocess.CompletedProcess:
    (tmp / "t.json").write_text(json.dumps(spec), encoding="utf-8")
    cmd = [sys.executable, str(SCRIPT), "--json", str(tmp / "t.json"),
           "--output", str(tmp / "t.html")]
    if pdf:
        cmd += ["--pdf", str(tmp / "t.pdf")]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _scratch(test: unittest.TestCase) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pres-print-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    return tmp


@unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
class PrintsEveryPreset(unittest.TestCase):
    def test_blurred_and_clipped_presets_print_their_text(self):
        for animation in ("clip", "smooth", "blur"):
            with self.subTest(animation=animation):
                tmp = _scratch(self)
                run = _build(tmp, dict(TREATMENT, animation=animation), pdf=True)
                self.assertEqual(run.returncode, 0, run.stderr[-600:])
                text = subprocess.run(["pdftotext", str(tmp / "t.pdf"), "-"],
                                      capture_output=True, text=True).stdout
                for phrase in ("readable in the PDF", "big quote that must print", "visible text A"):
                    self.assertIn(phrase, text)


# A stand-in for the three GSAP files, served instead of the CDN: enough
# surface for the page script to run, and a scrollTo that really scrolls.
GSAP_STUB = """
window.gsap = {
  registerPlugin: function() {},
  set: function() {},
  timeline: function() { var t = {to: function() { return t; }}; return t; },
  to: function(target, vars) {
    if (target === window && vars && vars.scrollTo) {
      var y = vars.scrollTo.y;
      var top = (typeof y === 'number') ? y : y.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, top - (vars.scrollTo.offsetY || 0));
      window.__navScrolled = true;
    }
    return {};
  }
};
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
class InTheBrowser(unittest.TestCase):
    def _page_run(self, spec, gsap: bool, action):
        tmp = _scratch(self)
        run = _build(tmp, spec)
        self.assertEqual(run.returncode, 0, run.stderr[-600:])
        with _playwright()() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})

                def cdn(route):
                    if gsap and route.request.url.endswith("/gsap.min.js"):
                        route.fulfill(status=200, content_type="text/javascript", body=GSAP_STUB)
                    elif gsap:
                        route.fulfill(status=200, content_type="text/javascript", body="")
                    else:
                        route.abort()
                page.route("**/cdn.jsdelivr.net/**", cdn)
                page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
                page.goto((tmp / "t.html").as_uri(), wait_until="load")
                return action(page)
            finally:
                browser.close()

    def test_sections_show_when_gsap_never_loads(self):
        def check(page):
            return page.evaluate("""() => [
                document.documentElement.classList.contains('no-gsap'),
                getComputedStyle(document.querySelector('.note-text p')).opacity,
                getComputedStyle(document.querySelector('.gap-card')).opacity]""")
        no_gsap, note_opacity, card_opacity = self._page_run(TREATMENT, gsap=False, action=check)
        self.assertTrue(no_gsap)
        self.assertEqual(note_opacity, "1")
        self.assertEqual(card_opacity, "1")

    def test_loaded_gsap_keeps_the_entrance_animations(self):
        def check(page):
            return page.evaluate("""() => [
                document.documentElement.classList.contains('no-gsap'),
                getComputedStyle(document.querySelector('.note-text p')).opacity]""")
        no_gsap, note_opacity = self._page_run(TREATMENT, gsap=True, action=check)
        self.assertFalse(no_gsap)
        self.assertEqual(note_opacity, "0")

    def test_menu_link_with_a_dotted_number_scrolls(self):
        spec = dict(TREATMENT, sections=[
            {"type": "divider", "number": "1.1", "title": "First"},
            {"type": "note", "paragraphs": ["x " * 400]},
            {"type": "divider", "number": "1.2", "title": "Second"},
            {"type": "note", "paragraphs": ["y " * 400]},
        ])

        def click(page):
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.click("a.sidebar-nav-item[data-nav-index='1']")
            page.wait_for_timeout(300)
            return page.evaluate("() => [window.__navScrolled === true, window.scrollY]"), errors
        (scrolled, scroll_y), errors = self._page_run(spec, gsap=True, action=click)
        self.assertEqual(errors, [])
        self.assertTrue(scrolled)
        self.assertGreater(scroll_y, 0)


if __name__ == "__main__":
    unittest.main()
