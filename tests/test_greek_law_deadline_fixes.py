"""greek-law deadline fixes, Linux bot review 2026-09-27. Each fails on the code before it.

ΚΠολΔ 144 παρ. 1 holds both the 19:00 expiry and the move of an εξαιρετέα last
day; παρ. 3 makes Saturday εξαιρετέα; παρ. 2 is about deadlines running against
the party who ordered a service. The August suspension is ΚΠολΔ 147 παρ. 2.
The engine and practice/prothesmies.md cited 144 παρ. 2 and 147 παρ. 7 and
printed no hour. And the ανακοπή κατά διαταγής πληρωμής runs 15 working days
(ΚΠολΔ 632 παρ. 2) while the engine, which its template points to, counted only
calendar days.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "greek-law"
ENGINE = SKILL / "scripts" / "prothesmies.py"


class DeadlineCitations(unittest.TestCase):
    def test_expiry_hour_is_printed(self):
        out = subprocess.run([sys.executable, str(ENGINE), "compute", "--apo", "2026-06-25", "--imeres", "30"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("ΛΗΞΗ: 2026-07-27 (Δευτέρα) στις 19:00", out)

    def test_expiry_hour_in_json(self):
        out = subprocess.run([sys.executable, str(ENGINE), "compute", "--apo", "2026-06-25", "--imeres", "30",
                              "--json"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out)["lixi_ora"], "19:00")

    def test_no_wrong_paragraphs(self):
        for path in [ENGINE, SKILL / "practice" / "prothesmies.md", SKILL / "SKILL.md"]:
            text = path.read_text(encoding="utf-8")
            for wrong in ("147 παρ. 7", "Μετάθεση λήξης (ΚΠολΔ 144 παρ. 2)", "εξαιρετέα** (ΚΠολΔ 144 παρ. 2)"):
                with self.subTest(path=path.name, wrong=wrong):
                    self.assertNotIn(wrong, text)


class WorkingDays(unittest.TestCase):
    def run_engine(self, *args):
        out = subprocess.run([sys.executable, str(ENGINE), "compute", *args, "--json"],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)["lixi"]

    def test_fifteen_working_days(self):
        # Thu 25.6.2026: 15 working days end Thu 16.7 (15 calendar days would say Fri 10.7)
        self.assertEqual(self.run_engine("--apo", "2026-06-25", "--imeres", "15", "--ergasimes"), "2026-07-16")

    def test_easter_holidays_are_skipped(self):
        # Mon 6.4.2026: Good Friday 10.4, the weekend and Easter Monday 13.4 do not count
        self.assertEqual(self.run_engine("--apo", "2026-04-06", "--imeres", "5", "--ergasimes"), "2026-04-15")

    def test_with_the_august_suspension(self):
        self.assertEqual(self.run_engine("--apo", "2026-07-20", "--imeres", "15", "--ergasimes",
                                         "--anastoli-avgoustou"), "2026-09-08")


if __name__ == "__main__":
    unittest.main()
