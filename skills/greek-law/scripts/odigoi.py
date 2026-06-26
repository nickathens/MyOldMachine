#!/usr/bin/env python3
"""οδηγοί πολίτη: plain-Greek everyday legal guides for a layperson.

The citizen-facing layer of the skill. A regular person rarely knows the name of
the law; they know the situation ("ο σπιτονοικοκύρης κρατάει την εγγύηση", "με
απέλυσαν"). This navigator maps the situation to the right guide, states the
rights in plain Greek with a light citation, gives the practical path (what to do,
where to go, what to bring), and, most important, flags the moment the matter
needs a δικηγόρος rather than self help.

Three responsibilities, kept honest:
  - ROUTING (list, find): match a plain-language problem to a guide.
  - GUIDANCE (show): the rights, the steps, and the hard escalation triggers.
  - ELIGIBILITY (voithia): a deterministic pre-check for legal aid (Ν.3226/2004),
    correct arithmetic over a reference figure the user confirms, never a euro
    amount asserted from memory.

It is orientation for a layperson, not legal advice. Every guide carries the line
that a deadline, a court, or real money means see a lawyer. Figures that move with
amendment are tagged [επαλήθευσε]; confirm them against current law before relying.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata

_VERIFY = "[επαλήθευσε]"


def normalize(text):
    """Accent and case fold Greek text, so a match works regardless of tonos."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.upper()


# Each guide: slug, title, area (the practice module it maps to), keywords (the
# plain words a citizen would type), a short summary, the rights (lightly cited),
# the practical steps, the escalation triggers (when self help stops and a lawyer
# starts), and the related skill tools. Citations are light by design: this layer
# explains, the professional layer cites in full.
GUIDES = [
    {
        "slug": "misthosi-engyisi",
        "title": "Μίσθωση κατοικίας και εγγύηση",
        "area": "Ενοχικό (μίσθωση)",
        "keywords": ["σπιτονοικοκυρης", "εγγυηση", "ενοικιο", "μισθωμα", "μισθωση",
                     "διαμερισμα", "κατοικια", "εκμισθωτης", "μισθωτης", "φθορες",
                     "επιστροφη εγγυησης", "καθυστερημενα ενοικια"],
        "summary": "Η σχέση μισθωτή και εκμισθωτή σε κατοικία, με έμφαση στην "
                   "επιστροφή της εγγύησης και στις φθορές.",
        "rights": [
            "Η εγγύηση είναι εξασφάλιση, όχι προπληρωμένο ενοίκιο. Επιστρέφεται "
            "στη λήξη, μειωμένη μόνο κατά πραγματικές οφειλές ή ζημίες που "
            "αποδεικνύει ο εκμισθωτής (ΑΚ 574 κ.ε. για τη μίσθωση πράγματος "
            + _VERIFY + ").",
            "Η φυσιολογική φθορά από τη συνήθη χρήση δεν αφαιρείται από την εγγύηση.",
            "Ο εκμισθωτής οφείλει να παραδώσει και να διατηρεί το μίσθιο κατάλληλο "
            "για τη συμφωνημένη χρήση.",
        ],
        "steps": [
            "Πρώτα γραπτή όχληση: εξώδικη δήλωση που ζητά την επιστροφή με "
            "προθεσμία. Δες το πρότυπο exodiki-dilosi στο protypa.py.",
            "Αν δεν επιστραφεί, η διαφορά πάει στο Ειρηνοδικείο (μισθωτικές "
            "διαφορές, ή μικροδιαφορές για μικρά ποσά).",
            "Κράτα: το συμφωνητικό, την απόδειξη της εγγύησης, φωτογραφίες "
            "παράδοσης και απόδοσης, όλη την επικοινωνία.",
        ],
        "escalation": [
            "Έξωση ή αγωγή απόδοσης μισθίου, ή κλήση σε δικαστήριο.",
            "Συσσωρευμένα οφειλόμενα ενοίκια, ή ποσά πάνω από το όριο των "
            "μικροδιαφορών.",
        ],
        "tools": ["protypa.py get exodiki-dilosi", "symvasi_check.py scan",
                  "practice/enochiko.md"],
    },
    {
        "slug": "apolysi-ergasia",
        "title": "Απόλυση και εργασιακά δικαιώματα",
        "area": "Εργατικό",
        "keywords": ["απολυση", "αποζημιωση απολυσης", "δεδουλευμενα", "μισθος",
                     "εργοδοτης", "καταγγελια", "υπερωριες", "δωρο", "επιδομα αδειας",
                     "αδηλωτη εργασια", "ενσημα", "οφειλομενοι μισθοι"],
        "summary": "Τι δικαιούσαι όταν σε απολύουν ή δεν σε πληρώνουν.",
        "rights": [
            "Η απόλυση χρειάζεται έγγραφο τύπο και καταβολή της νόμιμης "
            "αποζημίωσης για να είναι έγκυρη (Ν.2112/1920, όπως ισχύει μετά τον "
            "Ν.4808/2021 " + _VERIFY + ").",
            "Οι δεδουλευμένες αποδοχές οφείλονται πάντα: μισθός, υπερωρίες, δώρα, "
            "επίδομα και αποζημίωση αδείας. Δεν χάνονται με την απόλυση.",
            "Μπορείς να αμφισβητήσεις την εγκυρότητα της απόλυσης ως καταχρηστικής "
            "(ΑΚ 281), αλλά με σύντομη ανατρεπτική προθεσμία (επιβεβαίωσε, "
            "τρίμηνη κατά Ν.3198/1955 άρθρο 6 " + _VERIFY + ").",
        ],
        "steps": [
            "Καταγγελία στην Επιθεώρηση Εργασίας για ανείσπρακτες αποδοχές ή "
            "παραβάσεις.",
            "Γραπτή όχληση στον εργοδότη (εξώδικη δήλωση).",
            "Εργατικές διαφορές: ειδική διαδικασία στο αρμόδιο δικαστήριο.",
            "Κράτα: σύμβαση, αποδείξεις μισθοδοσίας, ένσημα ΕΦΚΑ, το έγγραφο της "
            "απόλυσης.",
        ],
        "escalation": [
            "Αμφισβήτηση της εγκυρότητας της απόλυσης: η προθεσμία είναι σύντομη "
            "και ανατρεπτική (δες prothesmies.py). Δικηγόρος γρήγορα.",
            "Διάκριση, εργατικό ατύχημα, ή ηθική παρενόχληση στον χώρο εργασίας.",
        ],
        "tools": ["vasi_agogis.py apozimiosi-apolysis",
                  "vasi_agogis.py dedoulevmenes-apodoches", "prothesmies.py",
                  "practice/ergatiko.md"],
    },
    {
        "slug": "katanalotis",
        "title": "Δικαιώματα καταναλωτή",
        "area": "Δίκαιο Καταναλωτή",
        "keywords": ["καταναλωτης", "ελαττωμα", "εγγυηση", "επιστροφη", "αγορα",
                     "παραγγελια", "υπαναχωρηση", "ελαττωματικο", "αντικατασταση",
                     "ηλεκτρονικη αγορα", "πωλητης", "καταστημα"],
        "summary": "Τα δικαιώματά σου ως αγοραστής, ιδίως σε ελαττωματικά προϊόντα "
                   "και σε αγορές από απόσταση.",
        "rights": [
            "Σε αγορές από απόσταση (ίντερνετ, τηλέφωνο) έχεις δικαίωμα "
            "υπαναχώρησης εντός δεκατεσσάρων ημερών χωρίς αιτιολογία (Ν.2251/1994 "
            + _VERIFY + ").",
            "Ο πωλητής ευθύνεται για πραγματικά ελαττώματα και για έλλειψη "
            "συνομολογημένων ιδιοτήτων (ΑΚ 534 κ.ε.): επισκευή, αντικατάσταση, "
            "μείωση τιμήματος ή υπαναχώρηση.",
            "Οι καταχρηστικοί γενικοί όροι σε καταναλωτικές συμβάσεις είναι άκυροι "
            "(Ν.2251/1994 άρθρο 2).",
        ],
        "steps": [
            "Πρώτα γραπτό παράπονο στον πωλητή, με σαφές αίτημα και προθεσμία.",
            "Συνήγορος του Καταναλωτή: δωρεάν εξωδικαστική επίλυση. Γραμμή 1520.",
            "Ενώσεις καταναλωτών για υποστήριξη.",
            "Κράτα: απόδειξη, την παραγγελία, την επικοινωνία, φωτογραφίες του "
            "ελαττώματος.",
        ],
        "escalation": [
            "Μεγάλα ποσά, ή άρνηση μετά την εξωδικαστική προσπάθεια.",
            "Σωματική βλάβη από ελαττωματικό προϊόν (ευθύνη παραγωγού).",
        ],
        "tools": ["symvasi_check.py scan", "practice/enochiko.md"],
    },
    {
        "slug": "prostima-trochaias",
        "title": "Κλήσεις και πρόστιμα τροχαίας",
        "area": "Διοικητικό",
        "keywords": ["κληση", "προστιμο", "τροχαια", "παραβαση", "κοκ", "παρκαρισμα",
                     "ταχυτητα", "δημος", "πινακιδες", "διπλωμα", "ενσταση"],
        "summary": "Πώς να χειριστείς μια κλήση ή ένα διοικητικό πρόστιμο, και "
                   "πότε να αντιρρήσεις.",
        "rights": [
            "Συνήθως ισχύει μειωμένο ποσό αν πληρώσεις εντός σύντομης προθεσμίας "
            "(επιβεβαίωσε την προθεσμία στην ίδια την κλήση " + _VERIFY + ").",
            "Έχεις δικαίωμα αντίρρησης ή προσφυγής κατά της πράξης, με προθεσμία.",
            "Η πράξη οφείλει να αναφέρει την παράβαση, τον χρόνο και την εκδούσα "
            "αρχή.",
        ],
        "steps": [
            "Αν η παράβαση ευσταθεί και θες την έκπτωση, πλήρωσε εντός της "
            "προθεσμίας του μειωμένου.",
            "Αν αμφισβητείς: ένσταση στην εκδούσα αρχή (Τροχαία ή Δήμος), και αν "
            "απορριφθεί, προσφυγή στο Διοικητικό Πρωτοδικείο.",
            "Κράτα: την κλήση, φωτογραφίες, στοιχεία μαρτύρων, ό,τι αποδεικνύει το "
            "αντίθετο.",
        ],
        "escalation": [
            "Αφαίρεση διπλώματος ή πινακίδων, ή σημεία ποινής.",
            "Μέθη ή σοβαρή παράβαση με ποινικές συνέπειες, ή κλήση σε δικαστήριο.",
        ],
        "tools": ["practice/dioikitiko-forologiko.md", "prothesmies.py"],
    },
    {
        "slug": "diadikasies-kep-aade",
        "title": "Διαδικασίες σε ΚΕΠ, ΑΑΔΕ και Δημόσιο",
        "area": "Διοικητικό και Φορολογικό",
        "keywords": ["κεπ", "ααδε", "govgr", "εφορια", "αφμ", "πιστοποιητικο",
                     "υπευθυνη δηλωση", "εξουσιοδοτηση", "φορολογικη δηλωση",
                     "εκκαθαριστικο", "αμκα", "taxisnet", "δημοσιο"],
        "summary": "Πώς να βγάλεις έγγραφα και να κάνεις συναλλαγές με το Δημόσιο, "
                   "ψηφιακά ή στο ΚΕΠ.",
        "rights": [
            "Τα περισσότερα πιστοποιητικά, η υπεύθυνη δήλωση και η εξουσιοδότηση "
            "εκδίδονται ψηφιακά μέσω gov.gr με κωδικούς TaxisNet.",
            "Το ΚΕΠ εξυπηρετεί δια ζώσης για τα ίδια και για όσα δεν είναι ακόμη "
            "ψηφιακά.",
            "Φορολογικά: myAADE και TaxisNet για δηλώσεις, βεβαιώσεις και "
            "ρυθμίσεις οφειλών.",
        ],
        "steps": [
            "Ψηφιακά: gov.gr για πιστοποιητικά, υπεύθυνη δήλωση και εξουσιοδότηση, "
            "myAADE για φορολογικά.",
            "Δια ζώσης: ΚΕΠ με ταυτότητα, ΑΦΜ και ΑΜΚΑ.",
            "Για φορολογική διαφορά, η αμφισβήτηση πράξης της ΑΑΔΕ γίνεται με "
            "ενδικοφανή προσφυγή στη ΔΕΔ.",
        ],
        "escalation": [
            "Φορολογική διαφορά: η ενδικοφανής προσφυγή στη ΔΕΔ έχει σύντομη "
            "προθεσμία (συνήθως τριάντα ημερών " + _VERIFY + ", δες prothesmies.py). "
            "Λογιστής ή δικηγόρος.",
            "Κατασχέσεις ή μεγάλα βεβαιωμένα πρόστιμα.",
        ],
        "tools": ["practice/dioikitiko-forologiko.md",
                  "prothesmies.py info endikofani-prosfygi-ded"],
    },
    {
        "slug": "oikogeneia-klironomia",
        "title": "Διαζύγιο και κληρονομιά: τα βασικά",
        "area": "Οικογενειακό και Κληρονομικό",
        "keywords": ["διαζυγιο", "χωρισμος", "διατροφη", "επιμελεια", "κληρονομια",
                     "διαθηκη", "αποποιηση", "αποδοχη κληρονομιας", "νομιμη μοιρα",
                     "κληρονομος", "χρεη κληρονομιας"],
        "summary": "Τα βασικά για διαζύγιο, διατροφή και κληρονομιά, και πού "
                   "χρειάζεται οπωσδήποτε δικηγόρος ή συμβολαιογράφος.",
        "rights": [
            "Διαζύγιο: συναινετικό με συμβολαιογραφική πράξη όταν συμφωνούν και οι "
            "δύο (Ν.4509/2017), ή με αγωγή όταν δεν συμφωνούν (ΑΚ 1439).",
            "Διατροφή ανήλικου τέκνου κατά τις ανάγκες του και τις οικονομικές "
            "δυνάμεις των γονέων (ΑΚ 1486 κ.ε.).",
            "Κληρονομιά: μπορείς να την αποδεχθείς ή να την αποποιηθείς. Η σιωπή "
            "σημαίνει αποδοχή, μαζί με τα χρέη. Η αποποίηση έχει αυστηρή προθεσμία "
            "(επιβεβαίωσε, συνήθως τεσσάρων μηνών, και ενός έτους αν ο "
            "κληρονομούμενος ή ο κληρονόμος ήταν στο εξωτερικό, ΑΚ 1847 "
            + _VERIFY + ").",
            "Νόμιμη μοίρα: οι στενοί συγγενείς δεν αποκληρώνονται ελεύθερα "
            "(ΑΚ 1825 κ.ε.).",
        ],
        "steps": [
            "Συναινετικό διαζύγιο: συμβολαιογράφος, με δικηγόρους των μερών.",
            "Αποδοχή ή αποποίηση: δήλωση στο Ειρηνοδικείο της κληρονομίας, μέσα "
            "στην προθεσμία.",
            "Κράτα: ληξιαρχικές πράξεις, τη διαθήκη αν υπάρχει, στοιχεία της "
            "περιουσίας και των τυχόν χρεών.",
        ],
        "escalation": [
            "Σχεδόν πάντα δικηγόρος ή συμβολαιογράφος. Ιδίως η προθεσμία "
            "αποποίησης: αν η κληρονομιά έχει χρέη, η απώλεια της προθεσμίας σε "
            "κάνει κληρονόμο των χρεών.",
            "Επιμέλεια και διατροφή σε σύγκρουση, ή αμφισβητούμενη διαθήκη.",
        ],
        "tools": ["vasi_agogis.py agogi-diatrofis-teknou",
                  "vasi_agogis.py nomimi-moira",
                  "practice/oikogeneiako-klironomiko.md"],
    },
    {
        "slug": "nomiki-voithia",
        "title": "Νομική βοήθεια (δωρεάν δικηγόρος)",
        "area": "Πρόσβαση στη δικαιοσύνη",
        "keywords": ["νομικη βοηθεια", "δωρεαν δικηγορος", "χαμηλο εισοδημα",
                     "ευεργετημα πενιας", "δικαστικα εξοδα", "αδυναμια πληρωμης",
                     "πενια"],
        "summary": "Δωρεάν νομική υποστήριξη και απαλλαγή από δικαστικά έξοδα για "
                   "χαμηλά εισοδήματα (Ν.3226/2004).",
        "rights": [
            "Δικαιούχοι είναι πολίτες χαμηλού εισοδήματος, καθώς και νομίμως "
            "διαμένοντες πολίτες της ΕΕ και τρίτων χωρών, όταν το ετήσιο "
            "οικογενειακό εισόδημα δεν υπερβαίνει τα δύο τρίτα του ελάχιστου "
            "ετήσιου ατομικού εισοδήματος (Ν.3226/2004, το ισχύον όριο " + _VERIFY + ").",
            "Καλύπτει δικηγόρο, συμβολαιογράφο και δικαστικό επιμελητή, και "
            "απαλλαγή από δικαστικά έξοδα.",
            "Υπάρχουν και αυτόματες κατηγορίες δικαιούχων, για παράδειγμα θύματα "
            "ορισμένων εγκλημάτων (" + _VERIFY + ").",
        ],
        "steps": [
            "Αίτηση στο δικαστήριο όπου εκκρεμεί ή θα εισαχθεί η υπόθεση.",
            "Δικαιολογητικά εισοδήματος: εκκαθαριστικό, δήλωση Ε1, σχετικές "
            "βεβαιώσεις.",
            "Για προέλεγχο επιλεξιμότητας τρέξε: odigoi.py voithia --eisodima ΠΟΣΟ",
        ],
        "escalation": [
            "Αυτός ο οδηγός είναι η ίδια η βοήθεια. Η αίτηση υποβάλλεται ανά "
            "υπόθεση, μαζί με την κύρια διαδικασία.",
        ],
        "tools": ["odigoi.py voithia"],
    },
]

_BY_SLUG = {g["slug"]: g for g in GUIDES}


def get(slug):
    return _BY_SLUG.get(slug)


def find(term):
    """Return guides whose title, keywords or summary match the term (accent fold)."""
    q = normalize(term)
    hits = []
    for g in GUIDES:
        haystack = " ".join([g["title"], g["summary"], " ".join(g["keywords"])])
        if q in normalize(haystack):
            hits.append(g)
    return hits


def legal_aid_check(eisodima, orio_anaforas=None):
    """Pre-check legal-aid eligibility under Ν.3226/2004.

    The test: annual family income at or below two thirds of the minimum annual
    individual income. The reference figure (orio_anaforas) moves with the minimum
    wage, so it is never hardcoded; the caller supplies the current figure and this
    function does the arithmetic. Without it, the test is explained but not decided.
    """
    if orio_anaforas is None:
        return {"eisodima": eisodima, "orio_anaforas": None,
                "katofli": None, "dikaioucos": None}
    katofli = orio_anaforas * 2 / 3
    return {"eisodima": eisodima, "orio_anaforas": orio_anaforas,
            "katofli": katofli, "dikaioucos": eisodima <= katofli}


def render_guide(g):
    lines = [
        f"{g['title']}  ({g['area']})",
        f"slug: {g['slug']}",
        "",
        g["summary"],
        "",
        "Τα δικαιώματά σου:",
    ]
    for r in g["rights"]:
        lines.append(f"  - {r}")
    lines += ["", "Τι να κάνεις:"]
    for s in g["steps"]:
        lines.append(f"  - {s}")
    lines += ["", "Πότε χρειάζεσαι δικηγόρο:"]
    for e in g["escalation"]:
        lines.append(f"  - {e}")
    if g.get("tools"):
        lines += ["", f"Σχετικά εργαλεία: {', '.join(g['tools'])}"]
    lines += [
        "",
        "Οδηγός ενημέρωσης, όχι νομική συμβουλή. Όπου υπάρχει προθεσμία, "
        "δικαστήριο ή σημαντικό χρηματικό ποσό, δες δικηγόρο.",
    ]
    return "\n".join(lines)


def render_list(guides):
    lines = ["Οδηγοί πολίτη (καθημερινά νομικά ζητήματα):", ""]
    width = max(len(g["slug"]) for g in guides)
    for g in guides:
        lines.append(f"  {g['slug']:<{width}}  {g['title']}")
    lines += ["", "Δες έναν: odigoi.py show misthosi-engyisi",
              "Ψάξε με λέξεις: odigoi.py find \"εγγύηση\""]
    return "\n".join(lines)


def render_find(term, hits):
    if not hits:
        return (f"Κανένας οδηγός δεν ταιριάζει με '{term}'. "
                f"Δες όλους: odigoi.py list")
    lines = [f"Οδηγοί που ταιριάζουν με '{term}':", ""]
    width = max(len(g["slug"]) for g in hits)
    for g in hits:
        lines.append(f"  {g['slug']:<{width}}  {g['title']}")
    lines += ["", "Άνοιξε έναν: odigoi.py show " + hits[0]["slug"]]
    return "\n".join(lines)


def render_voithia(r):
    lines = [
        "Προέλεγχος νομικής βοήθειας (Ν.3226/2004)",
        "",
        "Κριτήριο: το ετήσιο οικογενειακό εισόδημα να μην υπερβαίνει τα δύο τρίτα "
        "του ελάχιστου ετήσιου ατομικού εισοδήματος.",
        "",
        f"Ετήσιο οικογενειακό εισόδημα: {r['eisodima']:.2f} ευρώ",
    ]
    if r["orio_anaforas"] is None:
        lines += [
            "",
            "Δεν δόθηκε το εισόδημα αναφοράς, οπότε ο έλεγχος δεν αποφαίνεται.",
            "Δώσε το ισχύον ελάχιστο ετήσιο ατομικό εισόδημα για υπολογισμό:",
            "    odigoi.py voithia --eisodima ΠΟΣΟ --orio-anaforas ΠΟΣΟ_ΑΝΑΦΟΡΑΣ",
        ]
    else:
        verdict = "ΠΙΘΑΝΩΣ ΔΙΚΑΙΟΥΧΟΣ" if r["dikaioucos"] else "ΠΙΘΑΝΩΣ ΜΗ ΔΙΚΑΙΟΥΧΟΣ"
        lines += [
            f"Εισόδημα αναφοράς (ελάχιστο ετήσιο ατομικό): {r['orio_anaforas']:.2f} ευρώ",
            f"Όριο (δύο τρίτα του εισοδήματος αναφοράς): {r['katofli']:.2f} ευρώ",
            "",
            f"Αποτέλεσμα: {verdict}",
        ]
    lines += [
        "",
        f"Προσοχή {_VERIFY}: το εισόδημα αναφοράς και οι αυτόματες κατηγορίες "
        "δικαιούχων αλλάζουν με τον νόμο. Επιβεβαίωσε το ισχύον όριο και τις "
        "κατηγορίες στο κείμενο του Ν.3226/2004 πριν στηριχθείς στο αποτέλεσμα. "
        "Η οριστική κρίση γίνεται από το δικαστήριο επί της αίτησης.",
    ]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Οδηγοί πολίτη: καθημερινά νομικά ζητήματα στο ελληνικό δίκαιο")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="κατάλογος οδηγών")
    pl.add_argument("--json", action="store_true")

    ps = sub.add_parser("show", help="άνοιξε έναν οδηγό")
    ps.add_argument("slug")
    ps.add_argument("--json", action="store_true")

    pf = sub.add_parser("find", help="βρες οδηγό με λέξεις της καθημερινής γλώσσας")
    pf.add_argument("term")
    pf.add_argument("--json", action="store_true")

    pv = sub.add_parser("voithia", help="προέλεγχος νομικής βοήθειας (Ν.3226/2004)")
    pv.add_argument("--eisodima", required=True, type=float,
                    help="ετήσιο οικογενειακό εισόδημα σε ευρώ")
    pv.add_argument("--orio-anaforas", type=float, default=None,
                    help="ισχύον ελάχιστο ετήσιο ατομικό εισόδημα (εισόδημα αναφοράς)")
    pv.add_argument("--json", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(GUIDES, ensure_ascii=False, indent=2) if args.json
              else render_list(GUIDES))
        return 0

    if args.cmd == "show":
        g = get(args.slug)
        if g is None:
            sys.stderr.write(
                f"Άγνωστος οδηγός: '{args.slug}'. "
                f"Δες τη λίστα: odigoi.py list\n")
            return 2
        print(json.dumps(g, ensure_ascii=False, indent=2) if args.json
              else render_guide(g))
        return 0

    if args.cmd == "find":
        hits = find(args.term)
        if args.json:
            print(json.dumps([g["slug"] for g in hits], ensure_ascii=False, indent=2))
        else:
            print(render_find(args.term, hits))
        return 0

    if args.cmd == "voithia":
        if args.eisodima < 0 or (args.orio_anaforas is not None
                                 and args.orio_anaforas <= 0):
            p.error("τα ποσά πρέπει να είναι θετικά")
        r = legal_aid_check(args.eisodima, args.orio_anaforas)
        print(json.dumps(r, ensure_ascii=False, indent=2) if args.json
              else render_voithia(r))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
