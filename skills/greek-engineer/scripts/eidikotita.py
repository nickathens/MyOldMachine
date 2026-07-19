#!/usr/bin/env python3
"""Δρομολογητής ειδικοτήτων: από το ερώτημα στο σωστό πακέτο εργαλείων.

Το skill καλύπτει πολλές ειδικότητες μηχανικών πάνω σε κοινή κανονιστική
σπονδυλική στήλη. Αυτό το εργαλείο δρομολογεί: δίνεις μια φράση εργασίας
(π.χ. «πτώση τάσης σε γραμμή κουζίνας») και επιστρέφει την ειδικότητα, τα
scripts και τις πρακτικές ενότητες που την αφορούν. Η αναζήτηση είναι
αναίσθητη σε τόνους και πεζά κεφαλαία, ώστε να δουλεύει με βιαστική γραφή.

Usage:
  python eidikotita.py list
  python eidikotita.py find "πτωση τασης"
  python eidikotita.py find "φερουσα τοιχοποιια"
  python eidikotita.py show politikos
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata

DISCIPLINES = {
    "politikos": {
        "onoma": "Πολιτικός μηχανικός",
        "perigrafi": "Φέρων οργανισμός, φορτία, αντισεισμικός σχεδιασμός, "
                     "προσεισμικοί έλεγχοι, αυθαίρετα.",
        "tools": ["fortia.py", "plaisio.py", "domisi.py", "afthaireta.py",
                  "fakelos.py", "proseismikos.py", "kanonismoi.py"],
        "practice": ["ypologismoi.md", "methodos.md"],
        "lexeis": ["στατικά", "στατική", "φέρουσα", "φέρων", "σκυρόδεμα", "οπλισμένο",
                   "θεμελίωση", "θεμέλια", "αντισεισμικός", "σεισμός", "φάσμα",
                   "φορτία", "δοκός", "δοκάρι", "υποστύλωμα", "κολώνα", "πλάκα",
                   "τοιχίο", "ευρωκώδικας", "καν.επε", "τρωτότητα", "προσεισμικός",
                   "αυθαίρετο", "αυθαίρετα", "τακτοποίηση", "πλαίσιο", "χάλυβας",
                   "μεταλλική", "ξύλινη", "τοιχοποιία", "ροπή", "τέμνουσα", "βέλος"],
    },
    "architektonas": {
        "onoma": "Αρχιτέκτονας μηχανικός",
        "perigrafi": "Όροι δόμησης, κάλυψη, κατόψεις και σχέδια DXF, κτιριοδομικά, "
                     "προσβασιμότητα.",
        "tools": ["domisi.py", "katopsi.py", "fakelos.py", "kanonismoi.py"],
        "practice": ["methodos.md", "diepafes.md"],
        "lexeis": ["κάτοψη", "όψη", "τομή", "σχέδιο", "αρχιτεκτονική", "όροι δόμησης",
                   "κάλυψη", "συντελεστής δόμησης", "νοκ", "δόμηση", "ύψος",
                   "autocad", "dxf", "dwg", "κτιριοδομικός", "αμεα", "προσβασιμότητα",
                   "διάγραμμα κάλυψης", "εξώστης", "ημιυπαίθριος", "πατάρι",
                   "διατηρητέο", "οικισμός", "πρόσοψη"],
    },
    "ilektrologos": {
        "onoma": "Ηλεκτρολόγος μηχανικός",
        "perigrafi": "Εσωτερικές ηλεκτρικές εγκαταστάσεις, πίνακες, ΥΔΕ, "
                     "διαστασιολόγηση γραμμών.",
        "tools": ["ilektrologika.py", "fakelos.py", "kanonismoi.py"],
        "practice": ["methodos.md"],
        "lexeis": ["καλώδιο", "καλωδίωση", "πτώση τάσης", "ρεύμα", "διακόπτης",
                   "ασφάλεια", "ρελέ", "πίνακας", "γραμμή", "παροχή", "υδε",
                   "γείωση", "διαφορικό", "φωτοβολταϊκά", "δεδδηε", "μονοφασικό",
                   "τριφασικό", "hd 384", "φωτισμός", "πρίζα", "ηλεκτρολογικό"],
    },
    "michanologos": {
        "onoma": "Μηχανολόγος μηχανικός",
        "perigrafi": "Θέρμανση, ψύξη, κλιματισμός, υδραυλικά δίκτυα, αντλίες, "
                     "ενεργητική πυροπροστασία, ανελκυστήρες.",
        "tools": ["michanologika.py", "fakelos.py", "kanonismoi.py"],
        "practice": ["methodos.md"],
        "lexeis": ["θέρμανση", "ψύξη", "κλιματισμός", "λέβητας", "αντλία",
                   "κυκλοφορητής", "σωλήνας", "σωλήνωση", "υδραυλικά", "παροχή νερού",
                   "μανομετρικό", "τριβές", "αερισμός", "καυσαέρια", "αέριο",
                   "πυρόσβεση", "καταιονισμός", "ανελκυστήρας", "ψυκτικό",
                   "θερμαντικά", "σώματα", "ενδοδαπέδια", "αντλία θερμότητας"],
    },
    "energeiakos": {
        "onoma": "Ενεργειακός επιθεωρητής",
        "perigrafi": "ΠΕΑ, ΚΕΝΑΚ, ενεργειακή απόδοση. Πλήρες πακέτο σε επόμενο "
                     "στάδιο: προς το παρόν η κανονιστική εικόνα.",
        "tools": ["kanonismoi.py", "fakelos.py"],
        "practice": ["methodos.md"],
        "lexeis": ["πεα", "ενεργειακό πιστοποιητικό", "ενεργειακή κλάση", "κενακ",
                   "θερμομόνωση", "εξοικονομώ", "ενεργειακή απόδοση", "epbd"],
    },
    "topografos": {
        "onoma": "Τοπογράφος μηχανικός",
        "perigrafi": "Τοπογραφικά, ΕΓΣΑ '87, κτηματολόγιο. Πλήρες πακέτο σε "
                     "επόμενο στάδιο: προς το παρόν η θέση του τοπογραφικού στον φάκελο.",
        "tools": ["fakelos.py", "kanonismoi.py"],
        "practice": ["methodos.md"],
        "lexeis": ["τοπογραφικό", "εγσα", "συντεταγμένες", "κτηματολόγιο",
                   "γεωτεμάχιο", "όρια οικοπέδου", "αποτύπωση", "καεκ"],
    },
    "perivallontikos": {
        "onoma": "Μηχανικός περιβάλλοντος",
        "perigrafi": "Υγειονομική και περιβαλλοντική μηχανική: εγκαταστάσεις "
                     "επεξεργασίας λυμάτων, βιολογικός καθαρισμός, περιβαλλοντική "
                     "αδειοδότηση. v1: επεξεργασία αστικών λυμάτων.",
        "tools": ["lymata.py", "fakelos.py", "kanonismoi.py"],
        "practice": ["methodos.md"],
        "lexeis": ["λύματα", "λυμάτων", "λύμα", "βιολογικός καθαρισμός",
                   "επεξεργασία λυμάτων", "εγκατάσταση επεξεργασίας", "εελ",
                   "οξειδωτική τάφρος", "ενεργός ιλύς", "ιλύος", "ιλύ",
                   "αερισμός", "παρατεταμένος αερισμός", "νιτροποίηση",
                   "δευτεροβάθμια επεξεργασία", "αποδέκτης", "εκροή", "bod", "cod",
                   "mlss", "ισοδύναμος κάτοικος", "ισοδύναμοι κάτοικοι", "βιοαντιδραστήρας"],
    },
}


def _fold(text):
    """Αναδίπλωση για σύγκριση: πεζά, χωρίς τόνους και διαλυτικά, και με
    ενοποίηση του τελικού σίγμα, ώστε η αναζήτηση να μη σκοντάφτει στη γραφή."""
    nfd = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.replace("ς", "σ")


def find(query):
    """Δρομολόγηση φράσης εργασίας σε ειδικότητες, με σκορ επικάλυψης λέξεων."""
    q = _fold(query)
    if not q.strip():
        raise ValueError("Δώσε φράση αναζήτησης.")
    hits = []
    for slug, d in DISCIPLINES.items():
        score = sum(1 for lexi in d["lexeis"] if _fold(lexi) in q)
        if score:
            hits.append({"slug": slug, "onoma": d["onoma"], "score": score,
                         "tools": d["tools"]})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list", help="Κατάλογος ειδικοτήτων")
    ls.add_argument("--json", action="store_true")

    fi = sub.add_parser("find", help="Δρομολόγηση φράσης εργασίας")
    fi.add_argument("query", help="Η φράση, π.χ. 'πτωση τασης'")
    fi.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", help="Πλήρες πακέτο ειδικότητας")
    sh.add_argument("slug", help="Ειδικότητα, π.χ. politikos")
    sh.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "list":
        if args.json:
            print(json.dumps({k: v["onoma"] for k, v in DISCIPLINES.items()},
                             ensure_ascii=False, indent=2))
        else:
            print("Ειδικότητες:")
            for slug, d in DISCIPLINES.items():
                print(f"  {slug:<14} {d['onoma']}: {d['perigrafi']}")
    elif args.cmd == "find":
        hits = find(args.query)
        if args.json:
            print(json.dumps(hits, ensure_ascii=False, indent=2))
        elif not hits:
            print("Καμία ειδικότητα δεν ταίριαξε. Δες τον κατάλογο με list, ή "
                  "χρησιμοποίησε το κοινό εργαλείο kanonismoi.py.")
        else:
            print(f"Δρομολόγηση για: {args.query}")
            for h in hits:
                print(f"  {h['onoma']} (σκορ {h['score']}): " + ", ".join(h["tools"]))
    elif args.cmd == "show":
        d = DISCIPLINES.get(args.slug)
        if d is None:
            raise SystemExit(f"Άγνωστη ειδικότητα {args.slug}. Διαθέσιμες: " + ", ".join(DISCIPLINES))
        if args.json:
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(f"{d['onoma']}")
            print(f"  {d['perigrafi']}")
            print(f"  Εργαλεία: {', '.join(d['tools'])}")
            print(f"  Πρακτική: {', '.join(d['practice'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
