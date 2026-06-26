#!/usr/bin/env python3
"""Υπολογισμός δικονομικών προθεσμιών κατά τον ΚΠολΔ (ελληνικό δίκαιο).

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΜΗΧΑΝΗ ΗΜΕΡΟΛΟΓΙΟΥ (compute). Δίνεις αφετήρια ημερομηνία και
   αριθμό ημερών, και επιστρέφει τη λήξη εφαρμόζοντας τους κανόνες του ΚΠολΔ 144:
   η ημέρα του αφετήριου γεγονότος ΔΕΝ υπολογίζεται (παρ. 1), οι ενδιάμεσες αργίες
   ΥΠΟΛΟΓΙΖΟΝΤΑΙ, και μόνο η τελευταία ημέρα, αν πέσει Σάββατο, Κυριακή ή εξαιρετέα,
   μετατίθεται στην επόμενη εργάσιμη (παρ. 2). Υπολογίζει τις ελληνικές αργίες,
   περιλαμβανομένων των κινητών εορτών που εξαρτώνται από το Ορθόδοξο Πάσχα. Η
   αναστολή του Αυγούστου (ΚΠολΔ 147) προσφέρεται προαιρετικά. Αυτό το μέρος είναι
   πάντοτε σωστή αριθμητική ημερολογίου, ανεξάρτητη από νομική κρίση.

2. ΚΑΤΑΛΟΓΟΣ ΣΥΝΗΘΩΝ ΠΡΟΘΕΣΜΙΩΝ (list, info). Σκαλωσιά, όχι αυθεντία. Κάθε εγγραφή
   φέρει το άρθρο, την αφετηρία και τις παραλλαγές της, και είναι ΣΗΜΑΣΜΕΝΗ
   [επαλήθευσε]: ο αριθμός των ημερών πρέπει να επιβεβαιωθεί στον ισχύοντα κώδικα
   πριν χρησιμοποιηθεί, ιδίως γιατί η διαμονή στο εξωτερικό ή η μη επίδοση αλλάζουν
   ριζικά την προθεσμία. Η μηχανή compute δέχεται πάντοτε ρητό αριθμό ημερών, ώστε
   να μην παράγει ποτέ σιωπηλά λάθος αποτέλεσμα.

Ο διαχωρισμός ευθύνης: το script υπολογίζει το ημερολόγιο. Ο δικηγόρος επιβεβαιώνει
ποια προθεσμία ισχύει, πόσες ημέρες είναι, τι την εκκίνησε (επίδοση, δημοσίευση ή
γνώση) και αν εφαρμόζεται αναστολή. Οι δικονομικές προθεσμίες είναι αυστηρές και
ανατρεπτικές: η απώλειά τους σημαίνει απώλεια του ενδίκου μέσου.

Usage:
  python prothesmies.py compute --apo 2026-06-25 --imeres 30
  python prothesmies.py compute --apo 2026-07-20 --imeres 30 --anastoli-avgoustou
  python prothesmies.py list
  python prothesmies.py info efesi
  python prothesmies.py argies 2026
  (πρόσθεσε --json σε οποιαδήποτε εντολή για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

_GR_WEEKDAYS = ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή",
                "Σάββατο", "Κυριακή")

# Σταθερές εθνικές αργίες: κλειδί (μήνας, ημέρα), τιμή το όνομα.
_FIXED = {
    (1, 1): "Πρωτοχρονιά",
    (1, 6): "Θεοφάνεια",
    (3, 25): "Εικοστή Πέμπτη Μαρτίου",
    (5, 1): "Εργατική Πρωτομαγιά",
    (8, 15): "Κοίμηση της Θεοτόκου",
    (10, 28): "Εικοστή Ογδόη Οκτωβρίου",
    (12, 25): "Χριστούγεννα",
    (12, 26): "Σύναξη της Θεοτόκου",
}

# Κινητές εορτές, ως μετατόπιση ημερών από την Κυριακή του Ορθόδοξου Πάσχα.
_MOVABLE = {
    -48: "Καθαρά Δευτέρα",
    -2: "Μεγάλη Παρασκευή",
    1: "Δευτέρα του Πάσχα",
    50: "Δευτέρα του Αγίου Πνεύματος",
}

_VERIFY = "[επαλήθευσε]"


def orthodox_easter(year):
    """Κυριακή του Ορθόδοξου Πάσχα στο Γρηγοριανό ημερολόγιο.

    Αλγόριθμος Meeus για το Ιουλιανό Πάσχα, και μετατροπή στο Γρηγοριανό με +13 ημέρες.
    """
    # ponytail: η μετατόπιση +13 ισχύει για τα έτη 1900-2099 (Ιουλιανή προς Γρηγοριανή
    # διαφορά). Εκτός αυτού του εύρους η σταθερά αλλάζει. Δεν χρειάζεται εδώ, αλλά
    # είναι το γνωστό όριο του αλγορίθμου.
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    return dt.date(year, month, day) + dt.timedelta(days=13)


def argies(year):
    """Επιστρέφει {date: όνομα} τις εθνικές αργίες (εξαιρετέες ημέρες) του έτους."""
    out = {dt.date(year, m, d): name for (m, d), name in _FIXED.items()}
    easter = orthodox_easter(year)
    for offset, name in _MOVABLE.items():
        out[easter + dt.timedelta(days=offset)] = name
    return out


def _holiday_set(y0, y1):
    out = {}
    for y in range(y0, y1 + 1):
        out.update(argies(y))
    return out


def _is_nonworking(day, holidays):
    return day.weekday() >= 5 or day in holidays


def _roll_forward(day, holidays):
    """ΚΠολΔ 144 παρ. 2: αν η λήξη πέφτει Σάββατο, Κυριακή ή εξαιρετέα, μετατίθεται
    στην επόμενη εργάσιμη. Επιστρέφει (νέα ημέρα, λίστα μετατοπισμένων ημερών)."""
    moved = []
    guard = 0
    while _is_nonworking(day, holidays):
        moved.append((day, holidays.get(day, "Σαββατοκύριακο")))
        day += dt.timedelta(days=1)
        guard += 1
        if guard > 15:  # καμία νόμιμη ακολουθία αργιών δεν πλησιάζει αυτό το όριο
            raise RuntimeError("Αδύνατη εύρεση εργάσιμης ημέρας: ανωμαλία αργιών.")
    return day, moved


def compute(start, days, anastoli_avgoustou=False):
    """Υπολογίζει τη λήξη δικονομικής προθεσμίας ημερών κατά τον ΚΠολΔ 144.

    start: η ημερομηνία του αφετήριου γεγονότος (π.χ. της επίδοσης).
    days: ο αριθμός των ημερών της προθεσμίας (θετικός ακέραιος).
    anastoli_avgoustou: αν True, το διάστημα 1-31 Αυγούστου δεν προσμετράται
        (ΚΠολΔ 147 παρ. 7 [επαλήθευσε]: επιβεβαίωσε ότι εφαρμόζεται στη συγκεκριμένη
        προθεσμία, καθώς δεν καλύπτει όλες).
    """
    if days < 1:
        raise ValueError("Οι ημέρες πρέπει να είναι θετικός ακέραιος.")

    enarxi = start + dt.timedelta(days=1)  # ΚΠολΔ 144 παρ. 1: η επομένη του γεγονότος
    if not anastoli_avgoustou:
        raw_last = start + dt.timedelta(days=days)
    else:
        d = start
        counted = 0
        while counted < days:
            d += dt.timedelta(days=1)
            if d.month != 8:  # οι ημέρες του Αυγούστου δεν μετρούν
                counted += 1
        raw_last = d

    holidays = _holiday_set(start.year, raw_last.year + 1)
    lixi, moved = _roll_forward(raw_last, holidays)
    return {
        "afetiria": start.isoformat(),
        "afetiria_imera": _GR_WEEKDAYS[start.weekday()],
        "enarxi": enarxi.isoformat(),
        "imeres": days,
        "anastoli_avgoustou": anastoli_avgoustou,
        "teleftaia_imerologiaki": raw_last.isoformat(),
        "lixi": lixi.isoformat(),
        "lixi_imera": _GR_WEEKDAYS[lixi.weekday()],
        "metatethike": bool(moved),
        "metatopisi": [
            {"imera": d.isoformat(), "imera_evd": _GR_WEEKDAYS[d.weekday()], "logos": r}
            for d, r in moved
        ],
    }


# Σκαλωσιά συνήθων προθεσμιών. Κάθε αριθμός ημερών είναι ΕΝΔΕΙΚΤΙΚΟΣ και πρέπει να
# επιβεβαιωθεί [επαλήθευσε] στον ισχύοντα κώδικα πριν χρησιμοποιηθεί.
_REGISTRY = {
    "anakopi-erimodikias": {
        "onoma": "Ανακοπή ερημοδικίας",
        "imeres": 15,
        "arthro": "ΚΠολΔ 503",
        "afetiria": "Επίδοση της ερήμην απόφασης",
        "parallages": [],
        "katigoria": "Πολιτική δικονομία",
    },
    "efesi": {
        "onoma": "Έφεση",
        "imeres": 30,
        "arthro": "ΚΠολΔ 518",
        "afetiria": "Επίδοση της απόφασης",
        "parallages": [
            "60 ημέρες αν ο διάδικος διαμένει στο εξωτερικό ή είναι αγνώστου διαμονής.",
            "2 έτη από τη δημοσίευση αν η απόφαση δεν επιδόθηκε.",
        ],
        "katigoria": "Πολιτική δικονομία",
    },
    "anairesi": {
        "onoma": "Αναίρεση",
        "imeres": 30,
        "arthro": "ΚΠολΔ 564",
        "afetiria": "Επίδοση της απόφασης",
        "parallages": [
            "60 ημέρες αν ο διάδικος διαμένει στο εξωτερικό ή είναι αγνώστου διαμονής.",
            "2 έτη από τη δημοσίευση αν η απόφαση δεν επιδόθηκε.",
        ],
        "katigoria": "Πολιτική δικονομία",
    },
    "aitisi-akyroseos": {
        "onoma": "Αίτηση ακυρώσεως",
        "imeres": 60,
        "arthro": "ΠΔ 18/1989 άρθρο 46",
        "afetiria": "Δημοσίευση, κοινοποίηση ή πλήρης γνώση της προσβαλλόμενης πράξης",
        "parallages": [
            "90 ημέρες αν ο αιτών διαμένει στο εξωτερικό.",
        ],
        "katigoria": "Διοικητική δικονομία",
    },
    "prosfygi-ousias": {
        "onoma": "Προσφυγή ουσίας",
        "imeres": 60,
        "arthro": "ΚΔΔ Ν.2717/1999 άρθρο 66",
        "afetiria": "Κοινοποίηση της πράξης ή πλήρης γνώση της",
        "parallages": [],
        "katigoria": "Διοικητική δικονομία",
    },
    "endikofani-prosfygi-ded": {
        "onoma": "Ενδικοφανής προσφυγή στη ΔΕΔ",
        "imeres": 30,
        "arthro": "ΚΦΔ Ν.5104/2024 (πρώην Ν.4174/2013 άρθρο 63)",
        "afetiria": "Κοινοποίηση της προσβαλλόμενης πράξης",
        "parallages": [
            "Η ΔΕΔ αποφαίνεται εντός 120 ημερών. Άπρακτη η προθεσμία της σημαίνει "
            "σιωπηρή απόρριψη.",
        ],
        "katigoria": "Φορολογικό",
    },
}


def _render_compute(r):
    lines = [
        "Υπολογισμός προθεσμίας (ΚΠολΔ 144)",
        "",
        f"Αφετήριο γεγονός: {r['afetiria']} ({r['afetiria_imera']})",
        f"Έναρξη (επομένη): {r['enarxi']}",
        f"Διάρκεια: {r['imeres']} ημέρες",
        f"Αναστολή Αυγούστου: {'ναι' if r['anastoli_avgoustou'] else 'όχι'}",
        f"Τελευταία ημερολογιακή ημέρα: {r['teleftaia_imerologiaki']}",
    ]
    if r["metatethike"]:
        lines.append("Μετάθεση λήξης (ΚΠολΔ 144 παρ. 2):")
        for m in r["metatopisi"]:
            lines.append(f"    {m['imera']} ({m['imera_evd']}): {m['logos']}")
    lines += [
        "",
        f"ΛΗΞΗ: {r['lixi']} ({r['lixi_imera']})",
        "",
        "Η μηχανή υπολογίζει το ημερολόγιο με βάση τα στοιχεία που έδωσες.",
        "Επιβεβαίωσε τον αριθμό ημερών και την αφετηρία (επίδοση, δημοσίευση ή "
        "γνώση) στο διέπον άρθρο. Οι ενδιάμεσες αργίες προσμετρώνται. Τοπικές "
        "αργίες δικαστηρίων δεν περιλαμβάνονται.",
    ]
    return "\n".join(lines)


def _render_info(slug, entry):
    lines = [
        f"{entry['onoma']}  {_VERIFY}",
        "",
        f"Κατηγορία: {entry['katigoria']}",
        f"Διέπον άρθρο: {entry['arthro']}",
        f"Αφετηρία: {entry['afetiria']}",
        f"Τυπική προθεσμία (επιβεβαίωσε): {entry['imeres']} ημέρες",
    ]
    if entry["parallages"]:
        lines.append("Παραλλαγές:")
        for p in entry["parallages"]:
            lines.append(f"    {p}")
    lines += [
        "",
        f"Ο αριθμός ημερών είναι ενδεικτικός και χρειάζεται επιβεβαίωση στο {entry['arthro']}.",
        "Μόλις τον επιβεβαιώσεις, τρέξε:",
        f"    prothesmies.py compute --apo ΗΜΕΡΟΜΗΝΙΑ_ΑΦΕΤΗΡΙΑΣ --imeres {entry['imeres']}",
    ]
    return "\n".join(lines)


def _render_list():
    lines = ["Συνήθεις δικονομικές προθεσμίες (σκαλωσιά, όλες χρειάζονται επιβεβαίωση):", ""]
    width = max(len(s) for s in _REGISTRY)
    awidth = max(len(e["arthro"]) for e in _REGISTRY.values())
    for slug, e in _REGISTRY.items():
        lines.append(
            f"  {slug:<{width}}  {e['imeres']:>3} ημ.  {e['arthro']:<{awidth}}  {e['onoma']}")
    lines += ["", f"Λεπτομέρειες: prothesmies.py info efesi. Όλες οι τιμές {_VERIFY}."]
    return "\n".join(lines)


def _render_argies(year, table):
    lines = [f"Εξαιρετέες ημέρες {year}:", ""]
    for d in sorted(table):
        lines.append(f"  {d.isoformat()} ({_GR_WEEKDAYS[d.weekday()]})  {table[d]}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Υπολογισμός δικονομικών προθεσμιών κατά τον ΚΠολΔ")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("compute", help="υπολόγισε λήξη από αφετηρία και ημέρες")
    pc.add_argument("--apo", required=True, help="αφετήρια ημερομηνία ΕΕΕΕ-ΜΜ-ΗΗ")
    pc.add_argument("--imeres", required=True, type=int, help="αριθμός ημερών")
    pc.add_argument("--anastoli-avgoustou", action="store_true",
                    help="μη προσμέτρηση 1-31 Αυγούστου (ΚΠολΔ 147, επιβεβαίωσε)")
    pc.add_argument("--json", action="store_true")

    pi = sub.add_parser("info", help="στοιχεία μιας συνήθους προθεσμίας")
    pi.add_argument("slug")
    pi.add_argument("--json", action="store_true")

    pl = sub.add_parser("list", help="κατάλογος συνήθων προθεσμιών")
    pl.add_argument("--json", action="store_true")

    pa = sub.add_parser("argies", help="εξαιρετέες ημέρες ενός έτους")
    pa.add_argument("year", type=int)
    pa.add_argument("--json", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "compute":
        try:
            start = dt.date.fromisoformat(args.apo)
        except ValueError:
            p.error(f"μη έγκυρη ημερομηνία: {args.apo} (χρησιμοποίησε ΕΕΕΕ-ΜΜ-ΗΗ)")
        try:
            r = compute(start, args.imeres, args.anastoli_avgoustou)
        except ValueError as exc:
            p.error(str(exc))
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.json
              else _render_compute(r))
        return 0

    if args.cmd == "info":
        entry = _REGISTRY.get(args.slug)
        if entry is None:
            sys.stderr.write(
                f"Άγνωστη προθεσμία: {args.slug}. "
                f"Διαθέσιμες: {', '.join(_REGISTRY)}.\n")
            return 2
        print(json.dumps({"slug": args.slug, **entry, "flag": _VERIFY},
                         ensure_ascii=False, indent=2) if args.json
              else _render_info(args.slug, entry))
        return 0

    if args.cmd == "list":
        print(json.dumps(_REGISTRY, ensure_ascii=False, indent=2) if args.json
              else _render_list())
        return 0

    if args.cmd == "argies":
        table = argies(args.year)
        if args.json:
            print(json.dumps({d.isoformat(): n for d, n in sorted(table.items())},
                             ensure_ascii=False, indent=2))
        else:
            print(_render_argies(args.year, table))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
