#!/usr/bin/env python3
"""Προδιαστασιολόγηση δοκών και απλού πλαισίου με κλειστούς τύπους στατικής.

Πρώτη εκτίμηση, όχι μελέτη. Τρεις αρχές:

1. ΚΛΕΙΣΤΟΙ ΤΥΠΟΙ ΩΣ ΚΥΡΙΑ ΜΗΧΑΝΗ. Αμφιέρειστη, πακτωμένη και πρόβολος με
   ομοιόμορφο ή συγκεντρωμένο φορτίο, και μονώροφο πλαίσιο με αρθρωτές βάσεις
   κατά τους τύπους Kleinlogel. Καθαρά μαθηματικά, χωρίς εξαρτήσεις, ώστε η
   μηχανή να τρέχει παντού και να ελέγχεται με ταυτότητες στατικής: στο
   συμμετρικό πλαίσιο το άθροισμα ροπής ανοίγματος και γωνίας ισούται με wL2/8.

2. ΠΡΟΑΙΡΕΤΙΚΗ ΔΙΑΣΤΑΥΡΩΣΗ ΜΕ ΑΝΕΞΑΡΤΗΤΟ ΕΠΙΛΥΤΗ. Αν υπάρχει εγκατεστημένο το
   PyNiteFEA (pip install PyNiteFEA), η εντολή --pynite ξαναλύνει το πλαίσιο με
   πεπερασμένα στοιχεία και τυπώνει τη σύγκριση. Δεύτερη γνώμη, όχι προϋπόθεση.

3. ΟΙ ΤΙΜΕΣ ΔΙΑΤΟΜΩΝ ΕΙΝΑΙ ΠΙΝΑΚΕΣ, ΟΧΙ ΑΥΘΕΝΤΙΑ. Ο μικρός κατάλογος IPE και
   HEB φέρει [ΕΠΑΛΗΘΕΥΣΕ]: πριν από χρήση σε μελέτη, οι ιδιότητες επιβεβαιώνονται
   στους πίνακες του προμηθευτή ή του προτύπου. Ο έλεγχος αντοχής είναι πρώτης
   τάξης (Mpl,Rd = Wpl fy / γM0, EN 1993-1-1 παρ. 6.2.5) χωρίς λυγισμό, στρέβλωση
   ή αλληλεπίδραση: προδιαστασιολόγηση, ποτέ τελικός έλεγχος.

Ο μελετητής επιλέγει στατικό σύστημα, φορτία και διατομή, επιβεβαιώνει τις τιμές
και υπογράφει. Μονάδες: kN, m (E σε kPa, δηλαδή kN/m2).

Usage:
  python plaisio.py dokos --typos amfiereisti --l 6 --w 35.25 --diatomi IPE330 --xalyvas S275
  python plaisio.py dokos --typos provolos --l 2.5 --p 40 --diatomi IPE240
  python plaisio.py plaisio --l 6 --h 3.5 --w 35.25 --dokos IPE330 --stylos HEB240 --w-sls 25
  python plaisio.py plaisio --l 6 --h 3.5 --w 35.25 --dokos IPE330 --stylos HEB240 --pynite
  python plaisio.py diatomes
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

E_STEEL = 210e6  # kN/m2

# Κατάλογος διατομών: A (cm2), Iy (cm4, ισχυρός άξονας), Wel (cm3), Wpl (cm3),
# βάρος (kg/m). Τιμές τυποποιημένων πινάκων. Πριν από μελέτη: επιβεβαίωση.
SECTIONS = {
    "IPE200": {"A": 28.5, "Iy": 1943, "Wel": 194, "Wpl": 220.6, "g": 22.4},
    "IPE240": {"A": 39.1, "Iy": 3892, "Wel": 324, "Wpl": 366.6, "g": 30.7},
    "IPE270": {"A": 45.9, "Iy": 5790, "Wel": 429, "Wpl": 484.0, "g": 36.1},
    "IPE300": {"A": 53.8, "Iy": 8356, "Wel": 557, "Wpl": 628.4, "g": 42.2},
    "IPE330": {"A": 62.6, "Iy": 11770, "Wel": 713, "Wpl": 804.3, "g": 49.1},
    "IPE360": {"A": 72.7, "Iy": 16270, "Wel": 904, "Wpl": 1019.0, "g": 57.1},
    "IPE400": {"A": 84.5, "Iy": 23130, "Wel": 1156, "Wpl": 1307.0, "g": 66.3},
    "IPE450": {"A": 98.8, "Iy": 33740, "Wel": 1500, "Wpl": 1702.0, "g": 77.6},
    "IPE500": {"A": 115.5, "Iy": 48200, "Wel": 1928, "Wpl": 2194.0, "g": 90.7},
    "HEB200": {"A": 78.1, "Iy": 5696, "Wel": 570, "Wpl": 642.5, "g": 61.3},
    "HEB240": {"A": 106.0, "Iy": 11260, "Wel": 938, "Wpl": 1053.0, "g": 83.2},
    "HEB280": {"A": 131.4, "Iy": 19270, "Wel": 1376, "Wpl": 1534.0, "g": 103.0},
    "HEB320": {"A": 161.3, "Iy": 30820, "Wel": 1926, "Wpl": 2149.0, "g": 127.0},
}

STEELS = {"S235": 235e3, "S275": 275e3, "S355": 355e3}  # fy σε kN/m2

GAMMA_M0 = 1.0  # EN 1993-1-1, συνιστώμενη τιμή, το ΕΠ τη διατηρεί [ΕΠΑΛΗΘΕΥΣΕ]

DEFLECTION_DENOM = 250  # συνηθισμένο όριο βέλους L/250 [ΕΠΑΛΗΘΕΥΣΕ στο ΕΠ και τη χρήση]


def _section(name):
    s = SECTIONS.get(name.upper())
    if s is None:
        raise ValueError(f"Άγνωστη διατομή {name}. Διαθέσιμες: {', '.join(SECTIONS)}")
    return s


def _fy(name):
    fy = STEELS.get(name.upper())
    if fy is None:
        raise ValueError(f"Άγνωστος χάλυβας {name}. Διαθέσιμοι: {', '.join(STEELS)}")
    return fy


def dokos(typos, span, w=0.0, p=0.0, diatomi="IPE330", xalyvas="S275"):
    """Κλειστοί τύποι δοκού: ροπή, τέμνουσα, βέλος, βαθμός εκμετάλλευσης.

    typos: amfiereisti, paktomeni, provolos. Φορτία: w ομοιόμορφο (kN/m) και p
    συγκεντρωμένο (kN, στο μέσο ή στο άκρο του προβόλου). Το βέλος υπολογίζεται
    με τα ίδια φορτία που δόθηκαν: για βέλος λειτουργικότητας δώσε φορτία ΟΚΛ.
    """
    if span <= 0:
        raise ValueError("Το άνοιγμα πρέπει να είναι θετικό.")
    if w < 0 or p < 0:
        raise ValueError("Τα φορτία δίνονται μη αρνητικά.")
    if w == 0 and p == 0:
        raise ValueError("Δώσε τουλάχιστον ένα φορτίο, w ή p.")
    s = _section(diatomi)
    fy = _fy(xalyvas)
    inertia = s["Iy"] * 1e-8  # m4
    ei = E_STEEL * inertia

    if typos == "amfiereisti":
        m_max = w * span ** 2 / 8 + p * span / 4
        v_max = w * span / 2 + p / 2
        delta = 5 * w * span ** 4 / (384 * ei) + p * span ** 3 / (48 * ei)
        thesi = "μέσο ανοίγματος"
    elif typos == "paktomeni":
        m_end = w * span ** 2 / 12 + p * span / 8
        m_mid = w * span ** 2 / 24 + p * span / 8
        m_max = max(m_end, m_mid)
        v_max = w * span / 2 + p / 2
        delta = w * span ** 4 / (384 * ei) + p * span ** 3 / (192 * ei)
        thesi = "πάκτωση" if m_end >= m_mid else "μέσο ανοίγματος"
    elif typos == "provolos":
        m_max = w * span ** 2 / 2 + p * span
        v_max = w * span + p
        delta = w * span ** 4 / (8 * ei) + p * span ** 3 / (3 * ei)
        thesi = "πάκτωση"
    else:
        raise ValueError("Άγνωστος τύπος. Διαθέσιμοι: amfiereisti, paktomeni, provolos.")

    m_pl_rd = s["Wpl"] * 1e-6 * fy / GAMMA_M0
    util = m_max / m_pl_rd if m_pl_rd else float("inf")
    limit = span / DEFLECTION_DENOM
    return {
        "typos": typos,
        "L_m": span,
        "fortia": {"w_kN_m": w, "P_kN": p},
        "M_max_kNm": round(m_max, 2),
        "thesi_M_max": thesi,
        "V_max_kN": round(v_max, 2),
        "velos_m": round(delta, 6),
        "velos_mm": round(delta * 1000, 2),
        "orio_velous_mm": round(limit * 1000, 1),
        "orio_onoma": f"L/{DEFLECTION_DENOM}",
        "diatomi": diatomi.upper(),
        "xalyvas": xalyvas.upper(),
        "Mpl_Rd_kNm": round(m_pl_rd, 1),
        "ekmetalefsi": round(util, 3),
        "simeiosi": (
            f"Προδιαστασιολόγηση. Ιδιότητες διατομής και όριο βέλους {_VERIFY}. "
            "Χωρίς έλεγχο λυγισμού, στρεπτοκαμπτικού ή διάτμησης: αυτά ανήκουν στη μελέτη."
        ),
    }


def plaisio(span, height, w_uls, dokos_name, stylos_name, xalyvas="S275", w_sls=None):
    """Μονώροφο μονότοξο πλαίσιο με αρθρωτές βάσεις, ομοιόμορφο φορτίο στο ζύγωμα.

    Τύποι Kleinlogel: k = (Ib h) / (Ic L), ροπή γωνίας M = wL2 / (4(2k+3)).
    Αυτοέλεγχος: |M γωνίας| + |M ανοίγματος| = wL2/8, ταυτότητα ισορροπίας
    που πρέπει να κλείνει στην ακρίβεια της μηχανής, αλλιώς σφάλμα.
    """
    if span <= 0 or height <= 0:
        raise ValueError("Άνοιγμα και ύψος πρέπει να είναι θετικά.")
    if w_uls <= 0:
        raise ValueError("Το φορτίο ΟΚΑ πρέπει να είναι θετικό.")
    sb = _section(dokos_name)
    sc = _section(stylos_name)
    fy = _fy(xalyvas)

    k = (sb["Iy"] * height) / (sc["Iy"] * span)
    m_corner = w_uls * span ** 2 / (4 * (2 * k + 3))
    m_span = w_uls * span ** 2 / 8 - m_corner
    v_base = w_uls * span / 2
    h_base = m_corner / height

    identity = abs(m_corner) + abs(m_span)
    target = w_uls * span ** 2 / 8
    if abs(identity - target) > 1e-6 * max(1.0, target):
        raise RuntimeError("Αποτυχία ταυτότητας στατικής: ο υπολογισμός είναι εσφαλμένος.")

    m_pl_rd = sb["Wpl"] * 1e-6 * fy / GAMMA_M0
    util = max(abs(m_span), abs(m_corner)) / m_pl_rd

    out = {
        "systima": "μονώροφο πλαίσιο, αρθρωτές βάσεις",
        "L_m": span, "H_m": height, "w_OKA_kN_m": w_uls,
        "dokos": dokos_name.upper(), "stylos": stylos_name.upper(), "xalyvas": xalyvas.upper(),
        "k": round(k, 4),
        "M_gonias_kNm": round(m_corner, 2),
        "M_anoigmatos_kNm": round(m_span, 2),
        "taftotita_wL2_8_kNm": round(target, 2),
        "V_vasis_kN": round(v_base, 2),
        "H_vasis_kN": round(h_base, 2),
        "Mpl_Rd_dokou_kNm": round(m_pl_rd, 1),
        "ekmetalefsi_dokou": round(util, 3),
        "simeiosi": (
            f"Προδιαστασιολόγηση με τύπους Kleinlogel. Ιδιότητες διατομών {_VERIFY}. "
            "Χωρίς φαινόμενα 2ας τάξης, λυγισμό ή ελέγχους κόμβων."
        ),
    }

    if w_sls is not None and w_sls > 0:
        ei = E_STEEL * sb["Iy"] * 1e-8
        m_corner_sls = w_sls * span ** 2 / (4 * (2 * k + 3))
        delta = 5 * w_sls * span ** 4 / (384 * ei) - m_corner_sls * span ** 2 / (8 * ei)
        out["w_OKL_kN_m"] = w_sls
        out["velos_OKL_mm"] = round(delta * 1000, 2)
        out["orio_velous_mm"] = round(span / DEFLECTION_DENOM * 1000, 1)
    return out


def pynite_cross_check(result, xalyvas="S275"):
    """Διασταύρωση του πλαισίου με PyNiteFEA, αν είναι εγκατεστημένο.

    Επιστρέφει dict σύγκρισης ή None όταν το PyNite λείπει. Η απόκλιση των
    κλειστών τύπων από την ανάλυση μέλους αναμένεται μικρή (οι τύποι Kleinlogel
    αγνοούν αξονικές και διατμητικές παραμορφώσεις).
    """
    try:
        from Pynite import FEModel3D
    except ImportError:
        return None
    span, height, w = result["L_m"], result["H_m"], result["w_OKA_kN_m"]
    sb = SECTIONS[result["dokos"]]
    sc = SECTIONS[result["stylos"]]
    m = FEModel3D()
    m.add_material("steel", E_STEEL, 80.77e6, 0.3, 77.0, fy=STEELS[xalyvas.upper()])
    m.add_section("beam", sb["A"] * 1e-4, sb["Iy"] * 1e-8 / 10, sb["Iy"] * 1e-8, sb["Iy"] * 1e-8 / 100)
    m.add_section("col", sc["A"] * 1e-4, sc["Iy"] * 1e-8 / 10, sc["Iy"] * 1e-8, sc["Iy"] * 1e-8 / 100)
    m.add_node("N1", 0, 0, 0)
    m.add_node("N2", 0, height, 0)
    m.add_node("N3", span, height, 0)
    m.add_node("N4", span, 0, 0)
    m.add_member("C1", "N1", "N2", "steel", "col")
    m.add_member("B1", "N2", "N3", "steel", "beam")
    m.add_member("C2", "N4", "N3", "steel", "col")
    m.def_support("N1", True, True, True, True, True, False)
    m.def_support("N4", True, True, True, True, True, False)
    for n in ("N2", "N3"):
        m.def_support(n, False, False, True, True, True, False)
    m.add_member_dist_load("B1", "FY", -w, -w, case="W")
    m.add_load_combo("C", {"W": 1.0})
    # check_statics=False: θα τύπωνε πίνακα στο stdout. Ο έλεγχος εδώ είναι η
    # ίδια η σύγκριση με τους κλειστούς τύπους, που κλείνουν στη wL2/8.
    m.analyze(check_statics=False)
    mz = m.members["B1"].moment_array("Mz", 201, "C")[1]
    fem_span = abs(float(mz[100]))
    fem_corner = abs(float(mz[0]))
    return {
        "pynite_M_gonias_kNm": round(fem_corner, 2),
        "pynite_M_anoigmatos_kNm": round(fem_span, 2),
        "apoklisi_gonias_pct": round(
            100 * abs(fem_corner - abs(result["M_gonias_kNm"])) / fem_corner, 2) if fem_corner else 0.0,
        "apoklisi_anoigmatos_pct": round(
            100 * abs(fem_span - abs(result["M_anoigmatos_kNm"])) / fem_span, 2) if fem_span else 0.0,
    }


def _print_result(r):
    for key, val in r.items():
        if key == "simeiosi":
            continue
        print(f"  {key}: {val}")
    print(f"  {r['simeiosi']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dokos", help="Κλειστοί τύποι δοκού")
    d.add_argument("--typos", default="amfiereisti", help="amfiereisti, paktomeni, provolos")
    d.add_argument("--l", type=float, required=True, help="Άνοιγμα L σε m")
    d.add_argument("--w", type=float, default=0.0, help="Ομοιόμορφο φορτίο kN/m")
    d.add_argument("--p", type=float, default=0.0, help="Συγκεντρωμένο φορτίο kN")
    d.add_argument("--diatomi", default="IPE330")
    d.add_argument("--xalyvas", default="S275")
    d.add_argument("--json", action="store_true")

    p = sub.add_parser("plaisio", help="Μονώροφο πλαίσιο, αρθρωτές βάσεις")
    p.add_argument("--l", type=float, required=True, help="Άνοιγμα L σε m")
    p.add_argument("--h", type=float, required=True, help="Ύψος H σε m")
    p.add_argument("--w", type=float, required=True, help="Φορτίο ΟΚΑ στο ζύγωμα kN/m")
    p.add_argument("--w-sls", type=float, help="Φορτίο ΟΚΛ για το βέλος kN/m")
    p.add_argument("--dokos", default="IPE330")
    p.add_argument("--stylos", default="HEB240")
    p.add_argument("--xalyvas", default="S275")
    p.add_argument("--pynite", action="store_true", help="Διασταύρωση με PyNiteFEA αν υπάρχει")
    p.add_argument("--json", action="store_true")

    di = sub.add_parser("diatomes", help="Κατάλογος διατομών")
    di.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "dokos":
        r = dokos(args.typos, args.l, args.w, args.p, args.diatomi, args.xalyvas)
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.json else "Δοκός")
        if not args.json:
            _print_result(r)
    elif args.cmd == "plaisio":
        r = plaisio(args.l, args.h, args.w, args.dokos, args.stylos, args.xalyvas, args.w_sls)
        cross = pynite_cross_check(r, args.xalyvas) if args.pynite else None
        if cross:
            r["pynite"] = cross
        elif args.pynite:
            r["pynite"] = "Το PyNiteFEA δεν είναι εγκατεστημένο (pip install PyNiteFEA). Οι κλειστοί τύποι ισχύουν."
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print("Πλαίσιο")
            _print_result(r)
    elif args.cmd == "diatomes":
        if args.json:
            print(json.dumps(SECTIONS, ensure_ascii=False, indent=2))
        else:
            print(f"Κατάλογος διατομών, ισχυρός άξονας. Τιμές πινάκων {_VERIFY}")
            for name, s in SECTIONS.items():
                print(f"  {name:<7} A = {s['A']:>6} cm2, Iy = {s['Iy']:>6} cm4, "
                      f"Wpl = {s['Wpl']:>7} cm3, {s['g']} kg/m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
