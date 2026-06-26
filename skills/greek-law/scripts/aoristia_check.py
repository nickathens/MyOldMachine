#!/usr/bin/env python3
"""Structural αοριστία preflight for a Greek δικόγραφο (mainly an αγωγή).

Greek civil procedure strikes a pleading as απαράδεκτη when it fails the codified
anatomy of ΚΠολΔ 118 (elements of every δικόγραφο) and, for an αγωγή, ΚΠολΔ 216
(σαφής έκθεση γεγονότων, ακριβής περιγραφή αντικειμένου, ορισμένο αίτημα). That
defect is αοριστία, and the court raises it αυτεπαγγέλτως.

This is a DETERMINISTIC structural check. It verifies the mechanical elements that
need no legal judgement: is a court named, is the document kind named, are both
parties present, is there an ΑΦΜ, an operative αίτημα, a date, a signature, and for
a money claim a specified sum. It CANNOT judge legal sufficiency (whether every
προϋπόθεση of the invoked rule has a pleaded fact). That stays with the model,
guided by practice/politiki-dikonomia.md. A clean structural report means only that
the skeleton is present, never that the αγωγή is ορισμένη.

Usage:
  python aoristia_check.py draft.txt
  python aoristia_check.py draft.txt --type generic
  cat draft.txt | python aoristia_check.py -
  python aoristia_check.py draft.txt --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata


def normalize(text):
    """Accent and case fold Greek text for robust matching.

    Decomposes (NFD), drops combining marks (the tonos and dialytika), then
    uppercases. So ά, Ά and α all fold to Α, and final ς folds to Σ. The patterns
    below are written in this accent free uppercase form.
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.upper()


# Patterns, all in normalized (accent free, uppercase) form.
_AFM_NUM = re.compile(r"ΑΦΜ\D{0,8}\d{9}")
_AMOUNT = re.compile(r"\d[\d.\s]*[,.]?\d*\s*(?:ΕΥΡΩ|EUR|€)")
_DATE = re.compile(
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
    r"|\b\d{1,2}\s+(?:ΙΑΝΟΥΑΡΙΟΥ|ΦΕΒΡΟΥΑΡΙΟΥ|ΜΑΡΤΙΟΥ|ΑΠΡΙΛΙΟΥ|ΜΑΙΟΥ|ΙΟΥΝΙΟΥ"
    r"|ΙΟΥΛΙΟΥ|ΑΥΓΟΥΣΤΟΥ|ΣΕΠΤΕΜΒΡΙΟΥ|ΟΚΤΩΒΡΙΟΥ|ΝΟΕΜΒΡΙΟΥ|ΔΕΚΕΜΒΡΙΟΥ)\s+\d{4}\b"
)
_MONEY_CLAIM = ("ΑΠΟΖΗΜΙΩΣ", "ΚΑΤΑΒΑΛ", "ΟΦΕΙΛ", "ΧΡΗΜΑΤΙΚ", "ΠΟΣΟ")
_ASK_VERBS = ("ΖΗΤΩ", "ΖΗΤΟΥΜΕ", "ΖΗΤΕΙΤΑΙ", "ΖΗΤΟΥΝ")
_DOC_KINDS = ("ΑΓΩΓΗ", "ΑΙΤΗΣΗ", "ΑΝΑΚΟΠΗ", "ΕΦΕΣΗ", "ΠΡΟΣΦΥΓΗ", "ΕΞΩΔΙΚ",
              "ΠΡΟΤΑΣΕΙΣ", "ΑΝΑΙΡΕΣΗ")
_OPERATIVE = "ΓΙΑ ΤΟΥΣ ΛΟΓΟΥΣ ΑΥΤΟΥΣ"


def _has(norm, *needles):
    return any(n in norm for n in needles)


def check(text, doc_type="agogi"):
    """Return the structural findings for a draft. Each finding is a dict with
    code, label, status (ok|weak|missing), severity (critical|warning|info),
    article and a Greek note."""
    norm = normalize(text)
    line_set = {normalize(ln).strip() for ln in text.splitlines()}
    findings = []

    def add(code, label, status, severity, article, note=""):
        findings.append({
            "code": code, "label": label, "status": status,
            "severity": severity, "article": article, "note": note,
        })

    # ΚΠολΔ 118: the court.
    if _has(norm, "ΕΝΩΠΙΟΝ", "ΔΙΚΑΣΤΗΡΙ"):
        add("court", "Δικαστήριο", "ok", "critical", "ΚΠολΔ 118")
    else:
        add("court", "Δικαστήριο", "missing", "critical", "ΚΠολΔ 118",
            "Δεν εντοπίστηκε προσφώνηση δικαστηρίου (ΕΝΩΠΙΟΝ ...).")

    # ΚΠολΔ 118: the kind of document.
    type_ok = ("ΑΓΩΓΗ" in norm) if doc_type == "agogi" else _has(norm, *_DOC_KINDS)
    add("doc_type", "Είδος δικογράφου", "ok" if type_ok else "missing",
        "warning", "ΚΠολΔ 118",
        "" if type_ok else "Δεν δηλώνεται ρητά το είδος του δικογράφου.")

    # ΚΠολΔ 118: the parties. The standalone ΚΑΤΑ heading frames both sides.
    party_struct = "ΚΑΤΑ" in line_set
    plaintiff = _has(norm, "ΕΝΑΓΩΝ", "ΕΝΑΓΟΥΣ", "ΕΝΑΓΟΝΤ") or party_struct
    defendant = ("ΕΝΑΓΟΜΕΝ" in norm) or party_struct
    if doc_type == "agogi":
        add("plaintiff", "Ενάγων", "ok" if plaintiff else "missing", "critical",
            "ΚΠολΔ 118", "" if plaintiff else "Δεν προσδιορίζεται ο ενάγων.")
        add("defendant", "Εναγόμενος", "ok" if defendant else "missing", "critical",
            "ΚΠολΔ 118", "" if defendant else "Δεν προσδιορίζεται ο εναγόμενος.")
    else:
        any_party = plaintiff or defendant or _has(norm, "ΑΙΤ", "ΑΝΑΚΟΠΤ")
        add("parties", "Διάδικοι", "ok" if any_party else "weak", "warning",
            "ΚΠολΔ 118", "" if any_party else "Δεν εντοπίστηκαν σαφώς οι διάδικοι.")

    # ΚΠολΔ 118: the ΑΦΜ of the parties.
    if _AFM_NUM.search(norm):
        add("afm", "ΑΦΜ διαδίκων", "ok", "warning", "ΚΠολΔ 118")
    elif "ΑΦΜ" in norm:
        add("afm", "ΑΦΜ διαδίκων", "weak", "warning", "ΚΠολΔ 118",
            "Αναφέρεται ΑΦΜ χωρίς εννέα ψηφία κοντά του.")
    else:
        add("afm", "ΑΦΜ διαδίκων", "missing", "warning", "ΚΠολΔ 118",
            "Δεν εντοπίστηκε ΑΦΜ διαδίκου.")

    if doc_type == "agogi":
        # ΚΠολΔ 216(α): the founding facts.
        grounds = "ΕΠΕΙΔΗ" in norm
        body = norm.split(_OPERATIVE, 1)[0]
        if grounds or len(body) >= 400:
            add("facts", "Ιστορικό θεμελιωτικών γεγονότων", "ok", "info",
                "ΚΠολΔ 216",
                "Παρόν. Η νομική επάρκεια ελέγχεται ουσιαστικά, όχι μηχανικά.")
        else:
            add("facts", "Ιστορικό θεμελιωτικών γεγονότων", "missing", "critical",
                "ΚΠολΔ 216",
                "Δεν εντοπίστηκε επαρκές ιστορικό πραγματικών περιστατικών.")

        # ΚΠολΔ 216(γ): the definite petition.
        heading = _OPERATIVE in norm
        asks = _has(norm, *_ASK_VERBS)
        if heading and asks:
            add("aitima", "Ορισμένο αίτημα", "ok", "critical", "ΚΠολΔ 216")
        elif heading or asks:
            add("aitima", "Ορισμένο αίτημα", "weak", "critical", "ΚΠολΔ 216",
                "Εντοπίστηκε μέρος του αιτητικού. Βεβαιωθείτε ότι είναι ορισμένο.")
        else:
            add("aitima", "Ορισμένο αίτημα", "missing", "critical", "ΚΠολΔ 216",
                "Δεν εντοπίστηκε αιτητικό. Κρίσιμος λόγος αοριστίας.")

    # ΚΠολΔ 118: the date.
    add("date", "Χρονολογία", "ok" if _DATE.search(norm) else "missing",
        "warning", "ΚΠολΔ 118",
        "" if _DATE.search(norm) else "Δεν εντοπίστηκε χρονολογία.")

    # ΚΠολΔ 118: the signature.
    sig = _has(norm, "ΔΙΚΗΓΟΡΟ", "ΠΛΗΡΕΞΟΥΣΙ", "ΥΠΟΓΡΑΦ")
    add("signature", "Υπογραφή / πληρεξούσιος δικηγόρος",
        "ok" if sig else "missing", "warning", "ΚΠολΔ 118",
        "" if sig else "Δεν εντοπίστηκε υπογραφή ή δικηγόρος.")

    # ΚΠολΔ 216: quantitative αοριστία on a money claim.
    if doc_type == "agogi" and _has(norm, *_MONEY_CLAIM) and _has(norm, "ΕΥΡΩ", "€", "EUR"):
        if _AMOUNT.search(norm):
            add("quantum", "Ποσοτικός προσδιορισμός χρηματικού αιτήματος", "ok",
                "info", "ΚΠολΔ 216")
        else:
            add("quantum", "Ποσοτικός προσδιορισμός χρηματικού αιτήματος", "weak",
                "warning", "ΚΠολΔ 216",
                "Χρηματική αξίωση χωρίς προσδιορισμένο ποσό: κίνδυνος ποσοτικής αοριστίας.")

    return findings


def summarize(findings):
    crit = [f for f in findings if f["severity"] == "critical" and f["status"] != "ok"]
    warn = [f for f in findings if f["severity"] == "warning" and f["status"] != "ok"]
    nc, nw = len(crit), len(warn)
    crit_txt = f"{nc} κρίσιμη έλλειψη" if nc == 1 else f"{nc} κρίσιμες ελλείψεις"
    warn_txt = f"{nw} προειδοποίηση" if nw == 1 else f"{nw} προειδοποιήσεις"
    if crit:
        verdict = f"ΑΟΡΙΣΤΙΑ: {crit_txt}, {warn_txt}."
    elif warn:
        verdict = f"ΠΡΟΣΟΧΗ: {warn_txt}, καμία κρίσιμη έλλειψη."
    else:
        verdict = "Η δομή είναι πλήρης. Η ουσιαστική επάρκεια χρειάζεται νομικό έλεγχο."
    return {"critical": nc, "warnings": nw, "verdict": verdict}


_TAG = {"ok": "[ΟΚ]    ", "weak": "[ΑΣΑΦΕΣ]", "missing": "[ΛΕΙΠΕΙ]"}


def render(findings, summary):
    lines = ["Έλεγχος αοριστίας (δομικός)", ""]
    for f in findings:
        lines.append(f"{_TAG.get(f['status'], '')} {f['label']} ({f['article']})")
        if f["note"]:
            lines.append(f"         {f['note']}")
    lines += ["", summary["verdict"], "",
              "Δομικός έλεγχος μόνο. Η νομική επάρκεια του ιστορικού "
              "(ποιοτική και ποσοτική αοριστία) κρίνεται με υπαγωγή, όχι μηχανικά."]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Structural αοριστία check for a Greek δικόγραφο")
    p.add_argument("path", help="draft file, or - for stdin")
    p.add_argument("--type", choices=("agogi", "generic"), default="agogi",
                   help="agogi applies ΚΠολΔ 216; generic checks only the "
                        "ΚΠολΔ 118 elements common to any δικόγραφο")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.path == "-":
        text = sys.stdin.read()
    elif not os.path.exists(args.path):
        p.error(f"file not found: {args.path}")
    else:
        with open(args.path, encoding="utf-8") as fh:
            text = fh.read()

    findings = check(text, doc_type=args.type)
    summary = summarize(findings)
    if args.json:
        print(json.dumps({"findings": findings, "summary": summary},
                         ensure_ascii=False, indent=2))
    else:
        print(render(findings, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
