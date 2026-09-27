"""presentations: build helpers fixed in the Linux bot review 2026-09-27.

- a video aspect_ratio given as a number (2.39) crashed the build or vanished;
- the aesthetic references name commercial typefaces Google does not serve,
  so their treatments fell back to the browser's default fonts;
- the page gets a static fallback when GSAP never loads.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "presentations" / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("create_presentation_under_test",
                                                      SCRIPTS / "create_presentation.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


cp = _load()


class AspectRatio(unittest.TestCase):
    def test_every_accepted_form(self):
        cases = {"16:9": 56.25, "4/3": 75.0, "1920x1080": 56.25, 2.0: 50.0, "2": 50.0,
                 "1:1": 100.0}
        for value, pct in cases.items():
            with self.subTest(value=value):
                self.assertAlmostEqual(cp._aspect_padding(value), pct, places=4)

    def test_nonsense_is_ignored_not_fatal(self):
        for value in (None, "", "wide", "0", 0, "16:0", "-2", True, "nan", "inf"):
            with self.subTest(value=value):
                self.assertIsNone(cp._aspect_padding(value))

    def test_number_reaches_the_page(self):
        html = cp.render_video({"type": "video", "url": "https://x.org/a.mp4", "aspect_ratio": 2.39})
        self.assertIn("padding-bottom: 41.8410%", html)


class FontStandIns(unittest.TestCase):
    def test_commercial_face_gets_a_loadable_stand_in_after_it(self):
        css = cp.build_font_css({"heading": "Neue Haas Grotesk Display", "serif": "GT Sectra"})
        self.assertIn("--font-heading: 'Neue Haas Grotesk Display', 'Inter', sans-serif;", css)
        self.assertIn("--font-serif: 'GT Sectra', 'Source Serif 4', serif;", css)
        link = cp.build_font_link({"heading": "Neue Haas Grotesk Display", "serif": "GT Sectra"})
        self.assertIn("family=Inter:", link)
        self.assertIn("family=Source+Serif+4:", link)

    def test_a_google_font_in_the_stand_in_map_still_loads_itself(self):
        # design_md maps Roboto and Open Sans to Inter; both are Google fonts.
        link = cp.build_font_link({"body": "Roboto"})
        self.assertIn("family=Roboto:", link)
        self.assertIn("--font-body: 'Roboto', 'Inter', sans-serif;",
                      cp.build_font_css({"body": "Roboto"}))

    def test_google_fonts_and_unknown_names_are_unchanged(self):
        css = cp.build_font_css({"heading": "Space Grotesk", "body": "Optima"})
        self.assertIn("--font-heading: 'Space Grotesk', sans-serif;", css)
        self.assertIn("--font-body: 'Optima', sans-serif;", css)
        self.assertIn("family=Optima:wght@400;500;600;700", cp.build_font_link({"body": "Optima"}))

    def test_every_reference_font_loads_something(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import references
        finally:
            sys.path.remove(str(SCRIPTS))
        # Deliberately left: system faces (Optima, Times New Roman), custom
        # display cuts with no honest free equivalent, and Sansita, which
        # Google serves under its own name.
        left_alone = {"Optima", "Times New Roman", "Dazed Display", "MUBI Display",
                      "Tsukushi Mincho", "Sansita"}
        for name in references.list_references():
            for slot, font in (references.load_reference(name).get("fonts") or {}).items():
                if font in left_alone:
                    continue
                with self.subTest(reference=name, slot=slot, font=font):
                    self.assertTrue(font in cp.FONT_CATALOG or cp._font_substitute(font),
                                    f"{font} would fall back to the browser default")


    def test_a_name_with_an_apostrophe_stays_one_css_string(self):
        # Audit, 2026-09-27: 'Suisse Int'l' ended the string at the apostrophe,
        # Chromium dropped the whole --font-body declaration, and the aesop
        # look rendered in the template's default font.
        css = cp.build_font_css({"body": "Suisse Int'l"})
        self.assertIn("--font-body: 'Suisse Int\\'l', 'Inter', sans-serif;", css)
        sys.path.insert(0, str(SCRIPTS))
        try:
            import references
        finally:
            sys.path.remove(str(SCRIPTS))
        for name in references.list_references():
            for font in (references.load_reference(name).get("fonts") or {}).values():
                with self.subTest(reference=name, font=font):
                    self.assertRegex(cp._css_string(font), r"^'(?:[^'\\]|\\.)*'$")


class OfflineFallback(unittest.TestCase):
    def test_fallback_runs_before_the_animation_script(self):
        html = cp.build_html({"title": "x", "sections": [{"type": "note", "paragraphs": ["p"]}]},
                             theme_css="")
        self.assertIn("html.no-gsap body *", html)
        guard = html.index("classList.add('no-gsap')")
        self.assertLess(guard, html.index("gsap.registerPlugin(ScrollTrigger)"))
        # its own script element, so the main script throwing cannot stop it
        self.assertLess(html.index("</script>", guard), html.index("gsap.registerPlugin(ScrollTrigger)"))


if __name__ == "__main__":
    unittest.main()
