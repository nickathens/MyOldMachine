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


THEME_CSS = (SCRIPTS / "theme.css").read_text(encoding="utf-8")
GREEK_DECK = {"title": "Ελληνική παρουσίαση",
              "cover": {"brand": "Κουκου", "title": "Καμπάνια"},
              "sections": [{"type": "cards", "caption": "Γιατί τώρα",
                            "items": [{"name": "Αυθεντικότητα", "desc": "Κανείς δεν την κατέχει."}]}]}


class GreekDecks(unittest.TestCase):
    """Linux bot sweep 2026-10-07."""

    def test_a_greek_deck_without_lang_is_marked_greek(self):
        # marked "en", its CSS capitals kept the tonos and no Greek font loaded
        html = cp.build_html(dict(GREEK_DECK), THEME_CSS)
        self.assertIn('<html lang="el">', html)
        self.assertIn("Manrope", html)
        self.assertEqual(cp.detect_lang({"title": "Treatment", "sections": [
            {"type": "note", "paragraphs": ["An English deck with one word, Αθήνα."]}]}), "en")

    def test_an_explicit_lang_is_kept(self):
        html = cp.build_html(dict(GREEK_DECK, lang="en"), THEME_CSS)
        self.assertIn('<html lang="en">', html)

    def test_greek_companions_reach_the_theme_fonts(self):
        # the link loaded Manrope and Inter, but with no "fonts" in the deck the
        # stacks never named them and Greek fell to the system font
        css = cp.build_font_css({}, subsets=["greek"], defaults=cp.theme_fonts(THEME_CSS))
        self.assertRegex(css, r"--font-heading: 'Space Grotesk', 'Manrope', 'Inter', sans-serif;")
        self.assertRegex(css, r"--font-body: 'Outfit', 'Manrope'")
        self.assertEqual(cp.build_font_css({}, subsets=None, defaults=cp.theme_fonts(THEME_CSS)), "")

    def test_theme_fonts_reads_the_first_family(self):
        self.assertEqual(cp.theme_fonts(THEME_CSS),
                         {"heading": "Space Grotesk", "body": "Outfit", "serif": "Playfair Display"})


class ModeFollowsTheBackground(unittest.TestCase):
    """a white canvas (--design-md stripe, a light --aesthetic, or a
    light scheme) on a deck with no mode stayed dark, whose titles, cards
    and tables are white: 1.0:1."""

    def _light(self, data):
        return ".section-title { color: var(--text); }" in cp.build_html(data, THEME_CSS)

    def test_a_light_background_takes_the_light_surface(self):
        self.assertTrue(self._light({"title": "x", "scheme": {"bg": "#ffffff"}, "sections": []}))
        self.assertTrue(self._light({"title": "x", "scheme": {"bg": "#efeae0"}, "sections": []}))

    def test_dark_or_explicit_stays(self):
        self.assertFalse(self._light({"title": "x", "scheme": {"bg": "#000000"}, "sections": []}))
        self.assertFalse(self._light({"title": "x", "sections": []}))
        self.assertFalse(self._light({"title": "x", "mode": "dark", "scheme": {"bg": "#ffffff"},
                                      "sections": []}))


class ContentHeadingColour(unittest.TestCase):
    """the content heading was inline #fff, white on the light modes' cream."""

    def test_the_colour_comes_from_a_class_the_light_modes_override(self):
        html = cp.build_html({"title": "x", "mode": "light", "sections": [
            {"type": "content", "heading": "The heading", "texts": ["t"]}]}, THEME_CSS)
        h3 = html[html.index("<h3"):html.index("</h3>")]
        self.assertIn('class="content-heading"', h3)
        self.assertNotIn("#fff", h3)
        self.assertIn(".content-heading { color: var(--text); }", html)
        self.assertIn(".content-heading { color: #fff; }", THEME_CSS)


if __name__ == "__main__":
    unittest.main()
