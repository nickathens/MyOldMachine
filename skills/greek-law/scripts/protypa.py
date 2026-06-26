#!/usr/bin/env python3
"""πρότυπα εγγράφων: Greek legal document templates.

A small library of skeletons for the documents a Greek practice produces most often:
an αγωγή, an εξώδικη δήλωση, an αίτηση ασφαλιστικών μέτρων, an ανακοπή κατά διαταγής
πληρωμής, and an ιδιωτικό συμφωνητικό. Each δικόγραφο skeleton already carries the
codified structure of ΚΠολΔ 118 and 216 (court, kind, parties, ιστορικό, ΓΙΑ ΤΟΥΣ
ΛΟΓΟΥΣ ΑΥΤΟΥΣ, αίτημα, date, signature), so once the blanks are filled the document
passes the structural αοριστία preflight (aoristia_check.py) BY CONSTRUCTION. The
template gives the skeleton; the υπαγωγή and the legal sufficiency stay with the lawyer.

A template is NOT legal advice and NOT a finished pleading. The competent court, the
exact νομική βάση, the deadline, and every fact are the drafter's to set and to verify.
Confidentiality (Άρθρο 38 Κώδικα Δικηγόρων): fill real client data only on your own
machine and never transmit it onward.

Placeholders are written as {ΠΕΡΙΓΡΑΦΗ ΣΤΑ ΕΛΛΗΝΙΚΑ}. The text inside the braces is the
field description; `get` shows them as readable [blanks], `--raw` keeps the {markers}
for piping into `fill`.

Usage:
  python protypa.py list
  python protypa.py keys agogi-katavolis
  python protypa.py get agogi-katavolis
  python protypa.py get agogi-katavolis --raw
  python protypa.py get agogi-katavolis --sample
  python protypa.py fill agogi-katavolis --values stoixeia.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path


def normalize(text):
    """Accent and case fold Greek text, so a keyword match ignores the tonos."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.upper()


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TOKEN_RE = re.compile(r"\{([^{}]+)\}")

# Each entry: slug (and template filename), title, audience (professional | both),
# doc_class, aoristia (which aoristia_check.py --type applies: agogi | generic | None),
# a one line summary, and the validation tool the document pairs with.
META = [
    {
        "slug": "agogi-katavolis",
        "title": "Αγωγή καταβολής χρηματικού ποσού",
        "audience": "professional",
        "doc_class": "δικόγραφο",
        "aoristia": "agogi",
        "summary": "Αγωγή για την καταβολή ληξιπρόθεσμης χρηματικής οφειλής.",
        "pairs": "Μετά τη συμπλήρωση: aoristia_check.py (--type agogi) για τη δομή και "
                 "vasi_agogis.py για τη βάση της αξίωσης.",
    },
    {
        "slug": "exodiki-dilosi",
        "title": "Εξώδικη δήλωση, πρόσκληση και διαμαρτυρία",
        "audience": "both",
        "doc_class": "εξώδικο",
        "aoristia": None,
        "summary": "Εξώδικη όχληση ή πρόσκληση πριν τη δικαστική οδό. Επιδίδεται, δεν "
                   "κατατίθεται σε δικαστήριο.",
        "pairs": "Δεν υπόκειται στον δικαστικό έλεγχο αοριστίας. Συχνά το βήμα πριν από "
                 "αγωγή ή ασφαλιστικά μέτρα.",
    },
    {
        "slug": "aitisi-asfalistikon",
        "title": "Αίτηση ασφαλιστικών μέτρων",
        "audience": "professional",
        "doc_class": "δικόγραφο",
        "aoristia": "generic",
        "summary": "Αίτηση για προσωρινή δικαστική προστασία όταν συντρέχει επείγον ή "
                   "επικείμενος κίνδυνος.",
        "pairs": "Μετά τη συμπλήρωση: aoristia_check.py (--type generic). Αρκεί "
                 "πιθανολόγηση, όχι πλήρης απόδειξη.",
    },
    {
        "slug": "anakopi-diatagis-pliromis",
        "title": "Ανακοπή κατά διαταγής πληρωμής (ΚΠολΔ 632)",
        "audience": "professional",
        "doc_class": "δικόγραφο",
        "aoristia": "generic",
        "summary": "Ανακοπή κατά εκδοθείσας διαταγής πληρωμής, με αυστηρή προθεσμία.",
        "pairs": "Κρίσιμη προθεσμία (άρθρο 632 ΚΠολΔ): υπολογίστε την με prothesmies.py. "
                 "Μετά: aoristia_check.py (--type generic).",
    },
    {
        "slug": "idiotiko-symfonitiko",
        "title": "Ιδιωτικό συμφωνητικό",
        "audience": "both",
        "doc_class": "σύμβαση",
        "aoristia": None,
        "summary": "Σκελετός ιδιωτικής σύμβασης: αντικείμενο, διάρκεια, αμοιβή, "
                   "καταγγελία, εφαρμοστέο δίκαιο.",
        "pairs": "Δεν είναι δικόγραφο. Ελέγξτε το προσχέδιο με symvasi_check.py scan για "
                 "ρήτρες που πολεμά ο νόμος.",
    },
]

_BY_SLUG = {m["slug"]: m for m in META}


def _meta(slug):
    return _BY_SLUG.get(slug)


def load(slug):
    """Read a template's raw text (with the {markers} intact)."""
    with open(TEMPLATES_DIR / f"{slug}.txt", encoding="utf-8") as fh:
        return fh.read()


def tokens(text):
    """The ordered, de duplicated list of placeholder field descriptions."""
    seen, out = set(), []
    for m in TOKEN_RE.finditer(text):
        key = m.group(1)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def pretty(text):
    """Show placeholders as readable [blanks] for a human to fill."""
    return TOKEN_RE.sub(lambda m: f"[{m.group(1)}]", text)


def fill(text, values):
    """Substitute provided fields. An unprovided field stays loud, never silent."""
    def repl(m):
        key = m.group(1)
        val = values.get(key)
        if val is not None and str(val).strip():
            return str(val)
        return f"[ΣΥΜΠΛΗΡΩΣΤΕ: {key}]"
    return TOKEN_RE.sub(repl, text)


def sample_values(text):
    """Illustrative dummy values, by keyword, for a fully rendered ΥΠΟΔΕΙΓΜΑ.

    Fictional data only. The amounts, dates and ΑΦΜ satisfy the structural αοριστία
    regexes so the sample demonstrates a skeleton that is structurally complete.
    """
    out = {}
    for key in tokens(text):
        n = normalize(key)
        if "ΑΦΜ" in n:
            v = "123456789"
        elif "ΑΡΙΘΜΟΣ" in n:
            v = "1234/2026"
        elif "ΗΜΕΡΟΜΗΝΙΑ" in n:
            v = "26/06/2026"
        elif "ΠΟΣΟ" in n:
            v = "5.000"
        elif "ΔΙΚΑΣΤΗΡΙΟ" in n or "ΔΙΚΑΣΤΗΣ" in n:
            v = "ΕΙΡΗΝΟΔΙΚΕΙΟΥ ΑΘΗΝΩΝ"
        elif "ΤΟΠΟΣ" in n or "ΠΟΛΗ" in n:
            v = "Αθήνα"
        elif "ΔΙΕΥΘΥΝΣΗ" in n or "ΚΑΤΟΙΚΙΑ" in n:
            v = "Ερμού 1, Αθήνα"
        elif "ΟΝΟΜΑΤΕΠΩΝΥΜΟ" in n or "ΟΝΟΜΑ" in n:
            v = "Ιωάννη Παπαδόπουλου"
        else:
            v = "υπόδειγμα κειμένου"
        out[key] = v
    return out


def _footer(meta):
    return "\n".join([
        f"Συνέχεια: {meta['pairs']}",
        "Εμπιστευτικότητα (Άρθρο 38 Κώδικα Δικηγόρων): πραγματικά στοιχεία πελάτη μόνο "
        "στο δικό σας μηχάνημα, χωρίς διαβίβαση.",
        "Πρότυπο, όχι νομική συμβουλή. Η ουσία και η νομική θεμελίωση είναι ευθύνη του "
        "συντάκτη δικηγόρου.",
    ])


def render_list(entries):
    lines = ["Πρότυπα εγγράφων (skeletons με τα δομικά στοιχεία ΚΠολΔ 118/216):", ""]
    for e in entries:
        lines.append(f"  {e['slug']:<27} {e['audience']:<13} {e['title']}")
    lines += ["", "Δείτε ένα:  python protypa.py get <slug>",
              "Πεδία:      python protypa.py keys <slug>",
              "Υπόδειγμα:  python protypa.py get <slug> --sample"]
    return "\n".join(lines)


def render_keys(slug, text, meta):
    toks = tokens(text)
    lines = [
        f"{meta['title']}  ({meta['audience']})",
        f"slug: {slug}   κατηγορία: {meta['doc_class']}",
        "",
        f"Πεδία προς συμπλήρωση ({len(toks)}):",
    ]
    for t in toks:
        lines.append(f"  [{t}]")
    lines += ["", _footer(meta)]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Greek legal document templates (πρότυπα)")
    p.add_argument("command", help="list | keys <slug> | get <slug> | fill <slug>")
    p.add_argument("slug", nargs="?", default=None)
    p.add_argument("--raw", action="store_true",
                   help="get: print with {ΤΟΚΕΝ} markers, for piping into fill")
    p.add_argument("--sample", action="store_true",
                   help="get: fill with illustrative dummy data (ΥΠΟΔΕΙΓΜΑ)")
    p.add_argument("--values",
                   help="fill: path to a JSON object mapping each field to its value")
    p.add_argument("--json", action="store_true", help="list/keys: machine readable")
    args = p.parse_args(argv)

    if args.command not in ("list", "keys", "get", "fill"):
        p.error("command must be one of: list, keys, get, fill")

    if args.command == "list":
        if args.json:
            print(json.dumps(META, ensure_ascii=False, indent=2))
        else:
            print(render_list(META))
        return 0

    if not args.slug:
        p.error(f"{args.command} needs a slug: python protypa.py {args.command} <slug>")
    meta = _meta(args.slug)
    if meta is None:
        sys.stderr.write(f"Άγνωστο πρότυπο: '{args.slug}'. "
                         f"Δείτε τη λίστα: python protypa.py list\n")
        return 2
    text = load(args.slug)

    if args.command == "keys":
        if args.json:
            print(json.dumps({"slug": args.slug, "fields": tokens(text)},
                             ensure_ascii=False, indent=2))
        else:
            print(render_keys(args.slug, text, meta))
        return 0

    if args.command == "get":
        if args.raw:
            sys.stdout.write(text)
        elif args.sample:
            print("ΥΠΟΔΕΙΓΜΑ ΜΕ ΕΙΚΟΝΙΚΑ ΣΤΟΙΧΕΙΑ, ΟΧΙ ΓΙΑ ΚΑΤΑΘΕΣΗ")
            print("=" * 52)
            print(fill(text, sample_values(text)))
            print("=" * 52)
            print(_footer(meta))
        else:
            print(pretty(text))
            print()
            print(_footer(meta))
        return 0

    # fill
    if not args.values:
        p.error("fill needs --values path.json")
    if not os.path.exists(args.values):
        p.error(f"values file not found: {args.values}")
    with open(args.values, encoding="utf-8") as fh:
        values = json.load(fh)
    if not isinstance(values, dict):
        p.error("values JSON must be an object mapping each field to its value")
    sys.stdout.write(fill(text, {str(k): v for k, v in values.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
