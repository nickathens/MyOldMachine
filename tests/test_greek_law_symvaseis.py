"""Offline unit tests for the greek-law contract-review scaffold (symvasi_check.py).

Pure data and text: no network, no third party dependencies. Asserts the risk-control
catalogue integrity, the deterministic scanner (detection and apparent absence), the
checklist, and the CLI contract.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import os
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import symvasi_check as sc  # noqa: E402


class RiskControlsIntegrityTests(unittest.TestCase):
    def test_every_control_has_required_keys(self):
        for c in sc.RISK_CONTROLS:
            for key in ("id", "name", "article", "ceiling", "rule", "signatures", "review"):
                self.assertIn(key, c, f"{c.get('id')} missing {key}")

    def test_signatures_are_nonempty_lists_of_lists(self):
        for c in sc.RISK_CONTROLS:
            self.assertTrue(c["signatures"], f"{c['id']} has no signatures")
            for sig in c["signatures"]:
                self.assertIsInstance(sig, list)
                self.assertTrue(sig, f"{c['id']} has an empty signature")
                for part in sig:
                    self.assertTrue(part.strip())

    def test_ids_are_unique(self):
        ids = [c["id"] for c in sc.RISK_CONTROLS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ceiling_is_a_known_band(self):
        for c in sc.RISK_CONTROLS:
            self.assertIn(c["ceiling"], ("ΑΚΥΡΟΤΗΤΑ", "ΜΕΙΩΣΗ", "ΕΛΕΓΧΟΣ"))


class ScannerDetectionTests(unittest.TestCase):
    def _ids(self, text):
        return [c["id"] for c in sc.scan_text(text)["detected"]]

    def test_exclusion_of_liability_fires(self):
        text = "Άρθρο 7. Ο πωλητής απαλλάσσεται από κάθε ευθύνη για ζημίες."
        self.assertIn("apallaktiki-ritra", self._ids(text))

    def test_penalty_clause_fires(self):
        text = "Συμφωνείται ποινική ρήτρα ποσού 10.000 ευρώ ανά παράβαση."
        self.assertIn("poiniki-ritra", self._ids(text))

    def test_non_compete_fires(self):
        text = "Ο εργαζόμενος δεσμεύεται με ρήτρα μη ανταγωνισμού για πέντε έτη."
        self.assertIn("mi-antagonismos", self._ids(text))

    def test_consumer_terms_fire(self):
        text = "Οι παρόντες γενικοί όροι συναλλαγών δεσμεύουν τον καταναλωτή."
        self.assertIn("gos-katachrastikoi", self._ids(text))

    def test_detection_is_accent_and_case_insensitive(self):
        # No accents, lower case: must still match the accented signatures.
        text = "ο προμηθευτης απαλλασσεται απο καθε ευθυνη"
        self.assertIn("apallaktiki-ritra", self._ids(text))

    def test_clean_text_fires_nothing(self):
        text = "Ο πωλητής παραδίδει το πράγμα και ο αγοραστής καταβάλλει το τίμημα."
        self.assertEqual(self._ids(text), [])

    def test_a_control_fires_only_once_even_with_two_signatures(self):
        text = "Ουδεμία ευθύνη φέρει ο ανάδοχος και δεν ευθύνεται για τίποτε."
        ids = self._ids(text)
        self.assertEqual(ids.count("apallaktiki-ritra"), 1)


class EssentialsAbsenceTests(unittest.TestCase):
    def _absent(self, text):
        return [e["name"] for e in sc.scan_text(text)["absent"]]

    def test_sparse_text_reports_absences(self):
        self.assertTrue(self._absent("Κείμενο χωρίς δομικά στοιχεία."))

    def test_full_text_reports_no_absence(self):
        text = (
            "Μεταξύ των συμβαλλομένων με ΑΦΜ ορίζεται το αντικείμενο της παρούσας. "
            "Το τίμημα καταβάλλεται τμηματικά. Η διάρκεια και η καταγγελία ρυθμίζονται "
            "ρητά. Εφαρμοστέο δίκαιο είναι το ελληνικό και αρμόδια τα δικαστήρια Αθηνών. "
            "Ακολουθούν οι υπογραφές των μερών."
        )
        self.assertEqual(self._absent(text), [])


class RenderTests(unittest.TestCase):
    def test_render_risks_shows_articles(self):
        out = sc.render_risks()
        self.assertIn("ΑΚ 332", out)
        self.assertIn("ΑΚ 409", out)
        self.assertIn("Ν.2251/1994 άρθρο 2", out)

    def test_render_checklist_general_always_present(self):
        out = sc.render_checklist()
        self.assertIn("ΑΚ 369", out)  # form requirement appears in the general list

    def test_render_checklist_with_type_shows_type_items(self):
        out = sc.render_checklist("misthosi")
        self.assertIn("Μίσθωμα", out)

    def test_render_scan_states_no_final_colour(self):
        out = sc.render_scan(sc.scan_text("Ποινική ρήτρα 5.000 ευρώ."))
        self.assertIn("GREEN", out)  # the disclaimer naming the reviewer's grading


class CliTests(unittest.TestCase):
    def _run(self, argv, stdin=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if stdin is not None:
                saved = sys.stdin
                sys.stdin = io.StringIO(stdin)
                try:
                    rc = sc.main(argv)
                finally:
                    sys.stdin = saved
            else:
                rc = sc.main(argv)
        return rc, buf.getvalue()

    def test_checklist_returns_zero(self):
        rc, out = self._run(["checklist"])
        self.assertEqual(rc, 0)
        self.assertIn("Λίστα ελέγχου", out)

    def test_checklist_unknown_type_returns_two(self):
        rc, _ = self._run(["checklist", "--typos", "bogus"])
        self.assertEqual(rc, 2)

    def test_risks_returns_zero(self):
        rc, out = self._run(["risks"])
        self.assertEqual(rc, 0)
        self.assertIn("ΑΚ 332", out)

    def test_unknown_command_returns_two(self):
        rc, _ = self._run(["frobnicate"])
        self.assertEqual(rc, 2)

    def test_scan_file_returns_zero(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("Ο ανάδοχος απαλλάσσεται από κάθε ευθύνη.")
            path = fh.name
        try:
            rc, out = self._run(["scan", path])
            self.assertEqual(rc, 0)
            self.assertIn("ΑΚ 332", out)
        finally:
            os.unlink(path)

    def test_scan_missing_file_returns_two(self):
        rc, _ = self._run(["scan", "/no/such/contract.txt"])
        self.assertEqual(rc, 2)

    def test_scan_stdin(self):
        rc, out = self._run(["scan", "-"], stdin="Ποινική ρήτρα 10.000 ευρώ.")
        self.assertEqual(rc, 0)
        self.assertIn("ΑΚ 409", out)

    def test_scan_json_lists_detected_ids(self):
        rc, out = self._run(
            ["scan", "-", "--json"], stdin="Ρήτρα μη ανταγωνισμού για τρία έτη.")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("mi-antagonismos", data["detected"])

    def test_risks_json_is_the_full_catalogue(self):
        rc, out = self._run(["risks", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)), len(sc.RISK_CONTROLS))


if __name__ == "__main__":
    unittest.main()
