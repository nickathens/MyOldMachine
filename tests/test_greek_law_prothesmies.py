"""Offline unit tests for the greek-law deadline calculator (prothesmies.py).

Pure date arithmetic: no network, no third party dependencies. Verifies the
Orthodox Easter computation against three known years, the holiday set, the
KPolD 144 day-counting rules (dies a quo excluded, intermediate days counted,
last day rolled forward off weekends and holidays), the optional KPolD 147
August suspension, the registry integrity and the CLI.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-law" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prothesmies as pr  # noqa: E402


class EasterTests(unittest.TestCase):
    def test_known_orthodox_easter_dates(self):
        # Verified independently: Orthodox Pascha fell/falls on these dates.
        self.assertEqual(pr.orthodox_easter(2024), date(2024, 5, 5))
        self.assertEqual(pr.orthodox_easter(2025), date(2025, 4, 20))
        self.assertEqual(pr.orthodox_easter(2026), date(2026, 4, 12))

    def test_easter_always_sunday(self):
        for y in (2024, 2025, 2026, 2027, 2030):
            self.assertEqual(pr.orthodox_easter(y).weekday(), 6, y)


class ArgiesTests(unittest.TestCase):
    def test_fixed_holidays_present(self):
        a = pr.argies(2026)
        self.assertEqual(a[date(2026, 3, 25)], "Εικοστή Πέμπτη Μαρτίου")
        self.assertEqual(a[date(2026, 12, 25)], "Χριστούγεννα")
        self.assertEqual(a[date(2026, 8, 15)], "Κοίμηση της Θεοτόκου")

    def test_movable_holidays_anchored_to_easter(self):
        a = pr.argies(2026)
        self.assertIn(date(2026, 2, 23), a)  # Καθαρά Δευτέρα (Πάσχα - 48)
        self.assertIn(date(2026, 4, 10), a)  # Μεγάλη Παρασκευή (Πάσχα - 2)
        self.assertIn(date(2026, 4, 13), a)  # Δευτέρα του Πάσχα (Πάσχα + 1)
        self.assertIn(date(2026, 6, 1), a)   # Αγίου Πνεύματος (Πάσχα + 50)


class ComputeTests(unittest.TestCase):
    def test_dies_a_quo_excluded(self):
        r = pr.compute(date(2026, 6, 1), 3)
        self.assertEqual(r["enarxi"], "2026-06-02")
        self.assertEqual(r["teleftaia_imerologiaki"], "2026-06-04")
        self.assertEqual(r["lixi"], "2026-06-04")
        self.assertFalse(r["metatethike"])

    def test_sunday_last_day_rolls_to_monday(self):
        # 2026-06-01 is Monday; +6 = 2026-06-07 (Sunday) -> 2026-06-08 (Monday).
        r = pr.compute(date(2026, 6, 1), 6)
        self.assertEqual(r["teleftaia_imerologiaki"], "2026-06-07")
        self.assertEqual(r["lixi"], "2026-06-08")
        self.assertTrue(r["metatethike"])

    def test_last_day_on_holiday_cluster_rolls_past_it(self):
        # 2026-12-15 + 10 = 2026-12-25 (Christmas, Fri); 26 holiday, 27 Sunday;
        # rolls to 2026-12-28 (Monday).
        r = pr.compute(date(2026, 12, 15), 10)
        self.assertEqual(r["teleftaia_imerologiaki"], "2026-12-25")
        self.assertEqual(r["lixi"], "2026-12-28")
        self.assertEqual(len(r["metatopisi"]), 3)

    def test_intermediate_holidays_are_counted(self):
        # A period spanning Christmas keeps its length; only the LAST day rolls.
        # 2026-12-20 + 3 = 2026-12-23 (Wednesday), no roll, despite the 25th/26th
        # being outside this window. Confirms intermediate days are not skipped.
        r = pr.compute(date(2026, 12, 20), 3)
        self.assertEqual(r["lixi"], "2026-12-23")
        self.assertFalse(r["metatethike"])

    def test_august_suspension_excludes_august(self):
        r = pr.compute(date(2026, 7, 20), 30, anastoli_avgoustou=True)
        self.assertEqual(r["teleftaia_imerologiaki"], "2026-09-19")
        self.assertEqual(r["lixi"], "2026-09-21")  # Sat -> Mon

    def test_without_suspension_august_counts(self):
        r = pr.compute(date(2026, 7, 20), 30)
        self.assertEqual(r["teleftaia_imerologiaki"], "2026-08-19")

    def test_rejects_nonpositive_days(self):
        with self.assertRaises(ValueError):
            pr.compute(date(2026, 1, 1), 0)


class RegistryTests(unittest.TestCase):
    def test_every_entry_well_formed(self):
        for slug, e in pr._REGISTRY.items():
            self.assertRegex(slug, r"^[a-z-]+$")
            for key in ("onoma", "imeres", "arthro", "afetiria", "parallages",
                        "katigoria"):
                self.assertIn(key, e, slug)
            self.assertIsInstance(e["imeres"], int)
            self.assertGreater(e["imeres"], 0)
            self.assertIsInstance(e["parallages"], list)

    def test_consistent_with_admin_module_figures(self):
        # The shipped dioikitiko-forologiko module asserts these; keep them aligned.
        self.assertEqual(pr._REGISTRY["aitisi-akyroseos"]["imeres"], 60)
        self.assertEqual(pr._REGISTRY["endikofani-prosfygi-ded"]["imeres"], 30)


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pr.main(argv)
        return rc, buf.getvalue()

    def test_compute_json(self):
        rc, out = self._run(
            ["compute", "--apo", "2026-12-15", "--imeres", "10", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["lixi"], "2026-12-28")

    def test_info_known_slug_carries_article_and_flag(self):
        rc, out = self._run(["info", "efesi"])
        self.assertEqual(rc, 0)
        self.assertIn("ΚΠολΔ 518", out)
        self.assertIn("επαλήθευσε", out)

    def test_info_unknown_slug_returns_2(self):
        rc, _ = self._run(["info", "anyparkti"])
        self.assertEqual(rc, 2)

    def test_list_runs(self):
        rc, out = self._run(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("efesi", out)

    def test_argies_json(self):
        rc, out = self._run(["argies", "2026", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["2026-12-25"], "Χριστούγεννα")


if __name__ == "__main__":
    unittest.main()
