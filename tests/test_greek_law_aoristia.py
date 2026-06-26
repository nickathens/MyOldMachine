"""Offline unit tests for the greek-law αοριστία structural checker.

Pure text analysis: no network, no third party dependencies. Feeds sample
δικόγραφα and asserts the structural findings, severities and verdict.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import aoristia_check as ac  # noqa: E402


COMPLETE_AGOGI = """ΕΝΩΠΙΟΝ ΤΟΥ ΜΟΝΟΜΕΛΟΥΣ ΠΡΩΤΟΔΙΚΕΙΟΥ ΑΘΗΝΩΝ

ΑΓΩΓΗ

Του Ιωάννη Παπαδόπουλου του Γεωργίου, κατοίκου Αθηνών, οδός Σταδίου 10,
με ΑΦΜ 123456789.

ΚΑΤΑ

Της εταιρείας ΑΛΦΑ ΑΕ, που εδρεύει στην Αθήνα, με ΑΦΜ 998877665.

Την 1 Μαρτίου 2025 ο εναγόμενος προκάλεσε υπαιτίως ζημία στο όχημά μου κατά τη
διάρκεια τροχαίου ατυχήματος. Από την παράνομη και υπαίτια συμπεριφορά του υπέστην
υλική ζημία ύψους 5.000 ευρώ για την επισκευή του οχήματος, ποσό που αναλύεται σε
ανταλλακτικά και εργατικά.

Επειδή η αξίωσή μου είναι νόμιμη και βάσιμη, στηριζόμενη στα άρθρα 914 και 297 ΑΚ.

ΓΙΑ ΤΟΥΣ ΛΟΓΟΥΣ ΑΥΤΟΥΣ

ΖΗΤΩ να γίνει δεκτή η αγωγή, να υποχρεωθεί ο εναγόμενος να μου καταβάλει το ποσό
των 5.000 ευρώ νομιμοτόκως, και να καταδικαστεί στη δικαστική μου δαπάνη.

Αθήνα, 15 Απριλίου 2025
Ο πληρεξούσιος δικηγόρος
"""

NO_AITIMA = """ΕΝΩΠΙΟΝ ΤΟΥ ΜΟΝΟΜΕΛΟΥΣ ΠΡΩΤΟΔΙΚΕΙΟΥ ΑΘΗΝΩΝ
ΑΓΩΓΗ
Του Ιωάννη Παπαδόπουλου, κατοίκου Αθηνών, με ΑΦΜ 123456789.
ΚΑΤΑ
Της εταιρείας ΑΛΦΑ ΑΕ, με ΑΦΜ 998877665.
Επειδή ο εναγόμενος μου προκάλεσε ζημία την 1 Μαρτίου 2025 και ευθύνεται κατά το
άρθρο 914 ΑΚ, η αξίωσή μου είναι νόμιμη και βάσιμη και πρέπει να γίνει δεκτή.
Αθήνα, 15 Απριλίου 2025
Ο πληρεξούσιος δικηγόρος
"""

NO_AFM = """ΕΝΩΠΙΟΝ ΤΟΥ ΜΟΝΟΜΕΛΟΥΣ ΠΡΩΤΟΔΙΚΕΙΟΥ ΑΘΗΝΩΝ
ΑΓΩΓΗ
Του Ιωάννη Παπαδόπουλου, κατοίκου Αθηνών.
ΚΑΤΑ
Της εταιρείας ΑΛΦΑ ΑΕ.
Επειδή ο εναγόμενος μου προκάλεσε ζημία και ευθύνεται κατά το άρθρο 914 ΑΚ, με
αναλυτικό ιστορικό των γεγονότων που θεμελιώνουν επαρκώς την αξίωσή μου.
ΓΙΑ ΤΟΥΣ ΛΟΓΟΥΣ ΑΥΤΟΥΣ
ΖΗΤΩ να γίνει δεκτή η αγωγή και να καταδικαστεί ο εναγόμενος στη δικαστική δαπάνη.
Αθήνα, 15 Απριλίου 2025
Ο πληρεξούσιος δικηγόρος
"""

MONEY_NO_AMOUNT = """ΕΝΩΠΙΟΝ ΤΟΥ ΜΟΝΟΜΕΛΟΥΣ ΠΡΩΤΟΔΙΚΕΙΟΥ ΑΘΗΝΩΝ
ΑΓΩΓΗ
Του Ιωάννη Παπαδόπουλου, με ΑΦΜ 123456789.
ΚΑΤΑ
Της εταιρείας ΑΛΦΑ ΑΕ, με ΑΦΜ 998877665.
Επειδή ο εναγόμενος ευθύνεται για τη ζημία μου κατά το άρθρο 914 ΑΚ και οφείλει να
με αποζημιώσει πλήρως.
ΓΙΑ ΤΟΥΣ ΛΟΓΟΥΣ ΑΥΤΟΥΣ
ΖΗΤΩ να υποχρεωθεί ο εναγόμενος να μου καταβάλει αποζημίωση σε ευρώ.
Αθήνα, 15 Απριλίου 2025
Ο πληρεξούσιος δικηγόρος
"""


def _by_code(findings):
    return {f["code"]: f for f in findings}


class NormalizeTests(unittest.TestCase):
    def test_accent_and_case_fold(self):
        self.assertIn("ΖΗΤΩ", ac.normalize("ζητώ"))
        self.assertIn("ΕΝΑΓΩΝ", ac.normalize("Ενάγων"))

    def test_final_sigma_folds(self):
        self.assertTrue(ac.normalize("λόγος").endswith("ΛΟΓΟΣ"))


class CompleteAgogiTests(unittest.TestCase):
    def test_no_critical_no_warning(self):
        summary = ac.summarize(ac.check(COMPLETE_AGOGI))
        self.assertEqual(summary["critical"], 0)
        self.assertEqual(summary["warnings"], 0)

    def test_verdict_is_complete(self):
        summary = ac.summarize(ac.check(COMPLETE_AGOGI))
        self.assertIn("πλήρης", summary["verdict"])

    def test_every_element_ok(self):
        by = _by_code(ac.check(COMPLETE_AGOGI))
        for code in ("court", "doc_type", "plaintiff", "defendant", "afm",
                     "facts", "aitima", "date", "signature", "quantum"):
            self.assertEqual(by[code]["status"], "ok", code)


class MissingElementTests(unittest.TestCase):
    def test_empty_draft_is_critical(self):
        summary = ac.summarize(ac.check(""))
        self.assertGreater(summary["critical"], 0)

    def test_missing_aitima_is_critical(self):
        aitima = _by_code(ac.check(NO_AITIMA))["aitima"]
        self.assertEqual(aitima["status"], "missing")
        self.assertEqual(aitima["severity"], "critical")

    def test_missing_afm_is_warning_not_critical(self):
        afm = _by_code(ac.check(NO_AFM))["afm"]
        self.assertEqual(afm["status"], "missing")
        self.assertEqual(afm["severity"], "warning")

    def test_money_claim_without_amount_flags_quantum(self):
        quantum = _by_code(ac.check(MONEY_NO_AMOUNT))["quantum"]
        self.assertEqual(quantum["status"], "weak")
        self.assertEqual(quantum["severity"], "warning")


class GenericTypeTests(unittest.TestCase):
    def test_generic_skips_216_checks(self):
        codes = {f["code"] for f in ac.check(COMPLETE_AGOGI, doc_type="generic")}
        for absent in ("aitima", "facts", "quantum", "plaintiff", "defendant"):
            self.assertNotIn(absent, codes)
        for present in ("court", "doc_type", "parties", "date", "signature"):
            self.assertIn(present, codes)


class CliTests(unittest.TestCase):
    def test_json_cli_round_trip(self):
        fh = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            fh.write(COMPLETE_AGOGI)
            fh.close()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ac.main([fh.name, "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("findings", data)
            self.assertEqual(data["summary"]["critical"], 0)
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
