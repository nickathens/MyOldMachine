#!/usr/bin/env python3
"""Πρόγραμμα υποχρεωτικού προσεισμικού ελέγχου: αμοιβή, ένταξη, βήματα.

Νέα ροή εργασίας για πολιτικούς μηχανικούς από τον Μάρτιο του 2026: ο
πρωτοβάθμιος προσεισμικός έλεγχος δημόσιων κτιρίων και αθλητικών
εγκαταστάσεων, με μητρώο ελεγκτών στο ΤΕΕ και πλατφόρμα του ΟΑΣΠ.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΑΡΙΘΜΗΤΙΚΗ (amoivi). Η κλιμακωτή δομή αποζημίωσης της
   ΚΥΑ: για κτίρια, ευρώ ανά τετραγωνικό μέτρο κατά ζώνη εμβαδού με ελάχιστο
   ανά ζώνη. Για αθλητικές εγκαταστάσεις, χωριστές βαθμίδες για κερκίδες,
   στέγαστρα και πυλώνες. Η αποζημίωση κάθε ελέγχου καταβάλλεται εξ ημισείας
   στους δύο ελεγκτές. Καθαρή αριθμητική, με τα ποσά σημασμένα [ΕΠΑΛΗΘΕΥΣΕ]
   έναντι του ΦΕΚ.

2. ΟΔΗΓΟΣ ΕΝΤΑΞΗΣ (vimata). Τα βήματα για να μπει μηχανικός στο μητρώο και
   το αντικείμενο του ελέγχου. Σκαλωσιά με ημερομηνία επαλήθευσης, όχι
   αυθεντία: οι λεπτομέρειες ορίζονται από την ΚΥΑ και τις εγκυκλίους του
   προγράμματος.

Στοιχεία προγράμματος κατά την επαλήθευση της 2026-07-18: ισχύουσα ΚΥΑ ΥΠ
688/2026 (ΦΕΚ Β' 1276/6.3.2026), που διατηρεί για τα κτίρια την κλίμακα της
ΚΥΑ ΥΠ 342/2023 (ΦΕΚ Β' 2943/4.5.2023) και προσθέτει τις βαθμίδες αθλητικών
εγκαταστάσεων. Προϋπολογισμός 48 εκατ. ευρώ από το Ταμείο Ανάκαμψης, ανάθεση
με κλήρωση, δύο μηχανικοί ανά έλεγχο.

Usage:
  python proseismikos.py amoivi --tm 350
  python proseismikos.py amoivi --tm 2400 --ktiria 3
  python proseismikos.py amoivi --tm 12000 --typos kerkides
  python proseismikos.py amoivi --tm 22000 --typos stegastro
  python proseismikos.py amoivi --pylones 5
  python proseismikos.py vimata
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"
_AS_OF = "2026-07-18"
_KYA = "ΚΥΑ ΥΠ 688/2026 (ΦΕΚ Β' 1276/6.3.2026)"

# Κτίρια: ζώνες (άνω όριο m2 ή None, ευρώ ανά m2, ελάχιστο ευρώ ανά κτίριο).
# Ο συντελεστής της ζώνης εφαρμόζεται σε ΟΛΟ το εμβαδόν. Τα ελάχιστα είναι
# έτσι βαθμονομημένα ώστε η αμοιβή να είναι συνεχής και αύξουσα στα όρια.
# Κλίμακα της ΚΥΑ ΥΠ 342/2023, διατηρείται στην ισχύουσα ΚΥΑ. [ΕΠΑΛΗΘΕΥΣΕ]
KLIMAKA_KTIRION = [
    (1000.0, 1.00, 500.0),
    (1500.0, 0.90, 1000.0),
    (None, 0.80, 1350.0),
]

# Κερκίδες αθλητικών εγκαταστάσεων: βαθμίδες (άνω όριο m2 ή None, ποσό ευρώ)
# κατά την ισχύουσα ΚΥΑ. [ΕΠΑΛΗΘΕΥΣΕ]
KLIMAKA_KERKIDON = [
    (500.0, 500.0),
    (2000.0, 1000.0),
    (5000.0, 2500.0),
    (10000.0, 5000.0),
    (None, 7000.0),
]

# Στέγαστρα αθλητικών εγκαταστάσεων: βαθμίδες (άνω όριο m2 ή None, ποσό ευρώ)
# κατά την ισχύουσα ΚΥΑ. [ΕΠΑΛΗΘΕΥΣΕ]
KLIMAKA_STEGASTRON = [
    (100.0, 500.0),
    (500.0, 1000.0),
    (2000.0, 2000.0),
    (3500.0, 2750.0),
    (5000.0, 3500.0),
    (7500.0, 5000.0),
    (10000.0, 7000.0),
    (20000.0, 10000.0),
    (None, 15000.0),
]

# Πυλώνες, πίνακες αποτελεσμάτων, εξέδρες: ευρώ ανά πυλώνα με ελάχιστο ανά
# ομάδα, κατά την ισχύουσα ΚΥΑ. [ΕΠΑΛΗΘΕΥΣΕ]
EYRO_ANA_PYLONA = 150.0
ELAXISTO_PYLONON = 500.0

TYPOI = ("ktirio", "kerkides", "stegastro")

_SIMEIOSEIS = [
    f"Κλίμακα κατά την {_KYA}, επαλήθευση {_AS_OF}. "
    f"Ποσά {_VERIFY} στο ΦΕΚ πριν από κάθε δέσμευση.",
    "Η αποζημίωση κάθε ελέγχου καταβάλλεται εξ ημισείας στους δύο ελεγκτές.",
    f"Νησιωτικοί έλεγχοι από ελεγκτές ηπειρωτικής έδρας: επιπλέον 150 ευρώ "
    f"ανά ελεγκτή {_VERIFY}.",
    f"Προβλέπονται μειωμένα ποσοστά επί μερικής αδυναμίας στοιχείων ή "
    f"απροσπελασιμότητας {_VERIFY} στο ΦΕΚ.",
]

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


def _vathmida(tm, klimaka):
    """Επιστρέφει τη βαθμίδα της κλίμακας στην οποία πέφτει το εμβαδόν."""
    for orio, *ypoloipo in klimaka:
        if orio is None or tm <= orio:
            return ypoloipo
    raise RuntimeError("Η κλίμακα δεν καλύπτει το εμβαδόν: εσφαλμένος πίνακας.")


def amoivi(tm, ktiria=1, typos="ktirio"):
    """Αποζημίωση πρωτοβάθμιου ελέγχου κατά την κλιμακωτή δομή της ΚΥΑ.

    tm: συνολικά τετραγωνικά ανά μονάδα ελέγχου (κτίριο, κερκίδα ή στέγαστρο).
    ktiria: πλήθος μονάδων με τα ίδια τετραγωνικά η καθεμία (για γρήγορη
    εκτίμηση χαρτοφυλακίου δίνεται ανά μονάδα ξεχωριστά).
    typos: ktirio (κλίμακα ευρώ ανά m2 με ελάχιστα), kerkides ή stegastro
    (βαθμίδες σταθερού ποσού).
    """
    if tm <= 0:
        raise ValueError("Τα τετραγωνικά πρέπει να είναι θετικά.")
    if ktiria < 1:
        raise ValueError("Το πλήθος μονάδων πρέπει να είναι τουλάχιστον 1.")
    if typos not in TYPOI:
        raise ValueError(f"Άγνωστος τύπος {typos!r}: επίλεξε από {TYPOI}.")

    if typos == "ktirio":
        eyro_ana_tm, elaxisti = _vathmida(tm, KLIMAKA_KTIRION)
        ana_monada = max(elaxisti, eyro_ana_tm * tm)
        vathmida = {"eyro_ana_tm": eyro_ana_tm, "elaxisti_eyro": elaxisti}
        efarmostike_elaxisti = ana_monada == elaxisti
    else:
        klimaka = KLIMAKA_KERKIDON if typos == "kerkides" else KLIMAKA_STEGASTRON
        (ana_monada,) = _vathmida(tm, klimaka)
        vathmida = {"poso_vathmidas_eyro": ana_monada}
        efarmostike_elaxisti = False

    return {
        "typos": typos,
        "eisodos": {"tm_ana_monada": tm, "monades": ktiria},
        "vathmida": vathmida,
        "amoivi_ana_ktirio_eyro": round(ana_monada, 2),
        "ana_elegkti_eyro": round(ana_monada / 2, 2),
        "efarmostike_elaxisti": efarmostike_elaxisti,
        "synolo_eyro": round(ana_monada * ktiria, 2),
        "simeioseis": list(_SIMEIOSEIS),
    }


def amoivi_pylonon(plithos):
    """Αποζημίωση για ομάδα πυλώνων, πινάκων ή εξεδρών: ανά τεμάχιο με ελάχιστο."""
    if plithos < 1:
        raise ValueError("Το πλήθος πυλώνων πρέπει να είναι τουλάχιστον 1.")
    synolo = max(ELAXISTO_PYLONON, EYRO_ANA_PYLONA * plithos)
    return {
        "typos": "pylones",
        "eisodos": {"plithos": plithos, "eyro_ana_pylona": EYRO_ANA_PYLONA,
                    "elaxisto_omadas_eyro": ELAXISTO_PYLONON},
        "amoivi_omadas_eyro": round(synolo, 2),
        "ana_elegkti_eyro": round(synolo / 2, 2),
        "efarmostike_elaxisti": synolo == ELAXISTO_PYLONON,
        "simeioseis": list(_SIMEIOSEIS),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("amoivi", help="Αποζημίωση πρωτοβάθμιου ελέγχου")
    a.add_argument("--tm", type=float, help="Τετραγωνικά ανά μονάδα ελέγχου")
    a.add_argument("--typos", choices=TYPOI, default="ktirio",
                   help="Είδος μονάδας: κτίριο, κερκίδες ή στέγαστρο")
    a.add_argument("--ktiria", type=int, default=1, help="Πλήθος μονάδων")
    a.add_argument("--pylones", type=int,
                   help="Πλήθος πυλώνων ή πινάκων (χωριστή βαθμίδα, αγνοεί το --tm)")
    a.add_argument("--json", action="store_true")

    v = sub.add_parser("vimata", help="Βήματα ένταξης στο μητρώο και αντικείμενο")
    v.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "amoivi":
        if args.pylones is not None:
            r = amoivi_pylonon(args.pylones)
            if args.json:
                print(json.dumps(r, ensure_ascii=False, indent=2))
            else:
                print(f"Αποζημίωση ομάδας πυλώνων: {r['amoivi_omadas_eyro']} ευρώ"
                      + (" (ελάχιστη)" if r["efarmostike_elaxisti"] else ""))
                print(f"  Ανά ελεγκτή (εξ ημισείας): {r['ana_elegkti_eyro']} ευρώ")
                for s in r["simeioseis"]:
                    print(f"  {s}")
            return 0
        if args.tm is None:
            ap.error("Δώσε --tm (ή --pylones για βαθμίδα πυλώνων).")
        r = amoivi(args.tm, args.ktiria, args.typos)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"Αποζημίωση ανά μονάδα ({r['typos']}): {r['amoivi_ana_ktirio_eyro']} ευρώ"
                  + (" (ελάχιστη ζώνης)" if r["efarmostike_elaxisti"] else ""))
            print(f"  Ανά ελεγκτή (εξ ημισείας): {r['ana_elegkti_eyro']} ευρώ")
            if args.ktiria > 1:
                print(f"  Σύνολο για {args.ktiria} μονάδες: {r['synolo_eyro']} ευρώ")
            for s in r["simeioseis"]:
                print(f"  {s}")
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
