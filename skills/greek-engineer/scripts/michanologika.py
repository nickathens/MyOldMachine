#!/usr/bin/env python3
"""Υδραυλική δικτύων θέρμανσης: παροχή, τριβές Colebrook, σημείο αντλίας.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΜΗΧΑΝΗ (kykloma, trivi). Καθαρή φυσική χωρίς εξαρτήσεις:
   παροχή από θερμική ισχύ και ΔΤ, αριθμός Reynolds, συντελεστής τριβής με
   την εξίσωση Colebrook σε επαναληπτική επίλυση (στρωτή ροή: f = 64/Re),
   πτώση πίεσης Darcy Weisbach με τοπικές αντιστάσεις Σζ, και μανομετρικό
   αντλίας. Η μηχανή αυτοελέγχεται: το υπόλοιπο της Colebrook στη λύση
   πρέπει να είναι πρακτικά μηδέν, αλλιώς σφάλμα.

2. ΙΔΙΟΤΗΤΕΣ ΡΕΥΣΤΟΥ ΩΣ ΔΕΔΟΜΕΝΑ (ides). Οι ενσωματωμένες ιδιότητες νερού σε
   τυπικές θερμοκρασίες φέρουν [ΕΠΑΛΗΘΕΥΣΕ]: πριν από χρήση σε μελέτη
   επιβεβαιώνονται σε πίνακες ιδιοτήτων. Δέχεται και ρητές τιμές που
   υπερισχύουν του πίνακα.

Ο διαχωρισμός ευθύνης: το script υπολογίζει τη φυσική. Ο μηχανολόγος επιλέγει
θερμοκρασίες λειτουργίας, τραχύτητα, τοπικές αντιστάσεις και εξοπλισμό, και
υπογράφει τη μελέτη. Οι απώλειες τερματικών μονάδων και λέβητα προστίθενται
χωριστά στο τελικό μανομετρικό.

Usage:
  python michanologika.py kykloma --q 12000 --dt 20 --d 0.020 --l 38 --zeta 10
  python michanologika.py kykloma --q 24000 --dt 10 --d 0.026 --l 60 --zeta 14 --thermokrasia 45
  python michanologika.py trivi --re 22565 --rel-roughness 0.000075
  python michanologika.py ides
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import math
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

G = 9.81  # m/s2

# Ιδιότητες νερού σε τυπικές θερμοκρασίες: ρ (kg/m3), μ (Pa s), cp (J/kg K).
WATER = {
    20: {"rho": 998.2, "mu": 1.002e-3, "cp": 4182.0},
    45: {"rho": 990.2, "mu": 5.96e-4, "cp": 4180.0},
    60: {"rho": 983.2, "mu": 4.67e-4, "cp": 4185.0},
    70: {"rho": 977.8, "mu": 4.04e-4, "cp": 4190.0},
    80: {"rho": 971.8, "mu": 3.55e-4, "cp": 4196.0},
}

EPS_COPPER = 1.5e-6  # m, τραχύτητα χαλκοσωλήνα [ΕΠΑΛΗΘΕΥΣΕ]

RE_LAMINAR = 2300.0
RE_TURBULENT = 4000.0


def friction_factor(re, rel_roughness):
    """Συντελεστής τριβής Darcy: Colebrook σε πλήρως τυρβώδη, 64/Re σε στρωτή.

    Στη μεταβατική περιοχή 2300 έως 4000 επιστρέφει την τιμή Colebrook και το
    σημειώνει ο καλών: η φυσική εκεί είναι αβέβαιη από τη φύση της.
    Επιστρέφει (f, υπόλοιπο εξίσωσης στη λύση).
    """
    if re <= 0:
        raise ValueError("Ο αριθμός Reynolds πρέπει να είναι θετικός.")
    if rel_roughness < 0:
        raise ValueError("Η σχετική τραχύτητα δίνεται μη αρνητική.")
    if re < RE_LAMINAR:
        return 64.0 / re, 0.0
    # Εκκίνηση με τη ρητή προσέγγιση Swamee Jain, μετά επανάληψη Colebrook.
    arg0 = rel_roughness / 3.7 + 5.74 / re ** 0.9
    f = 0.25 / (math.log10(arg0) ** 2)
    for _ in range(50):
        rhs = -2.0 * math.log10(rel_roughness / 3.7 + 2.51 / (re * math.sqrt(f)))
        f_new = 1.0 / (rhs * rhs)
        if abs(f_new - f) < 1e-12:
            f = f_new
            break
        f = f_new
    residual = 1.0 / math.sqrt(f) + 2.0 * math.log10(rel_roughness / 3.7 + 2.51 / (re * math.sqrt(f)))
    if abs(residual) > 1e-6:
        raise RuntimeError("Η επίλυση Colebrook δεν συνέκλινε: εσωτερικό σφάλμα.")
    return f, residual


def water_props(temp_c):
    """Ιδιότητες νερού από τον ενσωματωμένο πίνακα, μόνο σε διαθέσιμες θερμοκρασίες."""
    props = WATER.get(int(temp_c))
    if props is None:
        raise ValueError(
            f"Δεν υπάρχουν ενσωματωμένες ιδιότητες για {temp_c} C. "
            f"Διαθέσιμες: {', '.join(str(t) for t in WATER)}. "
            "Δώσε ρητά --rho, --mu, --cp από πίνακα ιδιοτήτων.")
    return props


def kykloma(q_watt, dt, d_m, l_m, zeta=0.0, temp_c=70, eps=EPS_COPPER,
            rho=None, mu=None, cp=None):
    """Πλήρης υπολογισμός κυκλώματος: παροχή, ταχύτητα, τριβές, μανομετρικό.

    q_watt: θερμική ισχύς σε W. dt: θερμοκρασιακή διαφορά προσαγωγής και
    επιστροφής σε K. d_m: εσωτερική διάμετρος σε m. l_m: συνολικό ευθύγραμμο
    μήκος σε m. zeta: άθροισμα τοπικών αντιστάσεων Σζ.
    """
    if q_watt <= 0 or dt <= 0 or d_m <= 0 or l_m <= 0:
        raise ValueError("Ισχύς, ΔΤ, διάμετρος και μήκος πρέπει να είναι θετικά.")
    if zeta < 0 or eps < 0:
        raise ValueError("Σζ και τραχύτητα δίνονται μη αρνητικά.")
    if rho is None or mu is None or cp is None:
        props = water_props(temp_c)
        rho = rho if rho is not None else props["rho"]
        mu = mu if mu is not None else props["mu"]
        cp = cp if cp is not None else props["cp"]

    m_dot = q_watt / (cp * dt)
    q_vol = m_dot / rho
    area = math.pi * d_m ** 2 / 4
    v = q_vol / area
    re = rho * v * d_m / mu
    f, _ = friction_factor(re, eps / d_m)
    dp = (f * l_m / d_m + zeta) * rho * v ** 2 / 2
    head = dp / (rho * G)

    # Αυτοέλεγχος ενεργειακού ισοζυγίου: η παροχή πρέπει να ξαναδίνει την ισχύ.
    q_back = m_dot * cp * dt
    if abs(q_back - q_watt) > 1e-6 * q_watt:
        raise RuntimeError("Αποτυχία ισοζυγίου ενέργειας: εσωτερικό σφάλμα.")

    kathestos = "στρωτή" if re < RE_LAMINAR else ("μεταβατική" if re < RE_TURBULENT else "τυρβώδης")
    return {
        "eisodos": {"Q_W": q_watt, "dT_K": dt, "D_m": d_m, "L_m": l_m, "Sz": zeta,
                    "thermokrasia_C": temp_c, "rho": rho, "mu": mu, "cp": cp},
        "paroxi_kg_s": round(m_dot, 4),
        "paroxi_m3_h": round(q_vol * 3600, 3),
        "taxytita_m_s": round(v, 3),
        "Re": round(re, 0),
        "kathestos_rois": kathestos,
        "f_Darcy": round(f, 5),
        "ptosi_piesis_kPa": round(dp / 1000, 2),
        "manometriko_m": round(head, 3),
        "simeio_antlias": f"{q_vol * 3600:.2f} m3/h στα {head:.2f} m",
        "simeiosi": (
            f"Ιδιότητες ρευστού και τραχύτητα {_VERIFY}. Πρόσθεσε τις απώλειες "
            "τερματικών, λέβητα και εξαρτημάτων που δεν καλύπτει το Σζ. "
            "Ταχύτητες αναφοράς: 0.5 έως 1.0 m/s σε κλάδους, έως 2 m/s σε "
            f"κεντρικές στήλες {_VERIFY}."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("kykloma", help="Υπολογισμός κυκλώματος θέρμανσης")
    k.add_argument("--q", type=float, required=True, help="Θερμική ισχύς σε W")
    k.add_argument("--dt", type=float, required=True, help="ΔΤ προσαγωγής επιστροφής σε K")
    k.add_argument("--d", type=float, required=True, help="Εσωτερική διάμετρος σε m")
    k.add_argument("--l", type=float, required=True, help="Ευθύγραμμο μήκος σε m")
    k.add_argument("--zeta", type=float, default=0.0, help="Άθροισμα τοπικών αντιστάσεων Σζ")
    k.add_argument("--thermokrasia", type=int, default=70, help="Μέση θερμοκρασία νερού σε C")
    k.add_argument("--eps", type=float, default=EPS_COPPER, help="Τραχύτητα σωλήνα σε m")
    k.add_argument("--rho", type=float, help="Ρητή πυκνότητα kg/m3")
    k.add_argument("--mu", type=float, help="Ρητό ιξώδες Pa s")
    k.add_argument("--cp", type=float, help="Ρητή ειδική θερμότητα J/kg K")
    k.add_argument("--json", action="store_true")

    t = sub.add_parser("trivi", help="Συντελεστής τριβής για δεδομένο Re")
    t.add_argument("--re", type=float, required=True)
    t.add_argument("--rel-roughness", type=float, default=0.0, help="Σχετική τραχύτητα ε/D")
    t.add_argument("--json", action="store_true")

    i = sub.add_parser("ides", help="Ενσωματωμένες ιδιότητες νερού")
    i.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "kykloma":
        r = kykloma(args.q, args.dt, args.d, args.l, args.zeta, args.thermokrasia,
                    args.eps, args.rho, args.mu, args.cp)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            e = r["eisodos"]
            print(f"Κύκλωμα {e['Q_W'] / 1000:.1f} kW, ΔΤ = {e['dT_K']} K, D = {e['D_m'] * 1000:.0f} mm, "
                  f"L = {e['L_m']} m, Σζ = {e['Sz']}")
            print(f"  Παροχή: {r['paroxi_kg_s']} kg/s = {r['paroxi_m3_h']} m3/h")
            print(f"  Ταχύτητα: {r['taxytita_m_s']} m/s, Re = {r['Re']:.0f} ({r['kathestos_rois']})")
            print(f"  f = {r['f_Darcy']}, πτώση πίεσης = {r['ptosi_piesis_kPa']} kPa")
            print(f"  Σημείο αντλίας: {r['simeio_antlias']}")
            print(f"  {r['simeiosi']}")
    elif args.cmd == "trivi":
        f, residual = friction_factor(args.re, args.rel_roughness)
        payload = {"Re": args.re, "rel_roughness": args.rel_roughness,
                   "f_Darcy": round(f, 6), "ypoloipo_Colebrook": residual}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Re = {args.re:.0f}, ε/D = {args.rel_roughness}: f = {f:.5f}")
            if args.re < RE_LAMINAR:
                print("  Στρωτή ροή: f = 64/Re.")
            elif args.re < RE_TURBULENT:
                print("  Μεταβατική περιοχή: το αποτέλεσμα έχει εγγενή αβεβαιότητα.")
    elif args.cmd == "ides":
        if args.json:
            print(json.dumps(WATER, ensure_ascii=False, indent=2))
        else:
            print(f"Ιδιότητες νερού {_VERIFY}")
            for temp, p in WATER.items():
                print(f"  {temp} C: ρ = {p['rho']} kg/m3, μ = {p['mu']} Pa s, cp = {p['cp']} J/kg K")
            print(f"Τραχύτητα χαλκού: {EPS_COPPER} m {_VERIFY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
