"""Offline unit tests for the greek-engineer calculation engines.

Pure closed form arithmetic: no network, no third party dependencies. The
worked examples are self checking: the portal frame must close the wL2/8
statics identity, the Colebrook solver must zero its own residual, the
energy balance must return the input duty. Known values were cross verified
against an independent PyNite finite element run of the same portal frame
(corner 75.2 kNm, span 83.4 kNm, reactions 105.75 kN, deflection 7.4 mm).
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
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-engineer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fortia  # noqa: E402
import ilektrologika  # noqa: E402
import michanologika  # noqa: E402
import plaisio  # noqa: E402


class FortiaCombosTests(unittest.TestCase):
    def test_uls_single_variable(self):
        r = fortia.combos(15.0, 10.0, "A")
        self.assertAlmostEqual(r["OKA_6_10"]["timi"], 1.35 * 15 + 1.5 * 10)
        self.assertAlmostEqual(r["OKL_xaraktiristikos"], 25.0)
        self.assertAlmostEqual(r["OKL_syxnos"], 15 + 0.5 * 10)
        self.assertAlmostEqual(r["OKL_oionei_monimos"], 15 + 0.3 * 10)
        self.assertAlmostEqual(r["seismikos_G_psi2Q"], 15 + 0.3 * 10)

    def test_uls_two_variables_picks_worst_lead(self):
        # G=10, QA=5 (psi0 0.7), QH=2 (psi0 0.0). Leading A: 13.5+7.5+0 = 21.
        # Leading H: 13.5+3+1.5*0.7*5 = 21.75. The engine must report 21.75.
        r = fortia.combos(10.0, 5.0, "A", q2=2.0, category2="H")
        self.assertAlmostEqual(r["OKA_6_10"]["timi"], 21.75)
        self.assertEqual(r["OKA_6_10"]["kyria"], "H")
        self.assertEqual(len(r["OKA_oles_oi_periptoseis"]), 2)

    def test_sls_two_variables_apply_psi_to_accompanying(self):
        # EN 1990 6.14b and 6.15b with G=10, QA=5 (psi 0.7/0.5/0.3), QH=2 (0/0/0).
        # Characteristic, lead A: 10+5+0*2 = 15. Lead H: 10+2+0.7*5 = 15.5.
        # Frequent, lead A: 10+0.5*5+0*2 = 12.5. Lead H: 10+0*2+0.3*5 = 11.5.
        # The accompanying action must carry its psi factor, never enter full.
        r = fortia.combos(10.0, 5.0, "A", q2=2.0, category2="H")
        self.assertAlmostEqual(r["OKL_xaraktiristikos"], 15.5)
        self.assertEqual(r["OKL_xaraktiristikos_kyria"], "H")
        self.assertAlmostEqual(r["OKL_syxnos"], 12.5)
        self.assertEqual(r["OKL_syxnos_kyria"], "A")
        self.assertAlmostEqual(r["OKL_oionei_monimos"], 10 + 0.3 * 5)
        self.assertAlmostEqual(r["seismikos_G_psi2Q"], 10 + 0.3 * 5)

    def test_storage_psi_heavier_than_residential(self):
        e = fortia.PSI["E"]
        a = fortia.PSI["A"]
        self.assertGreater(e["psi2"], a["psi2"])
        self.assertEqual(fortia.PSI["H"]["psi2"], 0.0)

    def test_rejects_negative_and_unknown(self):
        with self.assertRaises(ValueError):
            fortia.combos(-1, 5, "A")
        with self.assertRaises(ValueError):
            fortia.combos(1, 5, "XX")
        with self.assertRaises(ValueError):
            fortia.combos(1, 5, "A", q2=3.0, category2="XX")


class FortiaFasmaTests(unittest.TestCase):
    def test_zone2_soil_b_parameters(self):
        p = fortia.fasma("2", "B", 3.9)
        self.assertAlmostEqual(p["agR_g"], 0.24)
        self.assertAlmostEqual(p["ag_g"], 0.24)
        self.assertAlmostEqual(p["S"], 1.2)
        self.assertAlmostEqual(p["TC_s"], 0.5)
        self.assertAlmostEqual(p["plato_Sd_g"], 0.24 * 1.2 * 2.5 / 3.9, places=4)

    def test_importance_scales_ag(self):
        p3 = fortia.fasma("1", "A", 1.5, gamma_i="III")
        self.assertAlmostEqual(p3["ag_g"], 1.2 * 0.16, places=6)
        p1 = fortia.fasma("1", "A", 1.5, gamma_i="I")
        self.assertAlmostEqual(p1["ag_g"], 0.8 * 0.16, places=6)

    def test_spectrum_continuous_at_branch_points(self):
        ag, s, tb, tc, td, q = 0.24, 1.2, 0.15, 0.5, 2.0, 3.9
        plateau = ag * s * 2.5 / q
        self.assertAlmostEqual(fortia.sd(tb, ag, s, tb, tc, td, q), plateau, places=10)
        self.assertAlmostEqual(fortia.sd(tc, ag, s, tb, tc, td, q), plateau, places=10)
        left_td = fortia.sd(td - 1e-9, ag, s, tb, tc, td, q)
        right_td = fortia.sd(td + 1e-9, ag, s, tb, tc, td, q)
        self.assertAlmostEqual(left_td, right_td, places=6)

    def test_beta_floor_engages_far_tail(self):
        # Big q pushes the curve under the floor: Sd must clamp at beta*ag.
        ag = 0.24
        val = fortia.sd(4.0, ag, 1.0, 0.15, 0.4, 2.0, 8.0)
        self.assertAlmostEqual(val, fortia.BETA * ag, places=10)

    def test_zero_period_is_two_thirds_ag_s_at_high_q(self):
        # At T=0 the branch gives ag*S*2/3 regardless of q.
        for q in (1.5, 3.9, 6.0):
            self.assertAlmostEqual(fortia.sd(0.0, 0.24, 1.2, 0.15, 0.5, 2.0, q),
                                   0.24 * 1.2 * 2 / 3, places=10)

    def test_td_override_applies(self):
        p = fortia.fasma("2", "B", 3.9, td_override=2.5)
        self.assertAlmostEqual(p["TD_s"], 2.5)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            fortia.fasma("9", "B", 3.9)
        with self.assertRaises(ValueError):
            fortia.fasma("2", "Z", 3.9)
        with self.assertRaises(ValueError):
            fortia.fasma("2", "B", 3.9, gamma_i="V")
        with self.assertRaises(ValueError):
            fortia.sd(0.5, 0.24, 1.2, 0.15, 0.5, 2.0, 0.5)
        with self.assertRaises(ValueError):
            fortia.sd(-0.1, 0.24, 1.2, 0.15, 0.5, 2.0, 2.0)

    def test_pinakas_rows_match_sd(self):
        p = fortia.fasma("3", "C", 3.0)
        rows = fortia.fasma_pinakas(p, periods=[0.1, 0.5, 1.0])
        for row in rows:
            expect = fortia.sd(row["T_s"], p["ag_g"], p["S"], p["TB_s"], p["TC_s"],
                               p["TD_s"], p["q"])
            self.assertAlmostEqual(row["Sd_g"], round(expect, 4))


class PlaisioDokosTests(unittest.TestCase):
    def test_simply_supported_udl_closed_form(self):
        r = plaisio.dokos("amfiereisti", 6.0, w=10.0, diatomi="IPE330")
        self.assertAlmostEqual(r["M_max_kNm"], 45.0)
        self.assertAlmostEqual(r["V_max_kN"], 30.0)
        ei = plaisio.E_STEEL * 11770e-8
        self.assertAlmostEqual(r["velos_mm"], round(5 * 10 * 6 ** 4 / (384 * ei) * 1000, 2))

    def test_fixed_beam_end_moment_governs(self):
        r = plaisio.dokos("paktomeni", 6.0, w=12.0)
        self.assertAlmostEqual(r["M_max_kNm"], round(12 * 36 / 12, 2))
        self.assertEqual(r["thesi_M_max"], "πάκτωση")

    def test_cantilever(self):
        r = plaisio.dokos("provolos", 2.0, w=10.0, p=5.0)
        self.assertAlmostEqual(r["M_max_kNm"], 10 * 4 / 2 + 5 * 2)
        self.assertAlmostEqual(r["V_max_kN"], 10 * 2 + 5)

    def test_utilization_against_plastic_resistance(self):
        r = plaisio.dokos("amfiereisti", 6.0, w=35.25, diatomi="IPE330", xalyvas="S275")
        m_pl = 804.3e-6 * 275e3
        self.assertAlmostEqual(r["Mpl_Rd_kNm"], round(m_pl, 1))
        self.assertAlmostEqual(r["ekmetalefsi"], round((35.25 * 36 / 8) / m_pl, 3))

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            plaisio.dokos("amfiereisti", 0, w=5)
        with self.assertRaises(ValueError):
            plaisio.dokos("amfiereisti", 6, w=0, p=0)
        with self.assertRaises(ValueError):
            plaisio.dokos("tokso", 6, w=5)
        with self.assertRaises(ValueError):
            plaisio.dokos("amfiereisti", 6, w=5, diatomi="IPE999")
        with self.assertRaises(ValueError):
            plaisio.dokos("amfiereisti", 6, w=5, xalyvas="S999")


class PlaisioFrameTests(unittest.TestCase):
    """The portal worked example, cross verified against an independent
    PyNite FE run: corner 75.2, span 83.4, V 105.75, deflection 7.4 mm."""

    def frame(self):
        return plaisio.plaisio(6.0, 3.5, 35.25, "IPE330", "HEB240", "S275", w_sls=25.0)

    def test_statics_identity_closes(self):
        r = self.frame()
        self.assertAlmostEqual(abs(r["M_gonias_kNm"]) + abs(r["M_anoigmatos_kNm"]),
                               r["taftotita_wL2_8_kNm"], places=1)
        self.assertAlmostEqual(r["taftotita_wL2_8_kNm"], round(35.25 * 36 / 8, 2))

    def test_matches_independent_fe_run(self):
        r = self.frame()
        self.assertAlmostEqual(r["M_gonias_kNm"], 75.19, delta=0.5)
        self.assertAlmostEqual(r["M_anoigmatos_kNm"], 83.44, delta=0.5)
        self.assertAlmostEqual(r["V_vasis_kN"], 105.75, places=2)
        self.assertAlmostEqual(r["velos_OKL_mm"], 7.36, delta=0.2)

    def test_stiffness_ratio(self):
        r = self.frame()
        self.assertAlmostEqual(r["k"], (11770 * 3.5) / (11260 * 6.0), places=4)

    def test_horizontal_thrust_from_corner_moment(self):
        r = self.frame()
        self.assertAlmostEqual(r["H_vasis_kN"], round(abs(r["M_gonias_kNm"]) / 3.5, 2), delta=0.02)

    def test_stiffer_columns_draw_more_corner_moment(self):
        soft = plaisio.plaisio(6.0, 3.5, 35.25, "IPE330", "HEB200")
        stiff = plaisio.plaisio(6.0, 3.5, 35.25, "IPE330", "HEB320")
        self.assertGreater(stiff["M_gonias_kNm"], soft["M_gonias_kNm"])

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            plaisio.plaisio(0, 3.5, 10, "IPE330", "HEB240")
        with self.assertRaises(ValueError):
            plaisio.plaisio(6, 3.5, -1, "IPE330", "HEB240")

    def test_pynite_cross_check_absent_or_close(self):
        r = self.frame()
        cross = plaisio.pynite_cross_check(r)
        if cross is None:
            self.skipTest("no PyNiteFEA in-process and no engineering venv; closed form stands alone")
        self.assertLess(cross["apoklisi_gonias_pct"], 5.0)
        self.assertLess(cross["apoklisi_anoigmatos_pct"], 5.0)


class PlaisioEngineBridgeTests(unittest.TestCase):
    """The PyNite cross check runs in an isolated engineering venv, reached over
    a subprocess, so PyNiteFEA's numpy >= 2.4 never pollutes the base install
    (whisper and numba pin numpy <= 2.3). These cover venv discovery and the
    graceful-absence path deterministically, without needing the venv to exist.
    The live round trip is covered by test_pynite_cross_check_absent_or_close on
    any machine that has the engineering venv."""

    def test_engine_python_honours_override(self):
        with tempfile.TemporaryDirectory() as d:
            binpy = Path(d) / "bin" / "python"
            binpy.parent.mkdir(parents=True)
            binpy.write_text("")
            with mock.patch.dict(os.environ, {"GREEK_ENGINEER_VENV": d}):
                self.assertEqual(plaisio._engine_python(), str(binpy))

    def test_engine_python_none_for_empty_override(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"GREEK_ENGINEER_VENV": d}):
            self.assertIsNone(plaisio._engine_python())

    def test_bridge_returns_none_without_engine(self):
        r = plaisio.plaisio(6.0, 3.5, 35.25, "IPE330", "HEB240", "S275")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"GREEK_ENGINEER_VENV": d}):
            self.assertIsNone(plaisio._bridge_cross_check(r, "S275"))


class IlektrologikaTests(unittest.TestCase):
    def test_three_phase_worked_example(self):
        r = ilektrologika.kyklonoma(10000, 3, 0.95, 28, 4.0, 16)
        self.assertAlmostEqual(r["Ib_A"], 15.19, places=2)
        self.assertEqual(r["syntonismos"]["apotelesma"], "ΙΚΑΝΟΠΟΙΕΙΤΑΙ")
        self.assertEqual(r["ptosi_tasis"]["apotelesma"], "ΙΚΑΝΟΠΟΙΕΙΤΑΙ")
        self.assertLess(r["ptosi_tasis"]["pososto"], 1.5)

    def test_single_phase_ib(self):
        r = ilektrologika.kyklonoma(3500, 1, 1.0, 8, 2.5, 16)
        self.assertAlmostEqual(r["Ib_A"], round(3500 / 230, 2))

    def test_coordination_fails_when_breaker_too_small(self):
        r = ilektrologika.kyklonoma(10000, 1, 1.0, 10, 10.0, 32)
        # Ib = 43.5 A > In = 32: must fail and suggest the next standard size.
        self.assertEqual(r["syntonismos"]["apotelesma"], "ΑΠΟΤΥΓΧΑΝΕΙ")
        self.assertIn("50", r["protasi"])

    def test_suggestion_never_exceeds_iz(self):
        # Ib = 43.5 A on a 6 mm2 line (Iz 36): no standard breaker fits between
        # Ib and Iz, so the advice must be a bigger conductor, never a breaker
        # above the cable's ampacity.
        r = ilektrologika.kyklonoma(10000, 1, 1.0, 10, 6.0, 32)
        self.assertEqual(r["syntonismos"]["apotelesma"], "ΑΠΟΤΥΓΧΑΝΕΙ")
        self.assertIn("διατομή", r["protasi"])

    def test_coordination_fails_when_breaker_exceeds_iz(self):
        r = ilektrologika.kyklonoma(2000, 1, 1.0, 10, 1.5, 20)
        # In = 20 > Iz(1.5mm2) = 15.5: bigger conductor, not bigger breaker.
        self.assertEqual(r["syntonismos"]["apotelesma"], "ΑΠΟΤΥΓΧΑΝΕΙ")
        self.assertIn("διατομή", r["protasi"])

    def test_explicit_iz_overrides_table(self):
        r = ilektrologika.kyklonoma(2000, 1, 1.0, 10, 1.5, 20, iz=27.0)
        self.assertEqual(r["Iz_A"], 27.0)
        self.assertEqual(r["syntonismos"]["apotelesma"], "ΙΚΑΝΟΠΟΙΕΙΤΑΙ")

    def test_unknown_section_requires_explicit_iz(self):
        with self.assertRaises(ValueError):
            ilektrologika.kyklonoma(2000, 1, 1.0, 10, 3.0, 16)

    def test_voltage_drop_scales_with_length(self):
        short = ilektrologika.kyklonoma(3500, 1, 1.0, 10, 2.5, 16)
        long = ilektrologika.kyklonoma(3500, 1, 1.0, 20, 2.5, 16)
        self.assertAlmostEqual(long["ptosi_tasis"]["dU_V"],
                               2 * short["ptosi_tasis"]["dU_V"], places=2)

    def test_custom_drop_limit(self):
        r = ilektrologika.kyklonoma(3500, 1, 1.0, 40, 2.5, 16, orio_pct=1.0)
        self.assertEqual(r["ptosi_tasis"]["orio"], 1.0)
        self.assertEqual(r["ptosi_tasis"]["apotelesma"], "ΑΠΟΤΥΓΧΑΝΕΙ")

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            ilektrologika.kyklonoma(0, 1, 1.0, 10, 2.5, 16)
        with self.assertRaises(ValueError):
            ilektrologika.kyklonoma(1000, 2, 1.0, 10, 2.5, 16)
        with self.assertRaises(ValueError):
            ilektrologika.kyklonoma(1000, 1, 1.5, 10, 2.5, 16)


class MichanologikaTests(unittest.TestCase):
    def test_laminar_friction_is_64_over_re(self):
        f, res = michanologika.friction_factor(1000, 0.0)
        self.assertAlmostEqual(f, 0.064)
        self.assertEqual(res, 0.0)

    def test_colebrook_residual_is_zero_at_solution(self):
        f, res = michanologika.friction_factor(22565, 1.5e-6 / 0.020)
        self.assertLess(abs(res), 1e-8)
        self.assertGreater(f, 0.02)
        self.assertLess(f, 0.03)

    def test_smooth_pipe_reference_value(self):
        # Smooth pipe at Re 1e5: Colebrook gives about 0.018, a textbook anchor.
        f, _ = michanologika.friction_factor(1e5, 0.0)
        self.assertAlmostEqual(f, 0.018, delta=0.001)

    def test_heating_loop_worked_example(self):
        r = michanologika.kykloma(12000, 20, 0.020, 38, zeta=10, temp_c=70)
        self.assertAlmostEqual(r["paroxi_m3_h"], 0.527, places=3)
        self.assertAlmostEqual(r["Re"], 22565, delta=30)
        self.assertAlmostEqual(r["manometriko_m"], 0.643, delta=0.01)
        self.assertEqual(r["kathestos_rois"], "τυρβώδης")

    def test_flow_halves_when_dt_doubles(self):
        a = michanologika.kykloma(12000, 10, 0.020, 38, temp_c=70)
        b = michanologika.kykloma(12000, 20, 0.020, 38, temp_c=70)
        self.assertAlmostEqual(a["paroxi_kg_s"], 2 * b["paroxi_kg_s"], places=6)

    def test_explicit_properties_override_table(self):
        r = michanologika.kykloma(10000, 20, 0.020, 30, rho=1000.0, mu=1e-3, cp=4200.0)
        self.assertAlmostEqual(r["paroxi_kg_s"], round(10000 / (4200 * 20), 4))

    def test_unknown_temperature_refuses(self):
        with self.assertRaises(ValueError):
            michanologika.kykloma(10000, 20, 0.020, 30, temp_c=55)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            michanologika.kykloma(0, 20, 0.02, 30)
        with self.assertRaises(ValueError):
            michanologika.kykloma(1000, 20, 0.02, 30, zeta=-1)
        with self.assertRaises(ValueError):
            michanologika.friction_factor(0, 0.0)


class CliSmokeTests(unittest.TestCase):
    """The argparse wiring end to end, with JSON output parsed back."""

    def run_cli(self, module, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = module.main(argv)
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_fortia_cli_json(self):
        out = self.run_cli(fortia, ["combos", "--g", "15", "--q", "10", "--category", "A", "--json"])
        self.assertAlmostEqual(json.loads(out)["OKA_6_10"]["timi"], 35.25)
        out = self.run_cli(fortia, ["fasma", "--zoni", "2", "--edafos", "B", "--q", "3.9",
                                    "--pinakas", "--json"])
        data = json.loads(out)
        self.assertIn("pinakas", data)
        self.assertAlmostEqual(data["plato_Sd_g"], 0.1846, places=4)

    def test_plaisio_cli_json(self):
        out = self.run_cli(plaisio, ["plaisio", "--l", "6", "--h", "3.5", "--w", "35.25",
                                     "--dokos", "IPE330", "--stylos", "HEB240", "--json"])
        data = json.loads(out)
        self.assertAlmostEqual(data["V_vasis_kN"], 105.75)

    def test_ilektrologika_cli_json(self):
        out = self.run_cli(ilektrologika, ["kyklonoma", "--p", "10000", "--faseis", "3",
                                           "--cosf", "0.95", "--l", "28", "--s", "4",
                                           "--in-a", "16", "--json"])
        self.assertAlmostEqual(json.loads(out)["Ib_A"], 15.19, places=2)

    def test_michanologika_cli_json(self):
        out = self.run_cli(michanologika, ["kykloma", "--q", "12000", "--dt", "20",
                                           "--d", "0.020", "--l", "38", "--zeta", "10", "--json"])
        self.assertAlmostEqual(json.loads(out)["paroxi_m3_h"], 0.527, places=3)

    def test_reference_table_commands(self):
        for module, argv in ((fortia, ["psi"]), (fortia, ["zones"]),
                             (plaisio, ["diatomes"]), (ilektrologika, ["pinakes"]),
                             (ilektrologika, ["yde"]), (michanologika, ["ides"])):
            out = self.run_cli(module, argv)
            self.assertTrue(out.strip())


if __name__ == "__main__":
    unittest.main()
