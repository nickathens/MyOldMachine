#!/usr/bin/env python3
"""Έλεγχος διακλάδωσης κατά το πρότυπο ΕΛΟΤ HD 384: συντονισμός και πτώση τάσης.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΜΗΧΑΝΗ (kyklonoma). Ρεύμα σχεδιασμού Ib από ισχύ και cosφ,
   έλεγχος συντονισμού Ib <= In <= Iz, και πτώση τάσης με ωμικό και επαγωγικό
   μέρος. Για αυτόματους διακόπτες EN 60898 ισχύει I2 = 1.45 In, οπότε ο
   έλεγχος I2 <= 1.45 Iz καλύπτεται αυτόματα όταν In <= Iz. Καθαρή αριθμητική.

2. ΚΑΤΑΛΟΓΟΣ ΤΙΜΩΝ (pinakes). Σκαλωσιά, όχι αυθεντία. Η ενσωματωμένη στήλη
   επιτρεπόμενων εντάσεων Iz αντιστοιχεί σε ΜΙΑ μόνο περίπτωση, μέθοδος
   αναφοράς B1, αγωγοί χαλκού με μόνωση PVC, τρεις φορτισμένοι αγωγοί, 30
   βαθμοί περιβάλλοντος, χωρίς ομαδοποίηση. Κάθε άλλη όδευση, μόνωση,
   θερμοκρασία ή ομαδοποίηση αλλάζει την Iz. Όλες οι τιμές φέρουν [ΕΠΑΛΗΘΕΥΣΕ]
   έναντι των πινάκων του ισχύοντος προτύπου, και η μηχανή δέχεται πάντοτε
   ρητή --iz που υπερισχύει του πίνακα.

Ο διαχωρισμός ευθύνης: το script υπολογίζει. Ο εγκαταστάτης ή ο μελετητής
επιβεβαιώνει μέθοδο εγκατάστασης, συντελεστές διόρθωσης, όριο πτώσης τάσης για
τη συγκεκριμένη τροφοδοσία, και υπογράφει την ΥΔΕ.

Usage:
  python ilektrologika.py kyklonoma --p 10000 --faseis 3 --cosf 0.95 --l 28 --s 4 --in-a 16
  python ilektrologika.py kyklonoma --p 3500 --faseis 1 --l 8 --s 2.5 --in-a 16 --orio 3
  python ilektrologika.py kyklonoma --p 7400 --faseis 1 --l 15 --s 6 --in-a 32 --iz 41
  python ilektrologika.py pinakes
  python ilektrologika.py yde
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import math
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

U_1F = 230.0  # V, ονομαστική τάση φάσης
U_3F = 400.0  # V, ονομαστική πολική τάση

# Επιτρεπόμενες εντάσεις Iz (A) για την περίπτωση αναφοράς: μέθοδος B1, Cu/PVC,
# τρεις φορτισμένοι αγωγοί, 30 C, χωρίς ομαδοποίηση. Κλειδί: διατομή mm2.
IZ_B1_PVC_CU_3 = {
    1.5: 15.5, 2.5: 21.0, 4.0: 28.0, 6.0: 36.0,
    10.0: 50.0, 16.0: 66.0, 25.0: 88.0, 35.0: 110.0,
}

# Τυποποιημένα μεγέθη αυτόματων διακοπτών EN 60898.
STANDARD_IN = [6, 10, 16, 20, 25, 32, 40, 50, 63, 80, 100]

RHO_CU = 0.0225   # Ω mm2 / m, χαλκός σε θερμοκρασία λειτουργίας [ΕΠΑΛΗΘΕΥΣΕ]
X_PER_M = 0.00008  # Ω / m, τυπική επαγωγική αντίδραση καλωδίου [ΕΠΑΛΗΘΕΥΣΕ]

DROP_LIMIT_DEFAULT = 4.0  # %, σύνηθες όριο για τροφοδοσία από δίκτυο διανομής [ΕΠΑΛΗΘΕΥΣΕ]

# Μέγιστα διαστήματα επανελέγχου ΥΔΕ κατά κατηγορία χώρου (έτη).
YDE_INTERVALS = [
    {"xoros": "Κατοικίες και ανάλογοι χώροι", "eti": 14},
    {"xoros": "Επαγγελματικοί χώροι χωρίς εύφλεκτα υλικά", "eti": 7},
    {"xoros": "Επαγγελματικοί χώροι με εύφλεκτα υλικά", "eti": 2},
    {"xoros": "Χώροι ψυχαγωγίας και συνάθροισης κοινού", "eti": 1},
]


def kyklonoma(p_watt, faseis, cosf, length_m, s_mm2, in_a, iz=None, orio_pct=None):
    """Πλήρης έλεγχος διακλάδωσης: Ib, συντονισμός, πτώση τάσης.

    p_watt: ισχύς σε W. faseis: 1 ή 3. length_m: απλό μήκος όδευσης σε m.
    s_mm2: διατομή αγωγού φάσης. in_a: ονομαστικό ρεύμα διακόπτη. iz: ρητή
    επιτρεπόμενη ένταση που υπερισχύει του ενσωματωμένου πίνακα. orio_pct:
    όριο πτώσης τάσης, προεπιλογή η τιμή αναφοράς.
    """
    if p_watt <= 0 or length_m <= 0 or s_mm2 <= 0 or in_a <= 0:
        raise ValueError("Ισχύς, μήκος, διατομή και In πρέπει να είναι θετικά.")
    if faseis not in (1, 3):
        raise ValueError("Οι φάσεις είναι 1 ή 3.")
    if not 0 < cosf <= 1:
        raise ValueError("Το cosφ ανήκει στο διάστημα (0, 1].")

    if faseis == 1:
        u = U_1F
        ib = p_watt / (u * cosf)
    else:
        u = U_3F
        ib = p_watt / (math.sqrt(3) * u * cosf)

    iz_used = iz
    iz_source = "ρητή τιμή χρήστη"
    if iz_used is None:
        iz_used = IZ_B1_PVC_CU_3.get(float(s_mm2))
        iz_source = f"πίνακας αναφοράς B1/PVC/Cu/3 αγωγοί {_VERIFY}"
        if iz_used is None:
            raise ValueError(
                f"Η διατομή {s_mm2} mm2 δεν υπάρχει στον πίνακα αναφοράς. "
                "Δώσε ρητά --iz από τον πίνακα του προτύπου για την όδευσή σου.")

    coord_ok = ib <= in_a <= iz_used

    r_per_m = RHO_CU / s_mm2
    sinf = math.sqrt(max(0.0, 1 - cosf ** 2))
    z_term = r_per_m * cosf + X_PER_M * sinf
    if faseis == 1:
        du = 2 * ib * length_m * z_term
    else:
        du = math.sqrt(3) * ib * length_m * z_term
    drop_pct = du / u * 100.0
    limit = orio_pct if orio_pct is not None else DROP_LIMIT_DEFAULT
    drop_ok = drop_pct <= limit

    protasi = None
    if not coord_ok and ib > in_a:
        bigger = [n for n in STANDARD_IN if ib <= n <= iz_used]
        if bigger:
            protasi = f"Το In = {in_a} A είναι μικρότερο από το Ib. Επόμενο τυποποιημένο: {bigger[0]} A."
        else:
            protasi = ("Κανένα τυποποιημένο In δεν χωράει ανάμεσα στο Ib και την Iz: "
                       "απαιτείται μεγαλύτερη διατομή ή άλλη όδευση.")
    elif not coord_ok:
        protasi = "Το In υπερβαίνει την Iz: μεγαλύτερη διατομή ή άλλη όδευση, όχι μεγαλύτερος διακόπτης."

    return {
        "eisodos": {"P_W": p_watt, "faseis": faseis, "cosf": cosf, "L_m": length_m,
                    "S_mm2": s_mm2, "In_A": in_a, "U_V": u},
        "Ib_A": round(ib, 2),
        "Iz_A": iz_used,
        "pigi_Iz": iz_source,
        "syntonismos": {"elegxos": "Ib <= In <= Iz", "apotelesma": "ΙΚΑΝΟΠΟΙΕΙΤΑΙ" if coord_ok else "ΑΠΟΤΥΓΧΑΝΕΙ"},
        "protasi": protasi,
        "ptosi_tasis": {"dU_V": round(du, 2), "pososto": round(drop_pct, 2),
                        "orio": limit, "apotelesma": "ΙΚΑΝΟΠΟΙΕΙΤΑΙ" if drop_ok else "ΑΠΟΤΥΓΧΑΝΕΙ"},
        "simeiosi": (
            f"Τιμές ρ, x, Iz και ορίου πτώσης {_VERIFY} έναντι του ισχύοντος προτύπου "
            "και της πραγματικής όδευσης. Η ΥΔΕ υπογράφεται από αδειούχο εγκαταστάτη."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("kyklonoma", help="Έλεγχος διακλάδωσης")
    k.add_argument("--p", type=float, required=True, help="Ισχύς σε W")
    k.add_argument("--faseis", type=int, default=1, help="1 ή 3")
    k.add_argument("--cosf", type=float, default=1.0, help="Συντελεστής ισχύος")
    k.add_argument("--l", type=float, required=True, help="Απλό μήκος όδευσης σε m")
    k.add_argument("--s", type=float, required=True, help="Διατομή αγωγού σε mm2")
    k.add_argument("--in-a", type=float, required=True, help="Ονομαστικό ρεύμα διακόπτη σε A")
    k.add_argument("--iz", type=float, help="Ρητή επιτρεπόμενη ένταση Iz σε A")
    k.add_argument("--orio", type=float, help="Όριο πτώσης τάσης σε τοις εκατό")
    k.add_argument("--json", action="store_true")

    p = sub.add_parser("pinakes", help="Ενσωματωμένοι πίνακες αναφοράς")
    p.add_argument("--json", action="store_true")

    y = sub.add_parser("yde", help="Διαστήματα επανελέγχου ΥΔΕ")
    y.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "kyklonoma":
        r = kyklonoma(args.p, args.faseis, args.cosf, args.l, args.s, args.in_a, args.iz, args.orio)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            e = r["eisodos"]
            print(f"Διακλάδωση: {e['P_W'] / 1000:.1f} kW, {e['faseis']}Φ {e['U_V']:.0f} V, cosφ = {e['cosf']}")
            print(f"  Ib = {r['Ib_A']} A, In = {e['In_A']} A, Iz = {r['Iz_A']} A ({r['pigi_Iz']})")
            print(f"  Συντονισμός {r['syntonismos']['elegxos']}: {r['syntonismos']['apotelesma']}")
            if r["protasi"]:
                print(f"  Πρόταση: {r['protasi']}")
            pt = r["ptosi_tasis"]
            print(f"  Πτώση τάσης: {pt['dU_V']} V = {pt['pososto']}% (όριο {pt['orio']}%): {pt['apotelesma']}")
            print(f"  {r['simeiosi']}")
    elif args.cmd == "pinakes":
        payload = {"Iz_B1_PVC_Cu_3": IZ_B1_PVC_CU_3, "In_typopoiimena": STANDARD_IN,
                   "rho_cu": RHO_CU, "x_per_m": X_PER_M, "orio_ptosis_default": DROP_LIMIT_DEFAULT}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Πίνακας Iz αναφοράς, μέθοδος B1, Cu/PVC, 3 φορτισμένοι, 30 C {_VERIFY}")
            for s, iz in IZ_B1_PVC_CU_3.items():
                print(f"  {s:>4} mm2: {iz} A")
            print(f"Τυποποιημένα In: {', '.join(str(n) for n in STANDARD_IN)} A")
            print(f"ρ χαλκού = {RHO_CU} Ω mm2/m, x = {X_PER_M} Ω/m, όριο πτώσης {DROP_LIMIT_DEFAULT}% {_VERIFY}")
    elif args.cmd == "yde":
        if args.json:
            print(json.dumps(YDE_INTERVALS, ensure_ascii=False, indent=2))
        else:
            print(f"Μέγιστα διαστήματα επανελέγχου ΥΔΕ {_VERIFY}")
            for row in YDE_INTERVALS:
                print(f"  {row['xoros']}: {row['eti']} έτη")
            print("  Η ΥΔΕ συντάσσεται και υπογράφεται από αδειούχο ηλεκτρολόγο εγκαταστάτη.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
