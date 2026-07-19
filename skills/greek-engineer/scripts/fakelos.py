#!/usr/bin/env python3
"""Προέλεγχος πληρότητας φακέλου οικοδομικής άδειας (e-Άδειες).

Το χαρακτηριστικό εργαλείο του skill, το ανάλογο του aoristia_check του
greek-law: πριν από την υποβολή, έλεγξε ότι ο φάκελος περιέχει ό,τι θα
αναζητήσει ο ελεγκτής, και ποιος υπογράφει τι. Ένα κτίριο είναι σημείο
συνάντησης μελετών από πολλές ειδικότητες που οφείλουν να συμφωνούν μεταξύ
τους: εδώ είναι ο ενιαίος κατάλογος.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΟΣ ΕΛΕΓΧΟΣ (check). Δίνεις τι υπάρχει στον φάκελο, βγαίνει
   η λίστα των κενών, με τα υπό όρους στοιχεία χωριστά ώστε να απαντηθεί
   ρητά αν σε αφορούν. Καθαρή αντιπαραβολή με τον κατάλογο.

2. Ο ΚΑΤΑΛΟΓΟΣ ΕΙΝΑΙ ΣΚΑΛΩΣΙΑ [ΕΠΑΛΗΘΕΥΣΕ]. Οι απαιτούμενες μελέτες ανά
   περίπτωση ορίζονται από την ισχύουσα νομοθεσία και τις προδιαγραφές του
   e-Άδειες, και εξειδικεύονται από την οικεία ΥΔΟΜ. Ένας πλήρης κατά το
   εργαλείο φάκελος δεν είναι εγκεκριμένη άδεια: σημαίνει ότι η σκαλωσιά
   είναι στη θέση της. Ελλείψεις που κρίνονται κατά περίπτωση, όπως ειδικές
   εγκρίσεις, πρέπει να απαντηθούν με την ΥΔΟΜ.

Usage:
  python fakelos.py list
  python fakelos.py check --typos nea-oikodomi
  python fakelos.py check --typos nea-oikodomi --exo topografiko,architektoniki,statiki
  python fakelos.py check --typos nea-oikodomi --exo topografiko --synthikes anelkystiras
  python fakelos.py eidikotites
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

# Ο κατάλογος στοιχείων φακέλου νέας οικοδομής. Κάθε στοιχείο: ποιος το
# υπογράφει, αν είναι πάντοτε απαιτούμενο ή υπό όρους, και τι κοιτάζει ο
# ελεγκτής. Η στήλη ypografi δείχνει τη συνήθη ειδικότητα: οι επαγγελματικές
# άδειες και τα επιμέρους δικαιώματα υπογραφής κρίνονται από το ισχύον πλαίσιο.
STOIXEIA = {
    "topografiko": {
        "onoma": "Τοπογραφικό διάγραμμα σε ΕΓΣΑ '87",
        "ypografi": "τοπογράφος ή πολιτικός μηχανικός",
        "panta": True,
        "elegktis": "Εξαρτημένες συντεταγμένες, όροι δόμησης, ρυμοτομικές και "
                    "οικοδομικές γραμμές. Παλαιό ή ανεξάρτητο τοπογραφικό είναι "
                    "κλασικός λόγος επιστροφής του φακέλου.",
    },
    "diagramma-kalypsis": {
        "onoma": "Διάγραμμα κάλυψης και δόμησης",
        "ypografi": "αρχιτέκτονας ή πολιτικός μηχανικός",
        "panta": True,
        "elegktis": "Εξάντληση συντελεστών σε συμφωνία με τους βεβαιωμένους όρους "
                    "δόμησης. Πρέπει να κλείνει αριθμητικά με την αρχιτεκτονική μελέτη.",
    },
    "architektoniki": {
        "onoma": "Αρχιτεκτονική μελέτη",
        "ypografi": "αρχιτέκτονας",
        "panta": True,
        "elegktis": "Κατόψεις, όψεις, τομές σε συμφωνία με το διάγραμμα κάλυψης, "
                    "προσβασιμότητα ΑμεΑ, κτιριοδομικές απαιτήσεις.",
    },
    "statiki": {
        "onoma": "Στατική μελέτη",
        "ypografi": "πολιτικός μηχανικός",
        "panta": True,
        "elegktis": "Παραδοχές φορτίων και σεισμικών παραμέτρων, τεύχος "
                    "υπολογισμών, ξυλότυποι σε συμφωνία με την αρχιτεκτονική.",
    },
    "geotexniki": {
        "onoma": "Γεωτεχνική ή εδαφοτεχνική μελέτη",
        "ypografi": "πολιτικός μηχανικός",
        "panta": False,
        "synthiki": "Απαιτείται κατά τις προβλέψεις για το μέγεθος του κτιρίου και "
                    "τις εδαφικές συνθήκες " + _VERIFY,
        "elegktis": "Συμβατότητα παραδοχών θεμελίωσης με τη στατική μελέτη.",
    },
    "hm-ydrefsi": {
        "onoma": "Μελέτη ύδρευσης και αποχέτευσης",
        "ypografi": "μηχανολόγος ή ηλεκτρολόγος μηχανικός",
        "panta": True,
        "elegktis": "Δίκτυα, κλίσεις, σύνδεση με δίκτυα κοινής ωφέλειας.",
    },
    "hm-ilektrika": {
        "onoma": "Μελέτη ηλεκτρικών εγκαταστάσεων",
        "ypografi": "ηλεκτρολόγος ή μηχανολόγος μηχανικός",
        "panta": True,
        "elegktis": "Πίνακες, γραμμές, γειώσεις κατά το ισχύον πρότυπο.",
    },
    "hm-thermansi": {
        "onoma": "Μελέτη θέρμανσης και κλιματισμού",
        "ypografi": "μηχανολόγος μηχανικός",
        "panta": True,
        "elegktis": "Θερμικά φορτία σε συμφωνία με την ενεργειακή μελέτη.",
    },
    "anelkystiras": {
        "onoma": "Μελέτη ανελκυστήρα",
        "ypografi": "μηχανολόγος ή ηλεκτρολόγος μηχανικός",
        "panta": False,
        "synthiki": "Όταν το κτίριο απαιτεί ανελκυστήρα κατά τον κτιριοδομικό " + _VERIFY,
        "elegktis": "Φρεάτιο και μηχανοστάσιο σε συμφωνία με την αρχιτεκτονική.",
    },
    "kenak": {
        "onoma": "Μελέτη ενεργειακής απόδοσης ΚΕΝΑΚ",
        "ypografi": "μηχανικός με δικαίωμα κατά το ισχύον πλαίσιο",
        "panta": True,
        "elegktis": "Θερμομονωτική επάρκεια και παραδοχές σε συμφωνία με "
                    "αρχιτεκτονική και μηχανολογικές μελέτες. Πρόσεχε την "
                    "αναθεώρηση ΚΕΝΑΚ λόγω EPBD: δες kanonismoi.py show energeia.",
    },
    "pyroprostasia": {
        "onoma": "Μελέτη παθητικής και ενεργητικής πυροπροστασίας",
        "ypografi": "αρχιτέκτονας για την παθητική, μηχανολόγος για την ενεργητική",
        "panta": True,
        "elegktis": "Οδεύσεις διαφυγής, πυροδιαμερίσματα, μέσα πυρόσβεσης κατά "
                    "τον ισχύοντα κανονισμό για τη χρήση.",
    },
    "say-fay": {
        "onoma": "ΣΑΥ και ΦΑΥ (ασφάλεια και υγεία εργοταξίου)",
        "ypografi": "συντονιστής ασφάλειας, μηχανικός",
        "panta": True,
        "elegktis": "Παρουσία και αντιστοιχία με το τεχνικό αντικείμενο του έργου.",
    },
    "sda-aekk": {
        "onoma": "Σχέδιο Διαχείρισης Αποβλήτων (ΑΕΚΚ)",
        "ypografi": "μηχανικός του έργου",
        "panta": True,
        "elegktis": "Σύμβαση με εγκεκριμένο σύστημα εναλλακτικής διαχείρισης.",
    },
    "egkrisi-archaiologias": {
        "onoma": "Έγκριση αρχαιολογικής υπηρεσίας",
        "ypografi": "εκδίδεται από την υπηρεσία",
        "panta": False,
        "synthiki": "Σε κηρυγμένους αρχαιολογικούς χώρους ή ζώνες προστασίας " + _VERIFY,
        "elegktis": "Χωρίς αυτήν ο φάκελος δεν προχωρά σε εμπλεκόμενες περιοχές.",
    },
    "egkrisi-dasarxeiou": {
        "onoma": "Πράξη χαρακτηρισμού ή έγκριση δασαρχείου",
        "ypografi": "εκδίδεται από την υπηρεσία",
        "panta": False,
        "synthiki": "Σε εκτός σχεδίου ή περιοχές δασικού ενδιαφέροντος " + _VERIFY,
        "elegktis": "Συμβατότητα με τους δασικούς χάρτες.",
    },
    "symvoulio-architektonikis": {
        "onoma": "Γνωμοδότηση Συμβουλίου Αρχιτεκτονικής",
        "ypografi": "εκδίδεται από το συμβούλιο",
        "panta": False,
        "synthiki": "Σε παραδοσιακούς οικισμούς, διατηρητέα και ειδικές περιπτώσεις " + _VERIFY,
        "elegktis": "Απαιτείται πριν από την έγκριση δόμησης όπου προβλέπεται.",
    },
    "amoives-eisfores": {
        "onoma": "Αμοιβές μηχανικών και εισφορές",
        "ypografi": "υπολογίζονται στο σύστημα",
        "panta": True,
        "elegktis": "Πληρωμές και αποδεικτικά όπως τα παράγει η πλατφόρμα.",
    },
    # Στοιχεία περιβαλλοντικής αδειοδότησης ΕΕΛ: κόσμος διαφορετικός από την
    # οικοδομική άδεια. Δεν εμφανίζονται στους τύπους κτιρίου (δες _EEL_STOIXEIA).
    "mpe": {
        "onoma": "Μελέτη Περιβαλλοντικών Επιπτώσεων ή Πρότυπες Περιβαλλοντικές Δεσμεύσεις",
        "ypografi": "μελετητής περιβαλλοντικών με το ανάλογο πτυχίο",
        "panta": True,
        "elegktis": "Κατηγορία έργου Α ή Β κατά Ν.4014/2011: ΜΠΕ για την Α, ΠΠΔ "
                    "για τη Β. Αποδέκτης, όρια εκροής, φορτίο σχεδιασμού.",
    },
    "aepo": {
        "onoma": "Απόφαση Έγκρισης Περιβαλλοντικών Όρων (ΑΕΠΟ)",
        "ypografi": "εκδίδεται από την αρμόδια περιβαλλοντική αρχή",
        "panta": True,
        "elegktis": "Η εγκριτική απόφαση με τους περιβαλλοντικούς όρους. Χωρίς "
                    "αυτήν (ή την υπαγωγή σε ΠΠΔ) το έργο δεν αδειοδοτείται.",
    },
    "meleti-eel": {
        "onoma": "Μελέτη διεργασίας και Η/Μ εγκατάστασης επεξεργασίας",
        "ypografi": "μηχανολόγος, χημικός ή πολιτικός μηχανικός με δικαίωμα",
        "panta": True,
        "elegktis": "Διαστασιολόγηση βιολογικής βαθμίδας, ισοζύγια, αερισμός, "
                    "γραμμή ιλύος. Το τεύχος υπολογισμών προδιαστασιολογείται με lymata.py.",
    },
    "statiki-dexamenon": {
        "onoma": "Στατική μελέτη δεξαμενών υγρού περιεχομένου",
        "ypografi": "πολιτικός μηχανικός",
        "panta": True,
        "elegktis": "Στεγανότητα και έλεγχος ρηγμάτωσης κατά EN 1992-3, "
                    "υδροστατικά και σεισμικά φορτία δεξαμενής.",
    },
    "diaxeirisi-ilyos": {
        "onoma": "Σχέδιο διαχείρισης και διάθεσης ιλύος",
        "ypografi": "μηχανικός του έργου",
        "panta": True,
        "elegktis": "Πάχυνση, αφυδάτωση, σταθεροποίηση και τελική διάθεση κατά "
                    "το ισχύον πλαίσιο για την ιλύ.",
    },
    "adeia-diathesis": {
        "onoma": "Καθορισμός αποδέκτη και άδεια διάθεσης εκροής",
        "ypografi": "εκδίδεται από την αρμόδια αρχή",
        "panta": False,
        "synthiki": "Όταν ο αποδέκτης απαιτεί χωριστή άδεια διάθεσης (επιφανειακά "
                    "ύδατα, θαλάσσιος, επαναχρησιμοποίηση) " + _VERIFY,
        "elegktis": "Συμμόρφωση με τα όρια του αποδέκτη και τους όρους της ΑΕΠΟ.",
    },
}

# Στοιχεία που ανήκουν αποκλειστικά στον φάκελο ΕΕΛ (περιβαλλοντική
# αδειοδότηση), ώστε να μην εμφανίζονται στους τύπους οικοδομικής άδειας.
_EEL_STOIXEIA = ("mpe", "aepo", "meleti-eel", "statiki-dexamenon",
                 "diaxeirisi-ilyos", "adeia-diathesis")

# Ο πλήρης κατάλογος στοιχείων κτιρίου: όλα εκτός από τα αποκλειστικά ΕΕΛ.
_KTIRIO_STOIXEIA = [s for s in STOIXEIA if s not in _EEL_STOIXEIA]

TYPOI = {
    "nea-oikodomi": {
        "onoma": "Νέα οικοδομή",
        "stoixeia": list(_KTIRIO_STOIXEIA),
    },
    "prosthiki": {
        "onoma": "Προσθήκη σε υφιστάμενο κτίριο",
        "stoixeia": list(_KTIRIO_STOIXEIA),
        "simeiosi": "Επιπλέον: έλεγχος στατικής επάρκειας του υφισταμένου και "
                    "συσχέτιση με τυχόν τακτοποιήσεις αυθαιρέτων " + _VERIFY,
    },
    "allagi-xrisis": {
        "onoma": "Αλλαγή χρήσης",
        "stoixeia": ["diagramma-kalypsis", "architektoniki", "kenak",
                     "pyroprostasia", "amoives-eisfores"],
        "simeiosi": "Κατά περίπτωση απαιτούνται και στατικός έλεγχος ή "
                    "μηχανολογικές μελέτες για τη νέα χρήση " + _VERIFY,
    },
    "eel": {
        "onoma": "Εγκατάσταση Επεξεργασίας Λυμάτων (ΕΕΛ)",
        "stoixeia": ["topografiko", "mpe", "aepo", "meleti-eel", "statiki-dexamenon",
                     "hm-ilektrika", "diaxeirisi-ilyos", "adeia-diathesis",
                     "say-fay", "amoives-eisfores"],
        "simeiosi": "Έργο περιβαλλοντικής αδειοδότησης, όχι οικοδομικής άδειας: "
                    "η ΑΕΠΟ (ή η υπαγωγή σε ΠΠΔ) προηγείται. Δες kanonismoi.py "
                    "show astika-lymata και lymata.py " + _VERIFY,
    },
}


def check(typos, exo=(), synthikes_isxyoun=()):
    """Αντιπαραβολή του φακέλου με τον κατάλογο του τύπου έργου.

    exo: slugs στοιχείων που υπάρχουν ήδη. synthikes_isxyoun: slugs υπό όρους
    στοιχείων που ο μηχανικός βεβαιώνει ότι αφορούν το έργο (τα υπόλοιπα υπό
    όρους στοιχεία επιστρέφονται ως ανοιχτές ερωτήσεις, όχι ως κενά).
    """
    t = TYPOI.get(typos)
    if t is None:
        raise ValueError(f"Άγνωστος τύπος {typos}. Διαθέσιμοι: {', '.join(TYPOI)}")
    unknown = [s for s in list(exo) + list(synthikes_isxyoun) if s not in STOIXEIA]
    if unknown:
        raise ValueError(f"Άγνωστα στοιχεία: {', '.join(unknown)}. Δες τη λίστα με list.")

    exo_set = set(exo)
    cond_set = set(synthikes_isxyoun)
    kena, erotiseis, plires = [], [], []
    for slug in t["stoixeia"]:
        item = STOIXEIA[slug]
        required = item["panta"] or slug in cond_set
        if slug in exo_set:
            plires.append(slug)
        elif required:
            kena.append({"slug": slug, "onoma": item["onoma"], "ypografi": item["ypografi"],
                         "elegktis": item["elegktis"]})
        else:
            erotiseis.append({"slug": slug, "onoma": item["onoma"],
                              "synthiki": item.get("synthiki", "")})
    required_slugs = [s for s in t["stoixeia"] if STOIXEIA[s]["panta"] or s in cond_set]
    plires_required = [s for s in plires if s in required_slugs]
    return {
        "typos": t["onoma"],
        "plires": plires,
        "kena": kena,
        "anoixtes_erotiseis": erotiseis,
        "etoimotita": f"{len(plires_required)}/{len(required_slugs)} "
                      "πάντοτε απαιτούμενα ή βεβαιωμένα υπό όρους",
        "simeiosi": t.get("simeiosi", "") or (
            f"Κατάλογος σκαλωσιάς {_VERIFY} στις προδιαγραφές του e-Άδειες και την "
            "οικεία ΥΔΟΜ. Πληρότητα εδώ δεν σημαίνει έγκριση."
        ),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="Κατάλογος στοιχείων και τύπων έργου")
    ls.add_argument("--json", action="store_true")

    ch = sub.add_parser("check", help="Προέλεγχος πληρότητας")
    ch.add_argument("--typos", required=True, help="Τύπος έργου, π.χ. nea-oikodomi")
    ch.add_argument("--exo", default="", help="Slugs που υπάρχουν, χωρισμένα με κόμμα")
    ch.add_argument("--synthikes", default="",
                    help="Υπό όρους slugs που βεβαιωμένα αφορούν το έργο")
    ch.add_argument("--json", action="store_true")

    ei = sub.add_parser("eidikotites", help="Ποιος υπογράφει τι")
    ei.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "list":
        if args.json:
            print(json.dumps({"typoi": {k: v["onoma"] for k, v in TYPOI.items()},
                              "stoixeia": {k: v["onoma"] for k, v in STOIXEIA.items()}},
                             ensure_ascii=False, indent=2))
        else:
            print("Τύποι έργου:")
            for slug, t in TYPOI.items():
                print(f"  {slug:<15} {t['onoma']}")
            print("Στοιχεία φακέλου:")
            for slug, s in STOIXEIA.items():
                flag = "πάντοτε" if s["panta"] else "υπό όρους"
                print(f"  {slug:<24} {s['onoma']} ({flag})")
    elif args.cmd == "check":
        exo = [s.strip() for s in args.exo.split(",") if s.strip()]
        syn = [s.strip() for s in args.synthikes.split(",") if s.strip()]
        r = check(args.typos, exo, syn)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"Προέλεγχος φακέλου: {r['typos']}")
            print(f"  Ετοιμότητα: {r['etoimotita']}")
            if r["kena"]:
                print("  ΚΕΝΑ (απαιτούμενα που λείπουν):")
                for k in r["kena"]:
                    print(f"    {k['onoma']} ({k['ypografi']})")
                    print(f"      Τι κοιτάζει ο ελεγκτής: {k['elegktis']}")
            else:
                print("  Κανένα κενό στα απαιτούμενα.")
            if r["anoixtes_erotiseis"]:
                print("  ΑΝΟΙΧΤΕΣ ΕΡΩΤΗΣΕΙΣ (υπό όρους, απάντησε αν αφορούν το έργο):")
                for q in r["anoixtes_erotiseis"]:
                    print(f"    {q['onoma']}: {q['synthiki']}")
            print(f"  {r['simeiosi']}")
    elif args.cmd == "eidikotites":
        if args.json:
            print(json.dumps({k: v["ypografi"] for k, v in STOIXEIA.items()},
                             ensure_ascii=False, indent=2))
        else:
            print("Ποιος υπογράφει τι (συνήθης ειδικότητα, τα δικαιώματα κρίνονται "
                  f"από το ισχύον πλαίσιο {_VERIFY}):")
            for slug, s in STOIXEIA.items():
                print(f"  {s['onoma']}: {s['ypografi']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
