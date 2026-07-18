#!/usr/bin/env python3
"""Συνδυασμοί δράσεων EN 1990 και φάσμα σχεδιασμού EN 1998 (Ευρωκώδικες).

Δύο μέρη, με σαφή διαχωρισμό ευθύνης, κατά το πρότυπο του greek-law:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΜΗΧΑΝΗ (combos, fasma). Καθαρή αριθμητική πάνω στους τύπους
   των Ευρωκωδίκων: οι συνδυασμοί ΟΚΑ (EN 1990 εξ. 6.10) και ΟΚΛ
   (χαρακτηριστικός, συχνός, οιονεί μόνιμος), και το φάσμα σχεδιασμού του
   EN 1998-1 παρ. 3.2.2.5 με τους τέσσερις κλάδους και το κατώφλι β. Η
   αριθμητική είναι πάντοτε σωστή πάνω στα δεδομένα που δίνεις.

2. ΚΑΤΑΛΟΓΟΣ ΤΙΜΩΝ (psi, zones). Σκαλωσιά, όχι αυθεντία. Οι συντελεστές ψ
   (EN 1990 πίν. A1.1) και οι παράμετροι φάσματος τύπου 1 (EN 1998-1 πίν. 3.2)
   είναι οι ΣΥΝΙΣΤΩΜΕΝΕΣ τιμές CEN. Οι ζώνες σεισμικής επιτάχυνσης της Ελλάδας
   (Ζ1 0.16g, Ζ2 0.24g, Ζ3 0.36g) προέρχονται από το Εθνικό Προσάρτημα. Κάθε
   τιμή που μπορεί να διαφοροποιείται στο Εθνικό Προσάρτημα φέρει [ΕΠΑΛΗΘΕΥΣΕ]
   και πρέπει να επιβεβαιωθεί στο ισχύον κείμενο πριν χρησιμοποιηθεί σε μελέτη.

Ο διαχωρισμός ευθύνης: το script εκτελεί τους τύπους. Ο μελετητής επιβεβαιώνει
ζώνη, κατηγορία εδάφους, συντελεστή συμπεριφοράς q, κατηγορία σπουδαιότητας και
τις τιμές του Εθνικού Προσαρτήματος, και υπογράφει τη μελέτη.

ΣΗΜΕΙΩΣΗ ΜΕΤΑΒΑΣΗΣ: οι Ευρωκώδικες δεύτερης γενιάς παραδόθηκαν στους εθνικούς
φορείς το 2026 και η πρώτη γενιά αποσύρεται το 2028. Το παρόν υλοποιεί την
πρώτη γενιά, που παραμένει η βάση των ελληνικών μελετών μέχρι τη μετάβαση.

Usage:
  python fortia.py combos --g 15 --q 10 --category A
  python fortia.py combos --g 15 --q 10 --q2 4 --category2 H
  python fortia.py fasma --zoni 2 --edafos B --q 3.9 --periodos 0.5
  python fortia.py fasma --zoni 2 --edafos B --q 3.9 --pinakas
  python fortia.py psi
  python fortia.py zones
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

# EN 1990 πίνακας A1.1, συνιστώμενες τιμές CEN. Το Εθνικό Προσάρτημα μπορεί να
# διαφοροποιεί επιμέρους τιμές, γι' αυτό κάθε χρήση σε μελέτη περνά από επιβεβαίωση.
PSI = {
    "A": {"onoma": "Κατοικίες", "psi0": 0.7, "psi1": 0.5, "psi2": 0.3},
    "B": {"onoma": "Γραφεία", "psi0": 0.7, "psi1": 0.5, "psi2": 0.3},
    "C": {"onoma": "Χώροι συνάθροισης", "psi0": 0.7, "psi1": 0.7, "psi2": 0.6},
    "D": {"onoma": "Καταστήματα", "psi0": 0.7, "psi1": 0.7, "psi2": 0.6},
    "E": {"onoma": "Αποθήκες", "psi0": 1.0, "psi1": 0.9, "psi2": 0.8},
    "F": {"onoma": "Οχήματα έως 30 kN", "psi0": 0.7, "psi1": 0.7, "psi2": 0.6},
    "G": {"onoma": "Οχήματα 30 έως 160 kN", "psi0": 0.7, "psi1": 0.5, "psi2": 0.3},
    "H": {"onoma": "Στέγες μη προσβάσιμες", "psi0": 0.0, "psi1": 0.0, "psi2": 0.0},
    "XIONI": {"onoma": "Χιόνι, υψόμετρο κάτω από 1000 m", "psi0": 0.5, "psi1": 0.2, "psi2": 0.0},
    "ANEMOS": {"onoma": "Άνεμος", "psi0": 0.6, "psi1": 0.2, "psi2": 0.0},
}

# Συντελεστές ασφαλείας δράσεων, EN 1990 εξ. 6.10, συνιστώμενες τιμές (STR/GEO, σύνολο B).
GAMMA_G = 1.35
GAMMA_Q = 1.50

# Ελληνικό Εθνικό Προσάρτημα EN 1998-1: ζώνες σεισμικής επικινδυνότητας.
ZONES = {
    "1": {"agr_g": 0.16, "perigrafi": "Ζώνη Ι"},
    "2": {"agr_g": 0.24, "perigrafi": "Ζώνη ΙΙ"},
    "3": {"agr_g": 0.36, "perigrafi": "Ζώνη ΙΙΙ"},
}

# EN 1998-1 πίνακας 3.2, φάσμα τύπου 1, συνιστώμενες τιμές CEN. Το ελληνικό
# Εθνικό Προσάρτημα μπορεί να ορίζει διαφορετική TD. Επιβεβαίωση υποχρεωτική.
SOILS = {
    "A": {"S": 1.00, "TB": 0.15, "TC": 0.40, "TD": 2.00},
    "B": {"S": 1.20, "TB": 0.15, "TC": 0.50, "TD": 2.00},
    "C": {"S": 1.15, "TB": 0.20, "TC": 0.60, "TD": 2.00},
    "D": {"S": 1.35, "TB": 0.20, "TC": 0.80, "TD": 2.00},
    "E": {"S": 1.40, "TB": 0.15, "TC": 0.50, "TD": 2.00},
}

# EN 1998-1 πίνακας 4.3: συντελεστές σπουδαιότητας, συνιστώμενες τιμές.
IMPORTANCE = {"I": 0.8, "II": 1.0, "III": 1.2, "IV": 1.4}

BETA = 0.20  # κατώφλι φάσματος σχεδιασμού, συνιστώμενη τιμή CEN


def combos(g, q, category, q2=None, category2=None):
    """Συνδυασμοί δράσεων EN 1990 για μόνιμο G και έως δύο μεταβλητές δράσεις.

    Επιστρέφει τους συνδυασμούς ΟΚΑ (6.10) και ΟΚΛ (χαρακτηριστικό 6.14b,
    συχνό 6.15b, οιονεί μόνιμο 6.16b), και τον σεισμικό συνδυασμό G + ψ2 Q.
    Οι τιμές είναι σε όποια μονάδα δόθηκαν τα G και Q (η μηχανή δεν υποθέτει
    μονάδες).

    Σε κάθε συνδυασμό που διακρίνει κύρια δράση (ΟΚΑ 6.10, χαρακτηριστικός
    με ψ0 στις συνοδευτικές, συχνός με ψ1 κύρια και ψ2 λοιπές) κάθε μεταβλητή
    δοκιμάζεται ως κύρια και επιστρέφεται η δυσμενέστερη περίπτωση, με τη
    δράση που κυβέρνησε κατονομασμένη στο αποτέλεσμα.
    """
    if g < 0 or q < 0 or (q2 is not None and q2 < 0):
        raise ValueError("Οι δράσεις δίνονται ως μη αρνητικά μεγέθη.")
    cat = PSI.get(category.upper())
    if cat is None:
        raise ValueError(f"Άγνωστη κατηγορία {category}. Διαθέσιμες: {', '.join(PSI)}")
    entries = [(q, cat, category.upper())]
    if q2 is not None:
        cat2 = PSI.get((category2 or "").upper())
        if cat2 is None:
            raise ValueError("Με --q2 απαιτείται έγκυρη --category2.")
        entries.append((q2, cat2, category2.upper()))

    # ΟΚΑ 6.10: κάθε μεταβλητή δοκιμάζεται ως κύρια, οι υπόλοιπες με ψ0.
    uls_options = []
    for lead_idx, (qi, cati, code) in enumerate(entries):
        total = GAMMA_G * g + GAMMA_Q * qi
        parts = [f"{GAMMA_G:.2f}G", f"{GAMMA_Q:.2f}Q({code})"]
        for j, (qj, catj, codej) in enumerate(entries):
            if j == lead_idx:
                continue
            total += GAMMA_Q * catj["psi0"] * qj
            parts.append(f"{GAMMA_Q:.2f}x{catj['psi0']:.1f}Q({codej})")
        uls_options.append({"kyria": code, "typos": " + ".join(parts), "timi": round(total, 4)})
    uls = max(uls_options, key=lambda o: o["timi"])

    # ΟΚΛ 6.14b και 6.15b: όπως στον ΟΚΑ, κάθε μεταβλητή δοκιμάζεται ως κύρια
    # (χαρακτηριστικός: κύρια πλήρης, συνοδευτικές με ψ0. Συχνός: κύρια με ψ1,
    # λοιπές με ψ2) και κρατιέται η δυσμενέστερη περίπτωση.
    def _sls_worst(lead_psi_key, other_psi_key):
        best_code, best_total = None, None
        for lead_idx, (qi, cati, code) in enumerate(entries):
            lead_part = qi if lead_psi_key is None else cati[lead_psi_key] * qi
            total = g + lead_part + sum(
                catj[other_psi_key] * qj
                for j, (qj, catj, _c) in enumerate(entries) if j != lead_idx)
            if best_total is None or total > best_total:
                best_code, best_total = code, total
        return best_code, best_total

    char_lead, sls_char = _sls_worst(None, "psi0")
    freq_lead, sls_freq = _sls_worst("psi1", "psi2")
    sls_qp = g + sum(e[1]["psi2"] * e[0] for e in entries)
    seismic = g + sum(e[1]["psi2"] * e[0] for e in entries)

    return {
        "eisodos": {"G": g, "Q": [{"timi": e[0], "katigoria": e[2]} for e in entries]},
        "OKA_6_10": uls,
        "OKA_oles_oi_periptoseis": uls_options,
        "OKL_xaraktiristikos": round(sls_char, 4),
        "OKL_xaraktiristikos_kyria": char_lead,
        "OKL_syxnos": round(sls_freq, 4),
        "OKL_syxnos_kyria": freq_lead,
        "OKL_oionei_monimos": round(sls_qp, 4),
        "seismikos_G_psi2Q": round(seismic, 4),
        "simeiosi": (
            f"Συνιστώμενες τιμές CEN {_VERIFY}: επιβεβαίωσε ψ και γ στο Εθνικό "
            "Προσάρτημα. Ο σεισμικός συνδυασμός θέλει και τη δράση AEd από το φάσμα."
        ),
    }


def sd(T, ag, S, TB, TC, TD, q):
    """Φάσμα σχεδιασμού Sd(T) κατά EN 1998-1 παρ. 3.2.2.5, σε μονάδες του ag.

    Οι τέσσερις κλάδοι, με το κατώφλι β ag στους δύο φθίνοντες. Το q δεν
    επιτρέπεται κάτω από 1.0 (θα σήμαινε ενίσχυση αντί απομείωσης).
    """
    if q < 1.0:
        raise ValueError("Ο συντελεστής συμπεριφοράς q δεν μπορεί να είναι μικρότερος του 1.0.")
    if T < 0:
        raise ValueError("Η περίοδος T δίνεται μη αρνητική.")
    if T <= TB:
        return ag * S * (2.0 / 3.0 + (T / TB) * (2.5 / q - 2.0 / 3.0))
    if T <= TC:
        return ag * S * 2.5 / q
    if T <= TD:
        return max(ag * S * (2.5 / q) * (TC / T), BETA * ag)
    return max(ag * S * (2.5 / q) * (TC * TD / (T * T)), BETA * ag)


def fasma(zoni, edafos, q, gamma_i="II", td_override=None):
    """Παράμετροι φάσματος σχεδιασμού για ελληνική ζώνη και κατηγορία εδάφους.

    Επιστρέφει ag = γI agR, τις παραμέτρους S, TB, TC, TD και την τιμή του
    πλατό. Η TD δέχεται ρητή τιμή Εθνικού Προσαρτήματος με td_override.
    """
    z = ZONES.get(str(zoni))
    if z is None:
        raise ValueError(f"Άγνωστη ζώνη {zoni}. Διαθέσιμες: 1, 2, 3.")
    soil = SOILS.get(edafos.upper())
    if soil is None:
        raise ValueError(f"Άγνωστη κατηγορία εδάφους {edafos}. Διαθέσιμες: A, B, C, D, E.")
    gi = IMPORTANCE.get(gamma_i.upper())
    if gi is None:
        raise ValueError(f"Άγνωστη κατηγορία σπουδαιότητας {gamma_i}. Διαθέσιμες: I, II, III, IV.")
    td = td_override if td_override is not None else soil["TD"]
    ag = gi * z["agr_g"]  # σε g
    plateau = ag * soil["S"] * 2.5 / q
    return {
        "zoni": z["perigrafi"],
        "agR_g": z["agr_g"],
        "gamma_I": gi,
        "ag_g": round(ag, 4),
        "edafos": edafos.upper(),
        "S": soil["S"],
        "TB_s": soil["TB"],
        "TC_s": soil["TC"],
        "TD_s": td,
        "q": q,
        "beta": BETA,
        "plato_Sd_g": round(plateau, 4),
        "simeiosi": (
            f"Παράμετροι πίνακα 3.2 CEN και ζώνες Εθνικού Προσαρτήματος {_VERIFY}. "
            "Το ελληνικό Εθνικό Προσάρτημα ενδέχεται να ορίζει διαφορετική TD και "
            "τιμές γΙ. Επιβεβαίωσε στο ισχύον κείμενο πριν από χρήση σε μελέτη."
        ),
    }


def fasma_pinakas(params, periods=None):
    """Πίνακας τιμών Sd(T) σε g για τυπικές περιόδους, πάνω σε params από fasma()."""
    if periods is None:
        periods = [0.0, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0, 4.0]
    rows = []
    for t in periods:
        val = sd(t, params["ag_g"], params["S"], params["TB_s"], params["TC_s"],
                 params["TD_s"], params["q"])
        rows.append({"T_s": t, "Sd_g": round(val, 4)})
    return rows


def _print_combos(r):
    print("Συνδυασμοί δράσεων EN 1990")
    print(f"  Είσοδος: G = {r['eisodos']['G']}, "
          + ", ".join(f"Q({e['katigoria']}) = {e['timi']}" for e in r["eisodos"]["Q"]))
    print(f"  ΟΚΑ 6.10 (δυσμενέστερος): {r['OKA_6_10']['typos']} = {r['OKA_6_10']['timi']}")
    for o in r["OKA_oles_oi_periptoseis"]:
        print(f"    με κύρια την Q({o['kyria']}): {o['timi']}")
    print(f"  ΟΚΛ χαρακτηριστικός: {r['OKL_xaraktiristikos']} (κύρια η Q({r['OKL_xaraktiristikos_kyria']}))")
    print(f"  ΟΚΛ συχνός: {r['OKL_syxnos']} (κύρια η Q({r['OKL_syxnos_kyria']}))")
    print(f"  ΟΚΛ οιονεί μόνιμος: {r['OKL_oionei_monimos']}")
    print(f"  Σεισμικός (G + ψ2 Q, χωρίς AEd): {r['seismikos_G_psi2Q']}")
    print(f"  {r['simeiosi']}")


def _print_fasma(p, rows=None):
    print(f"Φάσμα σχεδιασμού EN 1998-1: {p['zoni']}, έδαφος {p['edafos']}, q = {p['q']}")
    print(f"  agR = {p['agR_g']} g, γI = {p['gamma_I']}, ag = {p['ag_g']} g")
    print(f"  S = {p['S']}, TB = {p['TB_s']} s, TC = {p['TC_s']} s, TD = {p['TD_s']} s, β = {p['beta']}")
    print(f"  Πλατό Sd = {p['plato_Sd_g']} g")
    if rows:
        print("  T (s)    Sd (g)")
        for row in rows:
            print(f"  {row['T_s']:<7} {row['Sd_g']}")
    print(f"  {p['simeiosi']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("combos", help="Συνδυασμοί δράσεων EN 1990")
    c.add_argument("--g", type=float, required=True, help="Μόνιμη δράση G")
    c.add_argument("--q", type=float, required=True, help="Μεταβλητή δράση Q")
    c.add_argument("--category", default="A", help="Κατηγορία χρήσης της Q (A, B, C, D, E, F, G, H, XIONI, ANEMOS)")
    c.add_argument("--q2", type=float, help="Δεύτερη μεταβλητή δράση")
    c.add_argument("--category2", help="Κατηγορία της δεύτερης μεταβλητής")
    c.add_argument("--json", action="store_true")

    f = sub.add_parser("fasma", help="Φάσμα σχεδιασμού EN 1998-1")
    f.add_argument("--zoni", required=True, help="Σεισμική ζώνη: 1, 2 ή 3")
    f.add_argument("--edafos", required=True, help="Κατηγορία εδάφους: A, B, C, D, E")
    f.add_argument("--q", type=float, required=True, help="Συντελεστής συμπεριφοράς q")
    f.add_argument("--spoudaiotita", default="II", help="Κατηγορία σπουδαιότητας: I, II, III, IV")
    f.add_argument("--td", type=float, help="Ρητή TD Εθνικού Προσαρτήματος σε s")
    f.add_argument("--periodos", type=float, help="Υπολογισμός Sd σε συγκεκριμένη περίοδο T (s)")
    f.add_argument("--pinakas", action="store_true", help="Πίνακας Sd(T) σε τυπικές περιόδους")
    f.add_argument("--json", action="store_true")

    p = sub.add_parser("psi", help="Πίνακας συντελεστών ψ (EN 1990 A1.1)")
    p.add_argument("--json", action="store_true")

    z = sub.add_parser("zones", help="Ελληνικές σεισμικές ζώνες και κατηγορίες εδάφους")
    z.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "combos":
        r = combos(args.g, args.q, args.category, args.q2, args.category2)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            _print_combos(r)
    elif args.cmd == "fasma":
        prm = fasma(args.zoni, args.edafos, args.q, args.spoudaiotita, args.td)
        if args.periodos is not None:
            prm["Sd_T_g"] = round(
                sd(args.periodos, prm["ag_g"], prm["S"], prm["TB_s"], prm["TC_s"], prm["TD_s"], prm["q"]), 4)
            prm["T_s"] = args.periodos
        rows = fasma_pinakas(prm) if args.pinakas else None
        if args.json:
            out = dict(prm)
            if rows:
                out["pinakas"] = rows
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            _print_fasma(prm, rows)
            if args.periodos is not None:
                print(f"  Sd(T = {args.periodos} s) = {prm['Sd_T_g']} g")
    elif args.cmd == "psi":
        if args.json:
            print(json.dumps(PSI, ensure_ascii=False, indent=2))
        else:
            print(f"Συντελεστές ψ, EN 1990 πίνακας A1.1, συνιστώμενες τιμές CEN {_VERIFY}")
            for code, row in PSI.items():
                print(f"  {code:<7} {row['onoma']:<32} ψ0 = {row['psi0']}, ψ1 = {row['psi1']}, ψ2 = {row['psi2']}")
    elif args.cmd == "zones":
        payload = {"zones": ZONES, "soils": SOILS, "importance": IMPORTANCE}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Σεισμικές ζώνες Ελλάδας (Εθνικό Προσάρτημα EN 1998-1) {_VERIFY}")
            for k, v in ZONES.items():
                print(f"  Ζώνη {k}: agR = {v['agr_g']} g")
            print("Κατηγορίες εδάφους (πίνακας 3.2, φάσμα τύπου 1, τιμές CEN)")
            for k, v in SOILS.items():
                print(f"  {k}: S = {v['S']}, TB = {v['TB']}, TC = {v['TC']}, TD = {v['TD']}")
            print("Συντελεστές σπουδαιότητας: " + ", ".join(f"{k} = {v}" for k, v in IMPORTANCE.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
