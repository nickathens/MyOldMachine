"""Offline unit tests for the greek-engineer regulatory and routing tools.

Covers the specialty router, the dated regulatory navigator with its
freshness contract, the envelope arithmetic, the fine structure arithmetic,
the pre seismic fee, the dossier precheck, and the integrity of SKILL.md
cross references (every tool and module the spine names must exist).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import re
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "greek-engineer"
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import afthaireta  # noqa: E402
import domisi  # noqa: E402
import eidikotita  # noqa: E402
import fakelos  # noqa: E402
import kanonismoi  # noqa: E402
import proseismikos  # noqa: E402


class EidikotitaTests(unittest.TestCase):
    def test_registry_well_formed(self):
        for slug, d in eidikotita.DISCIPLINES.items():
            self.assertRegex(slug, r"^[a-z]+$")
            self.assertTrue(d["onoma"])
            self.assertTrue(d["perigrafi"])
            self.assertTrue(d["tools"])
            self.assertTrue(d["lexeis"])

    def test_every_tool_reference_resolves(self):
        for slug, d in eidikotita.DISCIPLINES.items():
            for tool in d["tools"]:
                self.assertTrue((SCRIPTS / tool).is_file(), f"{slug}: {tool} missing")
            for module in d["practice"]:
                self.assertTrue((SKILL_DIR / "practice" / module).is_file(),
                                f"{slug}: practice/{module} missing")

    def test_routing_electrical_phrase(self):
        hits = eidikotita.find("πτωση τασης σε γραμμη κουζινας")
        self.assertEqual(hits[0]["slug"], "ilektrologos")

    def test_routing_structural_phrase(self):
        hits = eidikotita.find("φερουσα τοιχοποιια και σεισμος")
        self.assertEqual(hits[0]["slug"], "politikos")

    def test_accent_and_case_insensitive(self):
        upper = eidikotita.find("ΠΤΩΣΗ ΤΑΣΗΣ")
        accented = eidikotita.find("πτώση τάσης")
        self.assertEqual(upper[0]["slug"], accented[0]["slug"])

    def test_final_sigma_folding(self):
        self.assertEqual(eidikotita._fold("τάσης"), eidikotita._fold("ΤΑΣΗΣ"))

    def test_no_match_returns_empty(self):
        self.assertEqual(eidikotita.find("ζυμαρικα με σαλτσα"), [])
        with self.assertRaises(ValueError):
            eidikotita.find("   ")


class KanonismoiTests(unittest.TestCase):
    def test_every_domain_well_formed(self):
        for slug, d in kanonismoi.DOMAINS.items():
            self.assertRegex(slug, r"^[a-z-]+$")
            self.assertTrue(d["titlos"])
            self.assertTrue(d["katastasi"])
            self.assertTrue(d["gia_ton_michaniko"])
            self.assertTrue(d["piges"], f"{slug} has no sources")
            dt.date.fromisoformat(d["as_of"])  # must parse

    def test_expected_domains_present(self):
        for slug in ("nok", "xorotaxia", "eurocodes", "energeia", "afthaireta",
                     "proseismikos", "e-adeies"):
            self.assertIn(slug, kanonismoi.DOMAINS)

    def test_freshness_zero_age_on_verification_day(self):
        day = dt.date.fromisoformat(kanonismoi.DOMAINS["nok"]["as_of"])
        rows = kanonismoi.freshness(today=day)
        for r in rows:
            self.assertFalse(r["stale"])

    def test_freshness_flags_stale_after_limit(self):
        day = dt.date.fromisoformat(kanonismoi.DOMAINS["nok"]["as_of"])
        rows = kanonismoi.freshness(today=day + dt.timedelta(days=kanonismoi.STALE_DAYS + 1))
        self.assertTrue(all(r["stale"] for r in rows))


class DomisiTests(unittest.TestCase):
    def test_envelope_arithmetic(self):
        r = domisi.perigramma(500, 0.8, 60, 11)
        self.assertAlmostEqual(r["max_domisi_m2"], 400.0)
        self.assertAlmostEqual(r["max_kalypsi_m2"], 300.0)
        self.assertEqual(r["orofoi_apo_ypsos"], 3)
        self.assertEqual(r["elaxistoi_orofoi_gia_pliri_domisi"], 2)
        self.assertEqual(r["endeiktikoi_orofoi"], 2)

    def test_footprint_for_full_exploitation(self):
        r = domisi.perigramma(500, 0.8, 60, 11)
        self.assertAlmostEqual(r["apotypoma_gia_pliri_domisi_sto_ypsos_m2"],
                               round(400.0 / 3, 2))

    def test_exhaustion_flag_true_when_sd_fits_under_height(self):
        r = domisi.perigramma(500, 0.8, 60, 11)
        self.assertTrue(r["domisi_exantleitai_sto_ypsos"])
        self.assertAlmostEqual(r["megisti_domisi_sto_ypsos_m2"], 400.0)

    def test_flags_when_sd_cannot_be_exhausted_within_height(self):
        # 500 m2 plot, SD 2.0 -> 1000 m2 buildable, coverage 40% -> 200 m2
        # footprint, height 9 m -> 3 floors. 3 x 200 = 600 < 1000: the naive
        # per floor footprint (333 m2) would exceed the allowed coverage, so
        # the tool must say the SD does not fit under the height cap.
        r = domisi.perigramma(500, 2.0, 40, 9)
        self.assertFalse(r["domisi_exantleitai_sto_ypsos"])
        self.assertAlmostEqual(r["megisti_domisi_sto_ypsos_m2"], 600.0)
        self.assertGreater(r["apotypoma_gia_pliri_domisi_sto_ypsos_m2"],
                           r["max_kalypsi_m2"])

    def test_low_rise_rural_case(self):
        r = domisi.perigramma(4000, 0.2, 20, 7.5, orofos_ypsos=3.2)
        self.assertAlmostEqual(r["max_domisi_m2"], 800.0)
        self.assertEqual(r["orofoi_apo_ypsos"], 2)

    def test_rejects_bad_inputs(self):
        for bad in ((0, 0.8, 60, 11), (500, 0, 60, 11), (500, 0.8, 0, 11),
                    (500, 0.8, 101, 11), (500, 0.8, 60, 0)):
            with self.assertRaises(ValueError):
                domisi.perigramma(*bad)


class AfthairetaTests(unittest.TestCase):
    def test_fine_structure_arithmetic(self):
        r = afthaireta.prostimo(1000, 35, [0.4, 1.0])
        self.assertAlmostEqual(r["prostimo_eyro"], 1000 * 35 * 0.15 * 0.4)

    def test_linearity_in_area(self):
        a = afthaireta.prostimo(1000, 35, [0.4])
        b = afthaireta.prostimo(1000, 70, [0.4])
        self.assertAlmostEqual(b["prostimo_eyro"], 2 * a["prostimo_eyro"])

    def test_coefficients_multiply(self):
        r = afthaireta.prostimo(800, 10, [0.5, 0.5, 2.0])
        self.assertAlmostEqual(r["ginomeno_syntel"], 0.5)
        self.assertAlmostEqual(r["prostimo_eyro"], 800 * 10 * 0.15 * 0.5)

    def test_paravolo_adds_to_total(self):
        r = afthaireta.prostimo(1000, 35, [0.4], paravolo=250)
        self.assertAlmostEqual(r["synoliko_kostos_eyro"], r["prostimo_eyro"] + 250)

    def test_categories_well_formed(self):
        self.assertEqual([k["kat"] for k in afthaireta.KATIGORIES], [1, 2, 3, 4, 5])
        for k in afthaireta.KATIGORIES:
            self.assertTrue(k["titlos"])
            self.assertTrue(k["perigrafi"])

    def test_deadline_scaffold_names_2028(self):
        joined = json.dumps(afthaireta.PROTHESMIES, ensure_ascii=False)
        self.assertIn("31.3.2028", joined)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            afthaireta.prostimo(0, 35, [0.4])
        with self.assertRaises(ValueError):
            afthaireta.prostimo(1000, 35, [])
        with self.assertRaises(ValueError):
            afthaireta.prostimo(1000, 35, [0.4, -1])
        with self.assertRaises(ValueError):
            afthaireta.prostimo(1000, 35, [0.4], paravolo=-5)


class ProseismikosTests(unittest.TestCase):
    """Κλιμακωτή αποζημίωση κατά την ΚΥΑ ΥΠ 688/2026 (ΦΕΚ Β' 1276/6.3.2026)."""

    def test_minimum_fee_floor(self):
        r = proseismikos.amoivi(350)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 500.0)
        self.assertTrue(r["efarmostike_elaxisti"])

    def test_breakeven_point(self):
        r = proseismikos.amoivi(500)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 500.0)

    def test_zone1_upper_edge(self):
        # 1.000 m2 ανήκει στην πρώτη ζώνη: 1,00 x 1000 = 1000.
        r = proseismikos.amoivi(1000)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 1000.0)
        self.assertAlmostEqual(r["vathmida"]["eyro_ana_tm"], 1.00)

    def test_zone2_lower_edge_minimum_applies(self):
        # 1.001 m2: 0,90 x 1001 = 900,9 αλλά ισχύει το ελάχιστο 1.000 της ζώνης.
        r = proseismikos.amoivi(1001)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 1000.0)
        self.assertTrue(r["efarmostike_elaxisti"])

    def test_zone2_minimum_region(self):
        # 1.050 m2: 945 με τον συντελεστή, υπερισχύει το ελάχιστο 1.000.
        r = proseismikos.amoivi(1050)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 1000.0)

    def test_zone2_upper_edge(self):
        # 1.500 m2: 0,90 x 1500 = 1350.
        r = proseismikos.amoivi(1500)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 1350.0)
        self.assertAlmostEqual(r["vathmida"]["eyro_ana_tm"], 0.90)

    def test_zone3_lower_edge_minimum_applies(self):
        # 1.501 m2: 0,80 x 1501 = 1200,8 αλλά ισχύει το ελάχιστο 1.350 της ζώνης.
        r = proseismikos.amoivi(1501)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 1350.0)
        self.assertTrue(r["efarmostike_elaxisti"])

    def test_zone3_worked_example(self):
        # Το παράδειγμα χρήσης του module: 2.400 m2 στη ζώνη 0,80 δίνει 1.920,
        # όχι 2.400 του ενιαίου 1 ευρώ ανά m2 της ανακοίνωσης έναρξης.
        r = proseismikos.amoivi(2400)
        self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], 1920.0)
        self.assertAlmostEqual(r["ana_elegkti_eyro"], 960.0)
        self.assertFalse(r["efarmostike_elaxisti"])

    def test_portfolio_total(self):
        r = proseismikos.amoivi(2400, ktiria=3)
        self.assertAlmostEqual(r["synolo_eyro"], 5760.0)

    def test_fee_monotonic_across_zone_edges(self):
        # Τα ελάχιστα των ζωνών κάνουν την αμοιβή συνεχή και μη φθίνουσα:
        # δεν υπάρχει εμβαδόν όπου περισσότερα τετραγωνικά δίνουν λιγότερα ευρώ.
        areas = [900, 999, 1000, 1001, 1050, 1112, 1499, 1500, 1501, 1687, 1688, 2400]
        fees = [proseismikos.amoivi(a)["amoivi_ana_ktirio_eyro"] for a in areas]
        for prev, cur in zip(fees, fees[1:]):
            self.assertLessEqual(prev, cur)

    def test_kerkides_steps(self):
        for tm, expected in [(500, 500.0), (501, 1000.0), (2000, 1000.0),
                             (2001, 2500.0), (5001, 5000.0), (10001, 7000.0)]:
            r = proseismikos.amoivi(tm, typos="kerkides")
            self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], expected)

    def test_stegastra_steps(self):
        for tm, expected in [(100, 500.0), (500, 1000.0), (2000, 2000.0),
                             (3500, 2750.0), (7500, 5000.0), (20000, 10000.0),
                             (20001, 15000.0)]:
            r = proseismikos.amoivi(tm, typos="stegastro")
            self.assertAlmostEqual(r["amoivi_ana_ktirio_eyro"], expected)

    def test_pylones_minimum_and_rate(self):
        r = proseismikos.amoivi_pylonon(2)
        self.assertAlmostEqual(r["amoivi_omadas_eyro"], 500.0)
        self.assertTrue(r["efarmostike_elaxisti"])
        r = proseismikos.amoivi_pylonon(5)
        self.assertAlmostEqual(r["amoivi_omadas_eyro"], 750.0)
        self.assertAlmostEqual(r["ana_elegkti_eyro"], 375.0)

    def test_split_between_two_inspectors(self):
        r = proseismikos.amoivi(1000)
        self.assertAlmostEqual(r["ana_elegkti_eyro"], 500.0)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            proseismikos.amoivi(0)
        with self.assertRaises(ValueError):
            proseismikos.amoivi(100, ktiria=0)
        with self.assertRaises(ValueError):
            proseismikos.amoivi(100, typos="gefyra")
        with self.assertRaises(ValueError):
            proseismikos.amoivi_pylonon(0)


class FakelosTests(unittest.TestCase):
    def test_catalog_well_formed(self):
        for slug, item in fakelos.STOIXEIA.items():
            self.assertRegex(slug, r"^[a-z-]+$")
            self.assertTrue(item["onoma"])
            self.assertTrue(item["ypografi"])
            self.assertTrue(item["elegktis"])
            self.assertIn("panta", item)
            if not item["panta"]:
                self.assertTrue(item.get("synthiki"), f"{slug} conditional without synthiki")

    def test_typoi_reference_valid_items(self):
        for slug, t in fakelos.TYPOI.items():
            for item in t["stoixeia"]:
                self.assertIn(item, fakelos.STOIXEIA, f"{slug} references unknown {item}")

    def test_gap_detection(self):
        r = fakelos.check("nea-oikodomi", exo=["topografiko", "architektoniki", "statiki"])
        gap_slugs = [k["slug"] for k in r["kena"]]
        self.assertIn("diagramma-kalypsis", gap_slugs)
        self.assertNotIn("topografiko", gap_slugs)
        self.assertIn("topografiko", r["plires"])

    def test_conditional_stays_question_until_confirmed(self):
        r = fakelos.check("nea-oikodomi")
        questions = [q["slug"] for q in r["anoixtes_erotiseis"]]
        self.assertIn("geotexniki", questions)
        r2 = fakelos.check("nea-oikodomi", synthikes_isxyoun=["geotexniki"])
        gap_slugs = [k["slug"] for k in r2["kena"]]
        self.assertIn("geotexniki", gap_slugs)
        self.assertNotIn("geotexniki", [q["slug"] for q in r2["anoixtes_erotiseis"]])

    def test_complete_dossier_has_no_gaps(self):
        always = [s for s, item in fakelos.STOIXEIA.items() if item["panta"]]
        r = fakelos.check("nea-oikodomi", exo=always)
        self.assertEqual(r["kena"], [])

    def test_readiness_counter_never_exceeds_denominator(self):
        # Having an unconfirmed conditional item must not inflate the ratio.
        everything = list(fakelos.STOIXEIA.keys())
        r = fakelos.check("nea-oikodomi", exo=everything)
        have, of = r["etoimotita"].split(" ")[0].split("/")
        self.assertLessEqual(int(have), int(of))

    def test_change_of_use_is_smaller_list(self):
        r = fakelos.check("allagi-xrisis")
        self.assertLess(len(r["kena"]), len(fakelos.check("nea-oikodomi")["kena"]))

    def test_rejects_unknown_type_and_item(self):
        with self.assertRaises(ValueError):
            fakelos.check("gefyra")
        with self.assertRaises(ValueError):
            fakelos.check("nea-oikodomi", exo=["anyparxto"])


class SkillIntegrityTests(unittest.TestCase):
    """Every artifact the spine names must exist: the greek-law grounding
    rule applied to the skill's own self references."""

    def test_skill_md_exists_and_names_disclaimer(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("DISCLAIMER.md", text)
        self.assertTrue((SKILL_DIR / "DISCLAIMER.md").is_file())

    def test_every_script_named_in_skill_md_exists(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for name in set(re.findall(r"([a-z_]+\.py)", text)):
            if name in ("diavgeia.py", "eurlex.py", "fetch_source.py"):
                law = SKILL_DIR.parent / "greek-law" / "scripts" / name
                self.assertTrue(law.is_file(), f"greek-law fetcher {name} missing")
            else:
                self.assertTrue((SCRIPTS / name).is_file(), f"{name} named but missing")

    def test_every_doc_named_in_skill_md_exists(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for name in set(re.findall(r"(?:practice|reference)/([a-z0-9-]+\.md)", text)):
            found = (SKILL_DIR / "practice" / name).is_file() or \
                (SKILL_DIR / "reference" / name).is_file()
            self.assertTrue(found, f"{name} named but missing")

    def test_all_scripts_are_named_in_skill_md(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for script in SCRIPTS.glob("*.py"):
            self.assertIn(script.name, text, f"{script.name} shipped but not documented")

    def test_deps_json_shape(self):
        data = json.loads((SKILL_DIR / "deps.json").read_text(encoding="utf-8"))
        self.assertIn("pip", data)
        self.assertTrue(any(p.startswith("ezdxf") for p in data["pip"]))

    def test_verify_tag_is_pervasive(self):
        # The honesty protocol must actually appear in every scaffolding script.
        for name in ("fortia.py", "plaisio.py", "ilektrologika.py", "michanologika.py",
                     "afthaireta.py", "domisi.py", "proseismikos.py", "fakelos.py"):
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("ΕΠΑΛΗΘΕΥΣΕ", text, f"{name} lacks the verify tag")


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, module, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = module.main(argv)
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_router_cli(self):
        out = self.run_cli(eidikotita, ["find", "πτωση τασης", "--json"])
        self.assertEqual(json.loads(out)[0]["slug"], "ilektrologos")
        self.assertTrue(self.run_cli(eidikotita, ["list"]).strip())
        self.assertTrue(self.run_cli(eidikotita, ["show", "politikos"]).strip())

    def test_kanonismoi_cli(self):
        out = self.run_cli(kanonismoi, ["show", "nok", "--json"])
        self.assertIn("katastasi", json.loads(out))
        self.assertTrue(self.run_cli(kanonismoi, ["freshness"]).strip())
        self.assertTrue(self.run_cli(kanonismoi, ["list"]).strip())

    def test_domisi_cli(self):
        out = self.run_cli(domisi, ["perigramma", "--emvadon", "500", "--sd", "0.8",
                                    "--kalypsi", "60", "--ypsos", "11", "--json"])
        self.assertAlmostEqual(json.loads(out)["max_domisi_m2"], 400.0)

    def test_afthaireta_cli(self):
        out = self.run_cli(afthaireta, ["prostimo", "--timi-zonis", "1000", "--tm", "35",
                                        "--syntelestes", "0.4,1.0", "--json"])
        self.assertAlmostEqual(json.loads(out)["prostimo_eyro"], 2100.0)
        self.assertTrue(self.run_cli(afthaireta, ["katigories"]).strip())

    def test_proseismikos_cli(self):
        out = self.run_cli(proseismikos, ["amoivi", "--tm", "350", "--json"])
        self.assertAlmostEqual(json.loads(out)["amoivi_ana_ktirio_eyro"], 500.0)
        self.assertTrue(self.run_cli(proseismikos, ["vimata"]).strip())

    def test_fakelos_cli(self):
        out = self.run_cli(fakelos, ["check", "--typos", "nea-oikodomi",
                                     "--exo", "topografiko,statiki", "--json"])
        data = json.loads(out)
        self.assertIn("kena", data)
        self.assertTrue(self.run_cli(fakelos, ["list"]).strip())
        self.assertTrue(self.run_cli(fakelos, ["eidikotites"]).strip())


if __name__ == "__main__":
    unittest.main()
