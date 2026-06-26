"""Offline unit tests for the greek-law layperson guides (odigoi.py).

Pure data and string handling: no network, no third party dependencies. Verifies
the guide registry integrity, the accent insensitive find routing, the
deterministic legal-aid (Ν.3226/2004) two thirds arithmetic, the render output
(including the escalation heading every guide must carry), and the CLI contract.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import odigoi as od  # noqa: E402

EXPECTED_SLUGS = {
    "misthosi-engyisi", "apolysi-ergasia", "katanalotis", "prostima-trochaias",
    "diadikasies-kep-aade", "oikogeneia-klironomia", "nomiki-voithia",
}


class RegistryTests(unittest.TestCase):
    def test_every_guide_well_formed(self):
        for g in od.GUIDES:
            self.assertRegex(g["slug"], r"^[a-z-]+$")
            for key in ("slug", "title", "area", "keywords", "summary", "rights",
                        "steps", "escalation", "tools"):
                self.assertIn(key, g, g["slug"])
            self.assertIsInstance(g["keywords"], list)
            self.assertTrue(g["keywords"], g["slug"])
            for listfield in ("rights", "steps", "escalation"):
                self.assertTrue(g[listfield], f"{g['slug']}:{listfield}")

    def test_slugs_unique_and_complete(self):
        slugs = [g["slug"] for g in od.GUIDES]
        self.assertEqual(len(slugs), len(set(slugs)))
        # All seven promised everyday topics ship.
        self.assertEqual(set(slugs), EXPECTED_SLUGS)

    def test_keywords_are_accent_folded(self):
        # Keywords are matched after normalize(); they must carry no tonos so the
        # stored form and a user query fold to the same thing.
        for g in od.GUIDES:
            for kw in g["keywords"]:
                self.assertEqual(kw, od.normalize(kw).lower(), f"{g['slug']}:{kw}")


class FindTests(unittest.TestCase):
    def test_find_matches_accent_insensitively(self):
        # A user types with tonos; the stored keyword has none. Both must match.
        hits = od.find("εγγύηση")
        self.assertIn("misthosi-engyisi", [g["slug"] for g in hits])

    def test_find_matches_title_word(self):
        hits = od.find("καταναλωτη")
        self.assertIn("katanalotis", [g["slug"] for g in hits])

    def test_find_unknown_returns_empty(self):
        self.assertEqual(od.find("ζζζαβγ"), [])


class LegalAidTests(unittest.TestCase):
    def test_without_reference_does_not_decide(self):
        r = od.legal_aid_check(5000.0)
        self.assertIsNone(r["orio_anaforas"])
        self.assertIsNone(r["dikaioucos"])
        self.assertIsNone(r["katofli"])

    def test_two_thirds_threshold(self):
        r = od.legal_aid_check(5000.0, 9000.0)
        self.assertAlmostEqual(r["katofli"], 6000.0)

    def test_eligible_at_or_below_threshold(self):
        self.assertTrue(od.legal_aid_check(5000.0, 9000.0)["dikaioucos"])
        self.assertTrue(od.legal_aid_check(6000.0, 9000.0)["dikaioucos"])  # boundary

    def test_not_eligible_above_threshold(self):
        self.assertFalse(od.legal_aid_check(7000.0, 9000.0)["dikaioucos"])


class RenderTests(unittest.TestCase):
    def test_show_carries_escalation_heading(self):
        # The escalation block is the safety feature; it must always render.
        out = od.render_guide(od.get("oikogeneia-klironomia"))
        self.assertIn("Πότε χρειάζεσαι δικηγόρο", out)
        self.assertIn("αποποίησης", out)

    def test_voithia_undecided_render_tells_user_what_to_supply(self):
        out = od.render_voithia(od.legal_aid_check(5000.0))
        self.assertIn("orio-anaforas", out)
        self.assertNotIn("ΠΙΘΑΝΩΣ", out)

    def test_voithia_decided_render_shows_verdict_and_flag(self):
        out = od.render_voithia(od.legal_aid_check(5000.0, 9000.0))
        self.assertIn("ΠΙΘΑΝΩΣ ΔΙΚΑΙΟΥΧΟΣ", out)
        self.assertIn("επαλήθευσε", out)


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = od.main(argv)
        return rc, buf.getvalue()

    def test_list_runs(self):
        rc, out = self._run(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("misthosi-engyisi", out)

    def test_show_known_slug(self):
        rc, out = self._run(["show", "katanalotis"])
        self.assertEqual(rc, 0)
        self.assertIn("καταναλωτ", od.normalize(out).lower())

    def test_show_unknown_slug_returns_2(self):
        rc, _ = self._run(["show", "anyparktos"])
        self.assertEqual(rc, 2)

    def test_find_json(self):
        rc, out = self._run(["find", "απολυση", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn("apolysi-ergasia", json.loads(out))

    def test_voithia_decided_json(self):
        rc, out = self._run(
            ["voithia", "--eisodima", "5000", "--orio-anaforas", "9000", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["dikaioucos"])

    def test_voithia_undecided_human(self):
        rc, out = self._run(["voithia", "--eisodima", "5000"])
        self.assertEqual(rc, 0)
        self.assertIn("δεν αποφαίνεται", out)


if __name__ == "__main__":
    unittest.main()
