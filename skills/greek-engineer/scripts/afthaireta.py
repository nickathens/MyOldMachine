#!/usr/bin/env python3
"""Αυθαίρετα Ν.4495/2017: κατηγορίες, δομή προστίμου, προθεσμίες.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης, κατά το πρότυπο του greek-law:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΑΡΙΘΜΗΤΙΚΗ (prostimo). Εφαρμόζει τη δομή του ενιαίου
   ειδικού προστίμου: πρόστιμο = τιμή ζώνης x τετραγωνικά x 15% x γινόμενο
   συντελεστών. Η ΤΙΜΗ ΖΩΝΗΣ και ΚΑΘΕ ΣΥΝΤΕΛΕΣΤΗΣ δίνονται ρητά από τον
   χρήστη, αφού επιβεβαιωθούν στο ισχύον κείμενο και στο πληροφοριακό σύστημα
   του ΤΕΕ. Η μηχανή δεν προτείνει ποτέ ποσό ή συντελεστή από μνήμη: κάνει
   την αριθμητική πάνω σε επιβεβαιωμένες τιμές, όπως το voithia του
   greek-law κάνει τα δύο τρίτα πάνω σε ποσό αναφοράς που δίνει ο χρήστης.

2. ΟΔΗΓΟΣ ΚΑΤΗΓΟΡΙΩΝ ΚΑΙ ΠΡΟΘΕΣΜΙΩΝ (katigories, prothesmies). Σκαλωσιά, όχι
   αυθεντία. Η διάρθρωση των πέντε κατηγοριών του Ν.4495/2017 με τα κριτήρια
   χρόνου και βαρύτητας, και οι κρίσιμες προθεσμίες, όλα με [ΕΠΑΛΗΘΕΥΣΕ] και
   ημερομηνία τελευταίας επαλήθευσης. Η υπαγωγή γίνεται από τον μηχανικό στο
   πληροφοριακό σύστημα, με αυτοψία και δικά του στοιχεία.

Ο διαχωρισμός ευθύνης: το script κάνει αριθμητική και δείχνει τη διάρθρωση.
Ο μηχανικός βεβαιώνει την κατηγορία, τους συντελεστές και την υπαγωγή, και
υπογράφει. Καμία έξοδος δεν αποτελεί βεβαίωση νομιμότητας.

Usage:
  python afthaireta.py prostimo --timi-zonis 1000 --tm 35 --syntelestes 0.4,1.0
  python afthaireta.py prostimo --timi-zonis 800 --tm 20 --syntelestes 0.15 --paravolo 250
  python afthaireta.py katigories
  python afthaireta.py prothesmies
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"
_AS_OF = "2026-07-18"

POSOSTO_VASIS = 0.15  # το 15% της δομής του ενιαίου ειδικού προστίμου [ΕΠΑΛΗΘΕΥΣΕ]

# Η διάρθρωση των κατηγοριών του Ν.4495/2017. Κριτήρια και άρθρα: επιβεβαίωση
# στο ισχύον κείμενο υποχρεωτική, ιδίως μετά την κωδικοποίηση του Ν.5306/2026.
KATIGORIES = [
    {"kat": 1, "titlos": "Προϋφιστάμενα του 1975",
     "perigrafi": "Κατασκευές πριν από την 9.6.1975. Εξαιρούνται οριστικά με καταβολή παραβόλου, "
                  "χωρίς πρόστιμο."},
    {"kat": 2, "titlos": "Προϋφιστάμενα του 1983",
     "perigrafi": "Κατασκευές πριν από την 1.1.1983. Οριστική εξαίρεση με παράβολο και "
                  "μειωμένους συντελεστές."},
    {"kat": 3, "titlos": "Μικρές παραβάσεις",
     "perigrafi": "Απαρίθμηση μικρών παραβάσεων του νόμου, π.χ. μικρές υπερβάσεις και "
                  "διαμορφώσεις. Εξαίρεση με παράβολο."},
    {"kat": 4, "titlos": "Με οικοδομική άδεια, εντός ποσοστών",
     "perigrafi": "Αυθαίρετες κατασκευές ή αλλαγές χρήσης σε κτίριο με άδεια, εφόσον δεν "
                  "υπερβαίνουν τα ποσοστά του νόμου. Αναστολή κυρώσεων κατά τους όρους του."},
    {"kat": 5, "titlos": "Λοιπές αυθαίρετες κατασκευές",
     "perigrafi": "Ό,τι δεν εντάσσεται στις προηγούμενες. Το καθεστώς υπαγωγής τους έχει "
                  "κλείσει κατά κανόνα και η τακτοποίηση συνδέεται με την Ηλεκτρονική "
                  "Ταυτότητα Κτιρίου και τις εκάστοτε ειδικές ρυθμίσεις."},
]

PROTHESMIES = [
    {"thema": "Δηλώσεις υπαγωγής κατηγοριών 1 έως 4",
     "prothesmia": "31.3.2028",
     "pigi": "Παράταση δημοσιευμένη τον Ιούλιο 2026, από 31.3.2026"},
    {"thema": "Ηλεκτρονική Ταυτότητα Κτιρίου",
     "prothesmia": "Απαιτείται για την ολοκλήρωση τακτοποίησης και για κάθε μεταβίβαση",
     "pigi": "Ν.4495/2017 όπως ισχύει"},
]


def prostimo(timi_zonis, tm, syntelestes, paravolo=None):
    """Αριθμητική ενιαίου ειδικού προστίμου πάνω σε επιβεβαιωμένες τιμές.

    timi_zonis: τιμή ζώνης σε ευρώ ανά m2, όπως ισχύει στην περιοχή, από τον
    χρήστη. tm: τετραγωνικά της αυθαίρετης κατασκευής. syntelestes: λίστα των
    συντελεστών του φύλλου καταγραφής όπως τους επιβεβαίωσε ο μηχανικός στο
    ισχύον παράρτημα. paravolo: προαιρετικό ποσό παραβόλου προς συνυπολογισμό
    στο συνολικό κόστος (το παράβολο δεν συμψηφίζεται εδώ με το πρόστιμο:
    ο συμψηφισμός κρίνεται στο πληροφοριακό σύστημα).
    """
    if timi_zonis <= 0 or tm <= 0:
        raise ValueError("Τιμή ζώνης και τετραγωνικά πρέπει να είναι θετικά.")
    if not syntelestes:
        raise ValueError("Δώσε τουλάχιστον έναν συντελεστή, όπως τον επιβεβαίωσες στο παράρτημα.")
    if any(s <= 0 for s in syntelestes):
        raise ValueError("Οι συντελεστές πρέπει να είναι θετικοί.")
    if paravolo is not None and paravolo < 0:
        raise ValueError("Το παράβολο δίνεται μη αρνητικό.")

    ginomeno = 1.0
    for s in syntelestes:
        ginomeno *= s
    poso = timi_zonis * tm * POSOSTO_VASIS * ginomeno
    out = {
        "eisodos": {"timi_zonis": timi_zonis, "tm": tm, "syntelestes": syntelestes,
                    "pososto_vasis": POSOSTO_VASIS},
        "ginomeno_syntel": round(ginomeno, 6),
        "prostimo_eyro": round(poso, 2),
        "simeiosi": (
            f"Αριθμητική επί επιβεβαιωμένων τιμών. Η δομή 15% και οι συντελεστές {_VERIFY} "
            "στο ισχύον παράρτημα του Ν.4495/2017. Η οριστική εκκαθάριση γίνεται "
            "μόνο στο πληροφοριακό σύστημα του ΤΕΕ."
        ),
    }
    if paravolo is not None:
        out["paravolo_eyro"] = paravolo
        out["synoliko_kostos_eyro"] = round(poso + paravolo, 2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prostimo", help="Αριθμητική ενιαίου ειδικού προστίμου")
    p.add_argument("--timi-zonis", type=float, required=True, help="Τιμή ζώνης σε ευρώ ανά m2")
    p.add_argument("--tm", type=float, required=True, help="Τετραγωνικά μέτρα")
    p.add_argument("--syntelestes", required=True,
                   help="Συντελεστές φύλλου καταγραφής, χωρισμένοι με κόμμα, π.χ. 0.4,1.0")
    p.add_argument("--paravolo", type=float, help="Ποσό παραβόλου προς συνυπολογισμό")
    p.add_argument("--json", action="store_true")

    k = sub.add_parser("katigories", help="Οι πέντε κατηγορίες του Ν.4495/2017")
    k.add_argument("--json", action="store_true")

    pr = sub.add_parser("prothesmies", help="Κρίσιμες προθεσμίες τακτοποίησης")
    pr.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "prostimo":
        try:
            synt = [float(x) for x in args.syntelestes.split(",") if x.strip()]
        except ValueError as exc:
            raise SystemExit(f"Μη αριθμητικός συντελεστής: {exc}")
        r = prostimo(args.timi_zonis, args.tm, synt, args.paravolo)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            e = r["eisodos"]
            print(f"Πρόστιμο = {e['timi_zonis']} x {e['tm']} x {e['pososto_vasis']} x "
                  f"{r['ginomeno_syntel']} = {r['prostimo_eyro']} ευρώ")
            if "synoliko_kostos_eyro" in r:
                print(f"  Με παράβολο {r['paravolo_eyro']} ευρώ: σύνολο {r['synoliko_kostos_eyro']} ευρώ")
            print(f"  {r['simeiosi']}")
    elif args.cmd == "katigories":
        if args.json:
            print(json.dumps(KATIGORIES, ensure_ascii=False, indent=2))
        else:
            print(f"Κατηγορίες αυθαιρέτων Ν.4495/2017 {_VERIFY} (επαλήθευση {_AS_OF})")
            for k in KATIGORIES:
                print(f"  Κατηγορία {k['kat']}: {k['titlos']}")
                print(f"    {k['perigrafi']}")
            print("  Η υπαγωγή βεβαιώνεται από μηχανικό με αυτοψία, στο πληροφοριακό σύστημα του ΤΕΕ.")
    elif args.cmd == "prothesmies":
        if args.json:
            print(json.dumps(PROTHESMIES, ensure_ascii=False, indent=2))
        else:
            print(f"Κρίσιμες προθεσμίες {_VERIFY} (επαλήθευση {_AS_OF})")
            for row in PROTHESMIES:
                print(f"  {row['thema']}: {row['prothesmia']}")
                print(f"    Πηγή: {row['pigi']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
