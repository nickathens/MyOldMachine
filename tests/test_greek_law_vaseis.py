"""Offline unit tests for the greek-law claim-basis (βάση αγωγής) reference.

Pure data and text: no network, no third party dependencies. Asserts the registry
integrity, lookup, accent insensitive search, and the CLI contract.
"""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vasi_agogis as va  # noqa: E402


class RegistryIntegrityTests(unittest.TestCase):
    def test_every_entry_has_required_keys(self):
        for e in va.REGISTRY:
            for key in ("slug", "title", "area", "code", "elements", "consequence"):
                self.assertIn(key, e, f"{e.get('slug')} missing {key}")
            self.assertTrue(e["elements"], f"{e['slug']} has no elements")

    def test_every_element_has_name_and_needs(self):
        for e in va.REGISTRY:
            for el in e["elements"]:
                self.assertIn("name", el)
                self.assertIn("needs", el)
                self.assertTrue(el["name"].strip())
                self.assertTrue(el["needs"].strip())

    def test_slugs_are_unique(self):
        slugs = [e["slug"] for e in va.REGISTRY]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_area_is_a_nonempty_label(self):
        # Not pinned to the current two areas: Stage 3c adds more (Εμπορικό, Εργατικό).
        for e in va.REGISTRY:
            self.assertTrue(e["area"].strip(), f"{e['slug']} has a blank area")


class DoctrineTests(unittest.TestCase):
    def test_adikopraxia_has_four_elements(self):
        # ΑΚ 914: παράνομη συμπεριφορά, υπαιτιότητα, ζημία, αιτιώδης σύνδεσμος.
        self.assertEqual(len(va.get("adikopraxia")["elements"]), 4)
        self.assertEqual(va.get("adikopraxia")["code"], "ΑΚ 914")

    def test_diekdikitiki_has_three_elements(self):
        # ΑΚ 1094: κυριότητα, νομή/κατοχή εναγομένου, ταυτότητα πράγματος.
        self.assertEqual(len(va.get("diekdikitiki")["elements"]), 3)
        self.assertEqual(va.get("diekdikitiki")["code"], "ΑΚ 1094")


class LookupTests(unittest.TestCase):
    def test_get_known_slug(self):
        self.assertIsNotNone(va.get("adikaiologitos-ploutismos"))

    def test_get_unknown_slug_returns_none(self):
        self.assertIsNone(va.get("does-not-exist"))


class SearchTests(unittest.TestCase):
    def test_search_by_word_stem(self):
        hits = va.search("πλουτισμ")
        self.assertEqual([h["slug"] for h in hits], ["adikaiologitos-ploutismos"])

    def test_search_by_article_number(self):
        hits = va.search("914")
        self.assertIn("adikopraxia", [h["slug"] for h in hits])

    def test_search_is_accent_and_case_insensitive(self):
        # lower case, no accents must still match the accented registry text.
        hits = va.search("αδικοπραξ")
        self.assertIn("adikopraxia", [h["slug"] for h in hits])

    def test_search_miss_returns_empty(self):
        self.assertEqual(va.search("ποινικη ευθυνη"), [])


class RenderTests(unittest.TestCase):
    def test_render_entry_shows_code_and_elements(self):
        out = va.render_entry(va.get("endosymvatiki-yperimeria"))
        self.assertIn("ΑΚ 340", out)
        self.assertIn("Όχληση του οφειλέτη", out)

    def test_render_list_shows_every_slug(self):
        out = va.render_list(va.REGISTRY)
        for e in va.REGISTRY:
            self.assertIn(e["slug"], out)


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = va.main(argv)
        return rc, buf.getvalue()

    def test_list_returns_zero_and_lists_all(self):
        rc, out = self._run(["list"])
        self.assertEqual(rc, 0)
        for e in va.REGISTRY:
            self.assertIn(e["slug"], out)

    def test_show_slug_returns_zero(self):
        rc, out = self._run(["arnitiki"])
        self.assertEqual(rc, 0)
        self.assertIn("ΑΚ 1108", out)

    def test_unknown_slug_returns_two(self):
        rc, _ = self._run(["bogus-slug"])
        self.assertEqual(rc, 2)

    def test_search_subcommand(self):
        rc, out = self._run(["search", "νομη"])
        self.assertEqual(rc, 0)
        self.assertIn("agogi-nomis", out)

    def test_json_round_trips(self):
        rc, out = self._run(["adikopraxia", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["code"], "ΑΚ 914")
        self.assertEqual(len(data["elements"]), 4)

    def test_list_json_is_the_full_registry(self):
        rc, out = self._run(["list", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)), len(va.REGISTRY))


if __name__ == "__main__":
    unittest.main()
