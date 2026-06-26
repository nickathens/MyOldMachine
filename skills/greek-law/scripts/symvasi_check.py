#!/usr/bin/env python3
"""έλεγχος σύμβασης: a contract-review scaffold for Greek law.

Two parts, mirroring aoristia_check.py and vasi_agogis.py:
  - a CHECKLIST (general plus per contract type) of the clauses a reviewer confirms,
  - a RISK_CONTROLS catalogue: clause patterns that Greek law polices (voids, reduces or
    scrutinises), each tied to its governing article,
plus a deterministic `scan` that reads a contract and flags which risk-control patterns
appear and which essential clauses seem absent. It is an orientation pre-pass.

It assigns no final GREEN, YELLOW or RED verdict. That colour needs the clause read in
context, which is the reviewer's (or the model's) judgment. The scanner orients; the
checklist and the risk catalogue give the framework to colour each finding, and the
doctrine lives in practice/symvaseis.md. Keyword detection has false negatives (a clause
phrased unusually is missed) and false positives (a word used in another sense), so a
clean scan is never a clean contract.

Usage:
  python symvasi_check.py checklist
  python symvasi_check.py checklist --typos misthosi
  python symvasi_check.py risks
  python symvasi_check.py scan CONTRACT.txt
  python symvasi_check.py scan CONTRACT.txt --json
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata


def normalize(text):
    """Accent and case fold Greek text, so a match works regardless of tonos."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.upper()


# Each control: a clause pattern Greek law polices when it is PRESENT. The signatures are
# OR of ANDs: the control fires if any inner list has all its substrings in the contract
# (accent and case folded). ceiling names the worst case the control can reach once the
# clause is actually read: ΑΚΥΡΟΤΗΤΑ (void), ΜΕΙΩΣΗ (reduced by the court), or ΕΛΕΓΧΟΣ
# (scrutinised for validity). The scanner only reports that the pattern is present and
# must be reviewed; it never asserts the ceiling has been reached.
RISK_CONTROLS = [
    {
        "id": "apallaktiki-ritra",
        "name": "Απαλλακτική ή περιοριστική ρήτρα ευθύνης",
        "article": "ΑΚ 332",
        "ceiling": "ΑΚΥΡΟΤΗΤΑ",
        "rule": "Άκυρη η εκ των προτέρων συμφωνία που απαλλάσσει τον οφειλέτη από ευθύνη "
                "για δόλο ή βαριά αμέλεια (ΑΚ 332). Σε γενικούς όρους συναλλαγών ελέγχεται "
                "ως καταχρηστικός ακόμη και ο περιορισμός για ελαφρά αμέλεια.",
        "signatures": [["απαλλ", "ευθυν"], ["ουδεμια ευθυν"], ["καμια ευθυν"],
                       ["δεν ευθυνεται"], ["δεν φερει", "ευθυν"], ["περιορ", "ευθυν"],
                       ["αποκλει", "ευθυν"]],
        "review": "Αποκλείει ή περιορίζει την ευθύνη για δόλο ή βαριά αμέλεια; Τότε είναι "
                  "άκυρη κατά ΑΚ 332.",
    },
    {
        "id": "poiniki-ritra",
        "name": "Ποινική ρήτρα",
        "article": "ΑΚ 409",
        "ceiling": "ΜΕΙΩΣΗ",
        "rule": "Η δυσανάλογα μεγάλη ποινική ρήτρα μειώνεται από το δικαστήριο ύστερα από "
                "αίτηση του οφειλέτη (ΑΚ 409). Η εκ των προτέρων παραίτηση από το δικαίωμα "
                "μείωσης δεν ισχύει.",
        "signatures": [["ποινικη ρητρα"], ["ποινικης ρητρας"], ["ποινικη ποινη"],
                       ["καταπιπτει", "ποινη"]],
        "review": "Είναι το ποσό δυσανάλογο προς την πιθανή ζημία; Υπόκειται σε δικαστική "
                  "μείωση κατά ΑΚ 409.",
    },
    {
        "id": "gos-katachrastikoi",
        "name": "Καταχρηστικοί Γενικοί Όροι Συναλλαγών",
        "article": "Ν.2251/1994 άρθρο 2",
        "ceiling": "ΑΚΥΡΟΤΗΤΑ",
        "rule": "Σε σύμβαση με καταναλωτή, οι προδιατυπωμένοι γενικοί όροι που διαταράσσουν "
                "ουσιωδώς την ισορροπία των δικαιωμάτων εις βάρος του καταναλωτή είναι "
                "άκυροι (Ν.2251/1994 άρθρο 2). Ο νόμος έχει ενδεικτικό κατάλογο "
                "καταχρηστικών όρων (επιβεβαιώστε την παράγραφο στο ισχύον κείμενο).",
        "signatures": [["γενικοι οροι συναλλαγων"], ["γενικους ορους"], ["καταναλωτ"]],
        "review": "Είναι ο αντισυμβαλλόμενος καταναλωτής και οι όροι προδιατυπωμένοι και "
                  "μονομερείς; Έλεγχος καταχρηστικότητας κατά Ν.2251/1994 άρθρο 2.",
    },
    {
        "id": "monomeris-tropopoiisi",
        "name": "Μονομερής τροποποίηση όρων",
        "article": "Ν.2251/1994 άρθρο 2",
        "ceiling": "ΑΚΥΡΟΤΗΤΑ",
        "rule": "Όρος που επιτρέπει σε ένα μέρος να μεταβάλλει μονομερώς ουσιώδη στοιχεία "
                "(τίμημα, παροχή) χωρίς σπουδαίο και ρητά οριζόμενο λόγο είναι ενδεικτικά "
                "καταχρηστικός σε καταναλωτικές συμβάσεις (Ν.2251/1994 άρθρο 2).",
        "signatures": [["μονομερ", "τροποποι"], ["δικαιωμα να τροποποι"],
                       ["διατηρει το δικαιωμα", "μεταβαλ"], ["μεταβαλλει", "οποτε"]],
        "review": "Επιτρέπει μονομερή μεταβολή ουσιωδών όρων; Ελέγξτε αν υπάρχει σπουδαίος "
                  "λόγος και δικαίωμα καταγγελίας του αντισυμβαλλομένου.",
    },
    {
        "id": "siopiri-ananeosi",
        "name": "Σιωπηρή ή αυτόματη ανανέωση",
        "article": "Ν.2251/1994 άρθρο 2",
        "ceiling": "ΕΛΕΓΧΟΣ",
        "rule": "Όρος αυτόματης ανανέωσης με δυσχερή ή ασύμμετρη δυνατότητα εξόδου "
                "ελέγχεται ως καταχρηστικός σε καταναλωτικές συμβάσεις.",
        "signatures": [["αυτοματ", "ανανε"], ["σιωπηρ", "ανανε"], ["σιωπηρ", "παρατ"],
                       ["παρατεινεται αυτοδικαιως"], ["ανανεωνεται αυτοματα"]],
        "review": "Ανανεώνεται χωρίς ενέργεια του αντισυμβαλλομένου, με δυσανάλογη "
                  "προθεσμία ή τρόπο καταγγελίας; Ελέγξτε τη διαφάνεια του όρου.",
    },
    {
        "id": "mi-antagonismos",
        "name": "Ρήτρα μη ανταγωνισμού",
        "article": "ΑΚ 178 και 179, Σύνταγμα άρθρο 5",
        "ceiling": "ΑΚΥΡΟΤΗΤΑ",
        "rule": "Έγκυρη μόνο όταν είναι περιορισμένη σε χρόνο, τόπο και αντικείμενο και "
                "δικαιολογείται από προστατευτέο συμφέρον. Η υπέρμετρη δέσμευση της "
                "επαγγελματικής ελευθερίας είναι άκυρη ως αντίθετη στα χρηστά ήθη (ΑΚ "
                "178 και 179) και στην οικονομική ελευθερία (Σύνταγμα άρθρο 5).",
        "signatures": [["μη ανταγωνισμ"], ["απαγορευ", "ανταγωνισμ"], ["ρητρα", "ανταγωνισμ"]],
        "review": "Περιορίζεται σε χρόνο, γεωγραφία και αντικείμενο; Αόριστη ή υπέρμετρη "
                  "δέσμευση είναι άκυρη.",
    },
    {
        "id": "parektasi-armodiotitas",
        "name": "Παρέκταση αρμοδιότητας ή ρήτρα διαιτησίας",
        "article": "Ν.2251/1994 άρθρο 2",
        "ceiling": "ΕΛΕΓΧΟΣ",
        "rule": "Όρος που επιβάλλει απομακρυσμένο αποκλειστικά αρμόδιο δικαστήριο ή "
                "διαιτησία σε καταναλωτή ελέγχεται ως καταχρηστικός. Σε εμπορικές "
                "συμβάσεις η παρέκταση είναι κατ' αρχήν επιτρεπτή.",
        "signatures": [["αποκλειστικ", "αρμοδι"], ["διαιτησ"], ["παρεκταση", "αρμοδι"]],
        "review": "Επιβάλλεται σε καταναλωτή απομακρυσμένο δικαστήριο ή διαιτησία; Ελέγξτε "
                  "την εγκυρότητα της ρήτρας.",
    },
]

# Essential clauses the scanner looks for by high-signal markers. Absence is reported as
# "appears absent, confirm", never as a hard omission: a contract can address any of these
# with wording the markers miss. This is orientation, not a structural verdict.
ESSENTIALS = [
    {"name": "Συμβαλλόμενοι και ΑΦΜ",
     "markers": ["αφμ", "α.φ.μ", "μεταξυ", "αφενος"],
     "note": "Πλήρη στοιχεία και φορολογικά μητρώα των μερών, νόμιμη εκπροσώπηση."},
    {"name": "Αντικείμενο ή παροχή",
     "markers": ["αντικειμενο", "παροχη", "υποχρεουται να", "αναλαμβανει"],
     "note": "Ορισμένη ή οριστή παροχή (ΑΚ 371)."},
    {"name": "Τίμημα ή αντιπαροχή",
     "markers": ["τιμημα", "αμοιβη", "ποσο", "ευρω", "μισθωμα", "τιμη"],
     "note": "Σαφές ποσό, ΦΠΑ, τρόπος και χρόνος καταβολής."},
    {"name": "Διάρκεια και λύση",
     "markers": ["διαρκεια", "καταγγελ", "λυση", "υπαναχωρ", "ληξη"],
     "note": "Διάρκεια, λόγοι και τρόπος καταγγελίας ή υπαναχώρησης."},
    {"name": "Εφαρμοστέο δίκαιο και αρμοδιότητα",
     "markers": ["εφαρμοστεο δικαιο", "διεπεται", "αρμοδι", "δικαστηρια"],
     "note": "Εφαρμοστέο δίκαιο και αρμόδια δικαστήρια."},
    {"name": "Υπογραφές",
     "markers": ["υπογραφ"],
     "note": "Υπογραφές των μερών, και ο τύπος που τυχόν απαιτεί ο νόμος."},
]


GENERAL_CHECKLIST = [
    {"topic": "Ικανότητα και εκπροσώπηση",
     "check": "Έχουν τα μέρη δικαιοπρακτική ικανότητα και νόμιμη εκπροσώπηση; (ΑΚ 127 κ.ε.)"},
    {"topic": "Αντικείμενο",
     "check": "Είναι η παροχή ορισμένη ή τουλάχιστον οριστή και δυνατή; (ΑΚ 371)"},
    {"topic": "Τύπος",
     "check": "Απαιτεί ο νόμος συμβολαιογραφικό τύπο; Ακίνητα (ΑΚ 369), δωρεά (ΑΚ 498). "
              "Έλλειψη τύπου επιφέρει ακυρότητα (ΑΚ 159)."},
    {"topic": "Νομιμότητα και χρηστά ήθη",
     "check": "Αντιβαίνει όρος σε απαγορευτικό κανόνα (ΑΚ 174) ή στα χρηστά ήθη (ΑΚ 178), "
              "ή είναι αισχροκερδής (ΑΚ 179);"},
    {"topic": "Καλή πίστη",
     "check": "Είναι η κατανομή δικαιωμάτων και κινδύνων σύμφωνη με την καλή πίστη και τα "
              "συναλλακτικά ήθη; (ΑΚ 200, 288)"},
    {"topic": "Ευθύνη",
     "check": "Υπάρχει απαλλακτική ή περιοριστική ρήτρα ευθύνης; Έλεγχος κατά ΑΚ 332."},
    {"topic": "Ποινική ρήτρα",
     "check": "Αν υπάρχει, είναι ανάλογη; Υπόκειται σε μείωση (ΑΚ 409)."},
    {"topic": "Απρόβλεπτη μεταβολή",
     "check": "Προβλέπεται αναπροσαρμογή σε απρόβλεπτη μεταβολή συνθηκών; Άλλως ισχύει η "
              "ΑΚ 388."},
    {"topic": "Προσωπικά δεδομένα",
     "check": "Αν γίνεται επεξεργασία δεδομένων, υπάρχει ρήτρα ΓΚΠΔ και, όπου χρειάζεται, "
              "σύμβαση εκτέλεσης επεξεργασίας; (ΓΚΠΔ, Ν.4624/2019)"},
    {"topic": "Εφαρμοστέο δίκαιο και επίλυση διαφορών",
     "check": "Ορίζονται εφαρμοστέο δίκαιο, αρμοδιότητα ή διαιτησία, σαφώς και έγκυρα;"},
]


TYPE_CHECKLISTS = {
    "misthosi": {
        "name": "Μίσθωση (κατοικίας ή επαγγελματική)",
        "items": [
            "Μίσθωμα, χρόνος και τρόπος καταβολής, ρήτρα αναπροσαρμογής.",
            "Διάρκεια. Η μίσθωση κατοικίας έχει ελάχιστη νόμιμη τριετία ακόμη και αν "
            "συμφωνηθεί μικρότερη (επιβεβαιώστε ΑΚ 608).",
            "Εγγύηση: ποσό, τρόπος και προθεσμία επιστροφής.",
            "Δαπάνες και επισκευές: ποιες βαρύνουν τον εκμισθωτή και ποιες τον μισθωτή "
            "(επιβεβαιώστε ΑΚ 591 και 592), κοινόχρηστα.",
            "Παράδοση και απόδοση του μισθίου, κατάσταση, πρωτόκολλο.",
            "Επαγγελματική μίσθωση: ισχύον καθεστώς μετά τις τροποποιήσεις του Π.Δ. "
            "34/1995 (επιβεβαιώστε, ιδίως μετά τον Ν.4242/2014).",
        ],
    },
    "ergasias": {
        "name": "Σύμβαση εργασίας",
        "items": [
            "Είδος σύμβασης: ορισμένου ή αορίστου χρόνου, πλήρης ή μερική απασχόληση.",
            "Αποδοχές: σύμφωνες με την οικεία ΣΣΕ και τον νόμιμο κατώτατο μισθό.",
            "Ωράριο, υπερωρίες, αναπληρωματική ανάπαυση.",
            "Δοκιμαστική περίοδος: διάρκεια και συνέπειες.",
            "Ρήτρα μη ανταγωνισμού: έγκυρη μόνο αν περιορισμένη σε χρόνο, τόπο, αντικείμενο "
            "(ΑΚ 178 και 179, Σύνταγμα 5).",
            "Καταγγελία και αποζημίωση: έγγραφος τύπος και νόμιμη αποζημίωση (Ν.2112/1920, "
            "όπως ισχύει μετά τον Ν.4808/2021).",
            "Ρήτρα εμπιστευτικότητας και προστασία δεδομένων εργαζομένου.",
        ],
    },
    "polisis": {
        "name": "Πώληση",
        "items": [
            "Τίμημα: σαφές, ΦΠΑ, τρόπος και χρόνος καταβολής.",
            "Μεταβίβαση κυριότητας και χρόνος μετάθεσης του κινδύνου.",
            "Ευθύνη για πραγματικά ελαττώματα και έλλειψη συνομολογημένων ιδιοτήτων "
            "(ΑΚ 534 κ.ε.), και τα δικαιώματα του αγοραστή.",
            "Παράδοση: τόπος, χρόνος, έξοδα.",
            "Επιφύλαξη κυριότητας μέχρι αποπληρωμής, αν συμφωνείται.",
            "Ακίνητο: συμβολαιογραφικός τύπος και μεταγραφή ή κτηματολογική εγγραφή "
            "(ΑΚ 369, 1033).",
        ],
    },
    "ergou": {
        "name": "Σύμβαση έργου ή παροχής υπηρεσιών",
        "items": [
            "Αντικείμενο και προδιαγραφές του έργου ή των υπηρεσιών, παραδοτέα.",
            "Αμοιβή, ορόσημα πληρωμής, ΦΠΑ.",
            "Χρονοδιάγραμμα, παράδοση και παραλαβή, κριτήρια αποδοχής.",
            "Ευθύνη για ελαττώματα του έργου και προθεσμίες (ΑΚ 681 κ.ε.).",
            "Πνευματικά δικαιώματα και ιδιοκτησία του παραδοτέου (Ν.2121/1993).",
            "Διάκριση από εξαρτημένη εργασία: ανεξαρτησία ως προς τον τρόπο εκτέλεσης, "
            "ώστε να μην αναχαρακτηριστεί ως σύμβαση εργασίας.",
            "Υπεργολαβία και εμπιστευτικότητα.",
        ],
    },
    "aporritou": {
        "name": "Σύμβαση εμπιστευτικότητας (NDA)",
        "items": [
            "Ορισμός της εμπιστευτικής πληροφορίας, με σαφή όρια.",
            "Σκοπός της επιτρεπόμενης χρήσης.",
            "Εξαιρέσεις: ήδη γνωστές, δημόσιες ή νομίμως αποκτηθείσες πληροφορίες.",
            "Διάρκεια της υποχρέωσης εχεμύθειας.",
            "Επιστροφή ή καταστροφή του υλικού στη λήξη.",
            "Ποινική ρήτρα: ανάλογη, υπό την επιφύλαξη μείωσης (ΑΚ 409).",
            "Εφαρμοστέο δίκαιο και αρμοδιότητα.",
        ],
    },
}


def scan_text(text):
    """Run the deterministic orientation pass over a contract's text.

    Returns a dict with the risk-control patterns detected and the essential clauses that
    appear absent. It reports presence and apparent absence only; it assigns no colour.
    """
    hay = normalize(text)
    detected = []
    for c in RISK_CONTROLS:
        for sig in c["signatures"]:
            if all(normalize(part) in hay for part in sig):
                detected.append(c)
                break
    absent = []
    for e in ESSENTIALS:
        if not any(normalize(m) in hay for m in e["markers"]):
            absent.append(e)
    return {"detected": detected, "absent": absent}


def render_scan(result):
    lines = ["Έλεγχος σύμβασης (προκαταρκτική σάρωση, όχι τελική κρίση):", ""]
    if result["detected"]:
        lines.append("Ρήτρες υπό έλεγχο (εντοπίστηκε μοτίβο που ελέγχει ο νόμος):")
        for c in result["detected"]:
            lines.append(f"  [ΠΡΟΣΟΧΗ] {c['name']} ({c['article']})  ανώτατο: {c['ceiling']}")
            lines.append(f"            {c['review']}")
    else:
        lines.append("Δεν εντοπίστηκε γνωστό μοτίβο επικίνδυνης ρήτρας. Προσοχή: η σάρωση "
                     "βασίζεται σε λέξεις κλειδιά και μπορεί να παραλείπει ρήτρες με "
                     "ασυνήθιστη διατύπωση.")
    lines.append("")
    if result["absent"]:
        lines.append("Ουσιώδη στοιχεία που δεν εντοπίστηκαν (επιβεβαιώστε αν όντως λείπουν):")
        for e in result["absent"]:
            lines.append(f"  [ΕΛΛΕΙΨΗ;] {e['name']}")
            lines.append(f"            {e['note']}")
    else:
        lines.append("Όλα τα βασικά ουσιώδη στοιχεία εντοπίστηκαν με την πρώτη ανάγνωση.")
    lines += [
        "",
        f"Σύνοψη: {len(result['detected'])} ρήτρες υπό έλεγχο, "
        f"{len(result['absent'])} πιθανές ελλείψεις.",
        "Η τελική κατάταξη (GREEN, YELLOW, RED) γίνεται από τον αναγνώστη, αφού διαβάσει "
        "την κάθε ρήτρα στο πλαίσιό της. Δείτε practice/symvaseis.md και "
        "`symvasi_check.py risks`.",
    ]
    return "\n".join(lines)


def render_risks():
    lines = ["Έλεγχοι του ελληνικού δικαίου σε όρους συμβάσεων:", ""]
    for c in RISK_CONTROLS:
        lines.append(f"{c['article']:<28} {c['name']}  (ανώτατο: {c['ceiling']})")
        lines.append(f"    {c['rule']}")
        lines.append("")
    lines.append("Σάρωση μιας σύμβασης: python symvasi_check.py scan CONTRACT.txt")
    return "\n".join(lines)


def render_checklist(typos=None):
    lines = ["Λίστα ελέγχου σύμβασης", ""]
    lines.append("Γενικά (κάθε σύμβαση):")
    for item in GENERAL_CHECKLIST:
        lines.append(f"  - {item['topic']}: {item['check']}")
    if typos:
        t = TYPE_CHECKLISTS[typos]
        lines += ["", f"Ειδικά για {t['name']}:"]
        for it in t["items"]:
            lines.append(f"  - {it}")
    else:
        lines += ["", "Ειδικές λίστες ανά τύπο (--typos):"]
        for key, t in TYPE_CHECKLISTS.items():
            lines.append(f"  {key:<12} {t['name']}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Contract-review scaffold for Greek law (checklist, risks, scan)")
    p.add_argument("command", help="checklist | risks | scan FILE")
    p.add_argument("path", nargs="?", default=None,
                   help="the contract file to scan (use - for stdin)")
    p.add_argument("--typos", default=None,
                   help="contract type for a specialised checklist")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.command == "checklist":
        if args.typos and args.typos not in TYPE_CHECKLISTS:
            sys.stderr.write(
                f"Άγνωστος τύπος: '{args.typos}'. Διαθέσιμοι: "
                f"{', '.join(TYPE_CHECKLISTS)}\n")
            return 2
        if args.json:
            payload = {"general": GENERAL_CHECKLIST}
            if args.typos:
                payload["typos"] = args.typos
                payload["items"] = TYPE_CHECKLISTS[args.typos]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_checklist(args.typos))
        return 0

    if args.command == "risks":
        if args.json:
            print(json.dumps(RISK_CONTROLS, ensure_ascii=False, indent=2))
        else:
            print(render_risks())
        return 0

    if args.command == "scan":
        if not args.path:
            p.error("scan needs a file: symvasi_check.py scan CONTRACT.txt")
        try:
            if args.path == "-":
                text = sys.stdin.read()
            else:
                with open(args.path, encoding="utf-8") as fh:
                    text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(f"Δεν διαβάστηκε το αρχείο: {exc}\n")
            return 2
        result = scan_text(text)
        if args.json:
            print(json.dumps(
                {"detected": [c["id"] for c in result["detected"]],
                 "absent": [e["name"] for e in result["absent"]]},
                ensure_ascii=False, indent=2))
        else:
            print(render_scan(result))
        return 0

    sys.stderr.write(
        f"Άγνωστη εντολή: '{args.command}'. "
        f"Χρήση: checklist | risks | scan FILE\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
