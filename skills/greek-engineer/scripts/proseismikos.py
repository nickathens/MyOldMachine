#!/usr/bin/env python3
"""Πρόγραμμα υποχρεωτικού προσεισμικού ελέγχου: αμοιβή, ένταξη, βήματα.

Νέα ροή εργασίας για πολιτικούς μηχανικούς από τον Μάρτιο του 2026: ο
πρωτοβάθμιος προσεισμικός έλεγχος δημόσιων κτιρίων και αθλητικών
εγκαταστάσεων, με μητρώο ελεγκτών στο ΤΕΕ και πλατφόρμα του ΟΑΣΠ.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΑΡΙΘΜΗΤΙΚΗ (amoivi). Η δομή αμοιβής του προγράμματος:
   1 ευρώ ανά τετραγωνικό μέτρο με ελάχιστο 500 ευρώ ανά κτίριο, όπως
   δημοσιεύθηκε κατά την έναρξη του προγράμματος. Καθαρή αριθμητική, με τα
   ποσά σημασμένα [ΕΠΑΛΗΘΕΥΣΕ] έναντι της ισχύουσας ΚΥΑ.

2. ΟΔΗΓΟΣ ΕΝΤΑΞΗΣ (vimata). Τα βήματα για να μπει μηχανικός στο μητρώο και
   το αντικείμενο του ελέγχου. Σκαλωσιά με ημερομηνία επαλήθευσης, όχι
   αυθεντία: οι λεπτομέρειες ορίζονται από την ΚΥΑ και τις εγκυκλίους του
   προγράμματος.

Στοιχεία προγράμματος κατά την επαλήθευση της 2026-07-18: ΚΥΑ σε ΦΕΚ Β'
1276/6.3.2026, προϋπολογισμός 48 εκατ. ευρώ από το Ταμείο Ανάκαμψης,
ανάθεση με κλήρωση, δύο μηχανικοί ανά κτίριο.

Usage:
  python proseismikos.py amoivi --tm 350
  python proseismikos.py amoivi --tm 2400 --ktiria 3
  python proseismikos.py vimata
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"
_AS_OF = "2026-07-18"

EYRO_ANA_TM = 1.0     # ευρώ ανά m2 [ΕΠΑΛΗΘΕΥΣΕ στην ισχύουσα ΚΥΑ]
ELAXISTI_AMOIVI = 500.0  # ευρώ ανά κτίριο [ΕΠΑΛΗΘΕΥΣΕ στην ισχύουσα ΚΥΑ]

VIMATA = [
    "Εγγραφή στο μητρώο ελεγκτών προσεισμικού ελέγχου που τηρεί το ΤΕΕ.",
    "Παρακολούθηση του υποχρεωτικού επιμορφωτικού σεμιναρίου του προγράμματος.",
    "Συμμετοχή στις κληρώσεις ανάθεσης: δύο μηχανικοί ανά κτίριο.",
    "Διενέργεια πρωτοβάθμιου ταχέος οπτικού ελέγχου κατά τη μεθοδολογία του ΟΑΣΠ "
    "και υποβολή του δελτίου ελέγχου στην ηλεκτρονική πλατφόρμα.",
    "Αμοιβή κατά τη δομή του προγράμματος, με την υποβολή να πιστοποιείται στην πλατφόρμα.",
]

ANTIKEIMENO = (
    "Πρώτη φάση: δημόσια κτίρια και αθλητικές εγκαταστάσεις. Ο πρωτοβάθμιος "
    "έλεγχος είναι ταχύς οπτικός έλεγχος τρωτότητας, όχι στατική μελέτη ούτε "
    "αποτίμηση κατά ΚΑΝ.ΕΠΕ: κατατάσσει και ιεραρχεί για τον δευτεροβάθμιο έλεγχο."
)


def amoivi(tm, ktiria=1, eyro_ana_tm=EYRO_ANA_TM, elaxisti=ELAXISTI_AMOIVI):
    """Αμοιβή προσεισμικού ελέγχου: μέγιστο του εμβαδικού και του ελάχιστου ανά κτίριο.

    tm: συνολικά τετραγωνικά ανά κτίριο. ktiria: πλήθος κτιρίων με τα ίδια
    τετραγωνικά το καθένα (για γρήγορη εκτίμηση χαρτοφυλακίου δίνεται ανά
    κτίριο ξεχωριστά).
    """
    if tm <= 0:
        raise ValueError("Τα τετραγωνικά πρέπει να είναι θετικά.")
    if ktiria < 1:
        raise ValueError("Το πλήθος κτιρίων πρέπει να είναι τουλάχιστον 1.")
    ana_ktirio = max(elaxisti, eyro_ana_tm * tm)
    return {
        "eisodos": {"tm_ana_ktirio": tm, "ktiria": ktiria,
                    "eyro_ana_tm": eyro_ana_tm, "elaxisti_ana_ktirio": elaxisti},
        "amoivi_ana_ktirio_eyro": round(ana_ktirio, 2),
        "efarmostike_elaxisti": ana_ktirio == elaxisti,
        "synolo_eyro": round(ana_ktirio * ktiria, 2),
        "simeiosi": (
            f"Δομή αμοιβής κατά την έναρξη του προγράμματος, επαλήθευση {_AS_OF}. "
            f"Ποσά {_VERIFY} στην ισχύουσα ΚΥΑ πριν από κάθε δέσμευση."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("amoivi", help="Αμοιβή πρωτοβάθμιου ελέγχου")
    a.add_argument("--tm", type=float, required=True, help="Τετραγωνικά ανά κτίριο")
    a.add_argument("--ktiria", type=int, default=1, help="Πλήθος κτιρίων")
    a.add_argument("--json", action="store_true")

    v = sub.add_parser("vimata", help="Βήματα ένταξης στο μητρώο και αντικείμενο")
    v.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "amoivi":
        r = amoivi(args.tm, args.ktiria)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"Αμοιβή ανά κτίριο: {r['amoivi_ana_ktirio_eyro']} ευρώ"
                  + (" (ελάχιστη)" if r["efarmostike_elaxisti"] else ""))
            if args.ktiria > 1:
                print(f"  Σύνολο για {args.ktiria} κτίρια: {r['synolo_eyro']} ευρώ")
            print(f"  {r['simeiosi']}")
    elif args.cmd == "vimata":
        payload = {"antikeimeno": ANTIKEIMENO, "vimata": VIMATA, "as_of": _AS_OF}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Προσεισμικός έλεγχος: ένταξη και αντικείμενο (επαλήθευση {_AS_OF})")
            print(f"  {ANTIKEIMENO}")
            for i, vima in enumerate(VIMATA, 1):
                print(f"  {i}. {vima}")
            print(f"  Λεπτομέρειες {_VERIFY} στην ΚΥΑ και τις εγκυκλίους του προγράμματος.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
