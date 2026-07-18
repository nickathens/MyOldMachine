#!/usr/bin/env python3
"""Πολεοδομικό περίγραμμα οικοπέδου: δόμηση, κάλυψη, ενδεικτικοί όροφοι.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΑΡΙΘΜΗΤΙΚΗ (perigramma). Παίρνει τους όρους δόμησης όπως
   τους ΕΠΙΒΕΒΑΙΩΣΕ ο χρήστης (εμβαδόν, συντελεστής δόμησης, ποσοστό κάλυψης,
   μέγιστο ύψος) και βγάζει τα μεγέθη: μέγιστη δόμηση, μέγιστη κάλυψη,
   ενδεικτικό πλήθος ορόφων. Καθαρή αριθμητική, πάντοτε σωστή πάνω στα
   δεδομένα που δόθηκαν.

2. ΤΟ SCRIPT ΔΕΝ ΓΝΩΡΙΖΕΙ ΟΡΟΥΣ ΔΟΜΗΣΗΣ. Οι όροι κάθε οικοπέδου προκύπτουν
   από το ισχύον ρυμοτομικό, τις χρήσεις γης και τις τυχόν ειδικές διατάξεις,
   και βεβαιώνονται με το τοπογραφικό και τη βεβαίωση όρων δόμησης. Καμία τιμή
   δεν προτείνεται από μνήμη. Η μηχανή αρνείται να τρέξει χωρίς ρητές τιμές.

ΠΡΟΣΟΧΗ 2026: το κανονιστικό πλαίσιο του ΝΟΚ είναι σε μετάβαση. Οι αποφάσεις
ΣτΕ 146 έως 149/2025 και οι τροποποιητικοί νόμοι άλλαξαν τα κίνητρα και τον
τρόπο μέτρησης (τα πατάρια προσμετρώνται πλέον στη δόμηση), και προωθείται
νέος απλοποιημένος ΝΟΚ. Δες kanonismoi.py show nok για την τρέχουσα εικόνα
και μην υποθέτεις μπόνους δόμησης ή ύψους χωρίς έλεγχο της ισχύουσας διάταξης.

Usage:
  python domisi.py perigramma --emvadon 500 --sd 0.8 --kalypsi 60 --ypsos 11
  python domisi.py perigramma --emvadon 4000 --sd 0.2 --kalypsi 20 --ypsos 7.5 --orofos-ypsos 3.2
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import math
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

OROFOS_YPSOS_DEFAULT = 3.0  # m, ενδεικτικό μεικτό ύψος ορόφου για την εκτίμηση ορόφων


def perigramma(emvadon, sd, kalypsi_pct, ypsos, orofos_ypsos=OROFOS_YPSOS_DEFAULT):
    """Μεγέθη περιγράμματος από επιβεβαιωμένους όρους δόμησης.

    emvadon: εμβαδόν οικοπέδου σε m2. sd: συντελεστής δόμησης. kalypsi_pct:
    ποσοστό κάλυψης επί τοις εκατό. ypsos: μέγιστο επιτρεπόμενο ύψος σε m.
    orofos_ypsos: ενδεικτικό ύψος ορόφου για την εκτίμηση πλήθους ορόφων.
    """
    if emvadon <= 0:
        raise ValueError("Το εμβαδόν πρέπει να είναι θετικό.")
    if sd <= 0:
        raise ValueError("Ο συντελεστής δόμησης πρέπει να είναι θετικός.")
    if not 0 < kalypsi_pct <= 100:
        raise ValueError("Το ποσοστό κάλυψης ανήκει στο διάστημα (0, 100].")
    if ypsos <= 0 or orofos_ypsos <= 0:
        raise ValueError("Ύψος και ύψος ορόφου πρέπει να είναι θετικά.")

    max_domisi = emvadon * sd
    max_kalypsi = emvadon * kalypsi_pct / 100.0
    orofoi_apo_ypsos = max(1, math.floor(ypsos / orofos_ypsos))
    orofoi_apo_domisi = max(1, math.ceil(max_domisi / max_kalypsi))
    endeiktikoi_orofoi = min(orofoi_apo_ypsos, max(orofoi_apo_domisi, 1))
    apotypoma_gia_orofous = max_domisi / orofoi_apo_ypsos

    return {
        "eisodos": {"emvadon_m2": emvadon, "sd": sd, "kalypsi_pct": kalypsi_pct,
                    "max_ypsos_m": ypsos, "orofos_ypsos_m": orofos_ypsos},
        "max_domisi_m2": round(max_domisi, 2),
        "max_kalypsi_m2": round(max_kalypsi, 2),
        "orofoi_apo_ypsos": orofoi_apo_ypsos,
        "elaxistoi_orofoi_gia_pliri_domisi": orofoi_apo_domisi,
        "endeiktikoi_orofoi": endeiktikoi_orofoi,
        "apotypoma_gia_pliri_domisi_sto_ypsos_m2": round(apotypoma_gia_orofous, 2),
        "simeiosi": (
            "Οι όροι δόμησης βεβαιώνονται από την αρμόδια ΥΔΟΜ για το συγκεκριμένο "
            f"οικόπεδο {_VERIFY}. Δεν περιλαμβάνονται: αποστάσεις Δ, εξοστάσεις, "
            "ημιυπαίθριοι, απομειώσεις και κίνητρα, που κρίνονται στην ισχύουσα "
            "διατύπωση του ΝΟΚ. Τα πατάρια προσμετρώνται πλέον στη δόμηση. "
            "Δες kanonismoi.py show nok πριν βασιστείς σε κίνητρα ή μπόνους."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("perigramma", help="Μεγέθη περιγράμματος από όρους δόμησης")
    p.add_argument("--emvadon", type=float, required=True, help="Εμβαδόν οικοπέδου σε m2")
    p.add_argument("--sd", type=float, required=True, help="Συντελεστής δόμησης")
    p.add_argument("--kalypsi", type=float, required=True, help="Ποσοστό κάλυψης επί τοις εκατό")
    p.add_argument("--ypsos", type=float, required=True, help="Μέγιστο επιτρεπόμενο ύψος σε m")
    p.add_argument("--orofos-ypsos", type=float, default=OROFOS_YPSOS_DEFAULT,
                   help="Ενδεικτικό μεικτό ύψος ορόφου σε m")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    r = perigramma(args.emvadon, args.sd, args.kalypsi, args.ypsos, args.orofos_ypsos)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        e = r["eisodos"]
        print(f"Οικόπεδο {e['emvadon_m2']} m2, ΣΔ = {e['sd']}, κάλυψη = {e['kalypsi_pct']}%, "
              f"ύψος = {e['max_ypsos_m']} m")
        print(f"  Μέγιστη δόμηση: {r['max_domisi_m2']} m2")
        print(f"  Μέγιστη κάλυψη: {r['max_kalypsi_m2']} m2")
        print(f"  Όροφοι από ύψος (ανά {e['orofos_ypsos_m']} m): {r['orofoi_apo_ypsos']}")
        print(f"  Ελάχιστοι όροφοι για εξάντληση δόμησης: {r['elaxistoi_orofoi_gia_pliri_domisi']}")
        print(f"  Αποτύπωμα για εξάντληση δόμησης στο ύψος: "
              f"{r['apotypoma_gia_pliri_domisi_sto_ypsos_m2']} m2 ανά όροφο")
        print(f"  {r['simeiosi']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
