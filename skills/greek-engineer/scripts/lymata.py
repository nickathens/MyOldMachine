#!/usr/bin/env python3
"""Επεξεργασία αστικών λυμάτων: διαστασιολόγηση ενεργού ιλύος και γεωμετρία τάφρου.

Το πακέτο του μηχανικού περιβάλλοντος (υγειονομική και περιβαλλοντική μηχανική).
v1: σχεδιασμός βιολογικής βαθμίδας ενεργού ιλύος με παρατεταμένο αερισμό, η
τεχνολογία της οξειδωτικής τάφρου, που είναι η επικρατέστερη για μικρούς και
μεσαίους ελληνικούς οικισμούς.

Τρία μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΜΗΧΑΝΗ (diastasiologisi). Το ισοζύγιο μάζας ενεργού ιλύος
   κατά τη μέθοδο DWA-A 131 / Metcalf & Eddy: παραγωγή βιομάζας από απόδοση
   και ηλικία ιλύος, όγκος αντιδραστήρα από τη μάζα MLVSS, υδραυλικός χρόνος
   παραμονής, λόγος τροφής προς μικροοργανισμούς F/M, ογκομετρική φόρτιση,
   απαίτηση οξυγόνου (άνθρακας και προαιρετική νιτροποίηση). Η μηχανή
   αυτοελέγχεται: η ταυτότητα F/M επί συγκέντρωση MLVSS πρέπει να ξαναδίνει
   την ογκομετρική φόρτιση, αλλιώς σφάλμα.

2. ΓΕΩΜΕΤΡΙΑ ΟΞΕΙΔΩΤΙΚΗΣ ΤΑΦΡΟΥ (tafros). Από τον όγκο διεργασίας και επιλεγμένο
   βάθος και πλάτος καναλιού, το μήκος του βρόχου, η διατομή, και η παροχή
   κυκλοφορίας που απαιτείται για να διατηρηθεί η ταχύτητα αιώρησης 0,3 m/s
   σε όλη τη διατομή. Η ταχύτητα την ορίζουν οι αεριστήρες και οι αναδευτήρες,
   όχι η παροχή διεργασίας: το εργαλείο δίνει τη γεωμετρία και τη ζητούμενη
   κυκλοφορία, όχι τον αεριστήρα.

3. ΤΥΠΙΚΕΣ ΤΙΜΕΣ ΩΣ ΔΕΔΟΜΕΝΑ (ides). Οι ενσωματωμένες παράμετροι σχεδιασμού
   φέρουν [ΕΠΑΛΗΘΕΥΣΕ]: είναι τυπικά εύρη βιβλιογραφίας, όχι τιμές έργου.
   Δέχονται ρητές τιμές που υπερισχύουν. Δες reference/perivallontikos.md.

Ο διαχωρισμός ευθύνης: το script υπολογίζει το ισοζύγιο. Ο μελετητής επιλέγει
παραδοχές, αποδέκτη, όρια εκροής και εξοπλισμό, και υπογράφει τη μελέτη. Η
διαστασιολόγηση εδώ είναι προδιαστασιολόγηση, όχι οριστική μελέτη.

Usage:
  python lymata.py diastasiologisi --ik 2000
  python lymata.py diastasiologisi --ik 5000 --srt 25 --mlss 4000 --tkn 55
  python lymata.py diastasiologisi --fortio 120 --paroxi 1000 --so 300
  python lymata.py tafros --v 1200 --vathos 3.5 --platos 4.0
  python lymata.py tafros --v 800 --vathos 1.2 --platos 6.0 --kanalia 1
  python lymata.py ides
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import math
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

# Τυπικά δεδομένα σχεδιασμού αστικών λυμάτων. Εύρη βιβλιογραφίας (Metcalf &
# Eddy, DWA-A 131), όχι τιμές έργου: κάθε ένα φέρει [ΕΠΑΛΗΘΕΥΣΕ]. Δες
# reference/perivallontikos.md για πηγές και εύρη.
RYPANSI_ANA_IK = 60.0     # g BOD5 ανά ισοδύναμο κάτοικο ανά ημέρα [ΕΠΑΛΗΘΕΥΣΕ]
PAROXI_ANA_IK = 200.0     # L ανά ισοδύναμο κάτοικο ανά ημέρα [ΕΠΑΛΗΘΕΥΣΕ]
EKROI_BOD5 = 25.0         # mg/L, όριο δευτεροβάθμιας κατά 91/271 Παράρτ. Ι [ΕΠΑΛΗΘΕΥΣΕ]
Y_APODOSI = 0.6           # kg VSS/kg BOD5, τυπική οικιακή [ΕΠΑΛΗΘΕΥΣΕ]
KD_APOSYNTHESI = 0.06     # 1/d, ενδογενής αποσύνθεση στους 20 C [ΕΠΑΛΗΘΕΥΣΕ]
FD_YPOLEIMMA = 0.15       # αδρανές κλάσμα κυτταρικού υπολείμματος [ΕΠΑΛΗΘΕΥΣΕ]
VSS_MLSS = 0.75           # λόγος MLVSS προς MLSS [ΕΠΑΛΗΘΕΥΣΕ]
MLSS_TYPIKO = 4000.0      # mg/L, τυπική οξειδωτικής τάφρου [ΕΠΑΛΗΘΕΥΣΕ]
SRT_TYPIKO = 25.0         # d, παρατεταμένος αερισμός με νιτροποίηση [ΕΠΑΛΗΘΕΥΣΕ]
F_BOD5_BODU = 0.68        # BOD5 προς τελικό BODu, για το ισοζύγιο οξυγόνου [ΕΠΑΛΗΘΕΥΣΕ]
O2_ANA_N = 4.57           # kg O2 ανά kg N που νιτροποιείται [ΕΠΑΛΗΘΕΥΣΕ]
KLASMA_NITROPOIISIS = 0.8  # κλάσμα TKN που νιτροποιείται (το υπόλοιπο αφομοιώνεται) [ΕΠΑΛΗΘΕΥΣΕ]
TAXYTITA_AIORISIS = 0.30  # m/s, ελάχιστη κυκλοφορία για αιώρηση στερεών [ΕΠΑΛΗΘΕΥΣΕ]
PERITHORIO = 0.5          # m, ελεύθερο ύψος τάφρου [ΕΠΑΛΗΘΕΥΣΕ]

# Τυπικά εύρη ελέγχου, για την ένδειξη εντός/εκτός στο αποτέλεσμα [ΕΠΑΛΗΘΕΥΣΕ].
EYRI = {
    "F_M": (0.05, 0.15),      # kg BOD/kg MLSS/d, παρατεταμένος αερισμός
    "HRT_h": (18.0, 36.0),    # ώρες
    "SRT_d": (15.0, 30.0),    # ημέρες
    "MLSS": (3000.0, 6000.0), # mg/L
    "BV": (0.15, 0.40),       # kg BOD/m3/d
}


def _entos(timi, eyros):
    """Ένδειξη αν μια τιμή πέφτει εντός του τυπικού εύρους (χαμηλό, υψηλό)."""
    lo, hi = eyros
    if not math.isfinite(timi):
        return "μη έγκυρη τιμή (NaN ή άπειρο)"
    if timi < lo:
        return "κάτω από το τυπικό εύρος"
    if timi > hi:
        return "πάνω από το τυπικό εύρος"
    return "εντός τυπικού εύρους"


def diastasiologisi(ik=None, fortio=None, paroxi=None, so=None, se=EKROI_BOD5,
                    srt=SRT_TYPIKO, mlss=MLSS_TYPIKO, vss_ratio=VSS_MLSS,
                    y=Y_APODOSI, kd=KD_APOSYNTHESI, fd=FD_YPOLEIMMA, tkn=None):
    """Διαστασιολόγηση βιολογικής βαθμίδας ενεργού ιλύος (ισοζύγιο μάζας).

    Εναλλακτικές είσοδοι φορτίου: ή ισοδύναμοι κάτοικοι (ik), ή απευθείας
    οργανικό φορτίο (fortio, kg BOD5/d) και παροχή (paroxi, m3/d). Η
    συγκέντρωση εισόδου so (mg/L) υπολογίζεται από φορτίο και παροχή αν δεν
    δοθεί. tkn (mg/L) προαιρετικό: ενεργοποιεί την απαίτηση οξυγόνου
    νιτροποίησης. Επιστρέφει όγκο, χρόνο παραμονής, F/M, ογκομετρική φόρτιση,
    παραγωγή ιλύος και απαίτηση οξυγόνου.
    """
    # Φορτίο και παροχή: ή από ισοδύναμους κατοίκους, ή ρητά.
    if ik is not None:
        if ik <= 0:
            raise ValueError("Οι ισοδύναμοι κάτοικοι πρέπει να είναι θετικοί.")
        # Οι ι.κ. ορίζουν και το φορτίο και την παροχή, άρα και τη συγκέντρωση
        # εισόδου: ρητό fortio, paroxi ή so μαζί με ik θα έδινε ασυνεπή νούμερα.
        if fortio is not None or paroxi is not None or so is not None:
            raise ValueError("Με --ik το φορτίο, η παροχή και η συγκέντρωση εισόδου "
                             "προκύπτουν από τους κατοίκους. Για δικές σου τιμές δώσε "
                             "--fortio και --paroxi (και προαιρετικά --so).")
        fortio = ik * RYPANSI_ANA_IK / 1000.0        # kg BOD5/d
        paroxi = ik * PAROXI_ANA_IK / 1000.0         # m3/d
    else:
        if fortio is None or paroxi is None:
            raise ValueError("Δώσε --ik, ή και τα δύο --fortio (kg/d) και --paroxi (m3/d).")
        if fortio <= 0 or paroxi <= 0:
            raise ValueError("Φορτίο και παροχή πρέπει να είναι θετικά.")

    for onoma, timi in (("fortio", fortio), ("paroxi", paroxi), ("so", so), ("se", se),
                        ("srt", srt), ("mlss", mlss), ("vss_ratio", vss_ratio),
                        ("y", y), ("kd", kd), ("fd", fd), ("tkn", tkn)):
        if timi is not None and not math.isfinite(timi):
            raise ValueError(f"Η τιμή {onoma} δεν είναι πεπερασμένος αριθμός.")
    if so is None:
        so = fortio * 1000.0 / paroxi                # mg/L = g/m3
    else:
        # Φορτίο, παροχή και συγκέντρωση δεσμεύονται από το fortio = Q*So/1000.
        # Και τα τρία ρητά και ασύμφωνα δίνουν αποτέλεσμα από το ένα και
        # έλεγχο από το άλλο (audit F36, 2026-09-06): 60 kg/d με 100 m3/d
        # και 1200 mg/L σημαίνει 120 kg/d. Ανοχή 2% για στρογγυλοποίηση.
        synepagomeno = paroxi * so / 1000.0
        if abs(synepagomeno - fortio) > 0.02 * max(fortio, synepagomeno):
            raise ValueError(
                f"Ασύμφωνες είσοδοι: παροχή {paroxi:g} m3/d επί So {so:g} mg/L δίνουν "
                f"{synepagomeno:.1f} kg BOD5/d, όχι {fortio:g}. Δώσε δύο από τα τρία, "
                "ή διόρθωσε το τρίτο.")
    if so <= 0 or se < 0 or se >= so:
        raise ValueError("Η εκροή πρέπει να είναι θετική και μικρότερη της εισόδου.")
    if srt <= 0 or mlss <= 0:
        raise ValueError("Ηλικία ιλύος και MLSS πρέπει να είναι θετικά.")
    if not (0 < vss_ratio <= 1):
        raise ValueError("Ο λόγος MLVSS/MLSS πρέπει να είναι στο (0, 1].")
    if y <= 0 or kd < 0 or not (0 <= fd <= 1):
        raise ValueError("Απόδοση θετική, αποσύνθεση μη αρνητική, fd στο [0, 1].")

    dbod = so - se                                   # mg/L απομακρυνόμενο BOD5
    mlvss = mlss * vss_ratio                         # mg/L

    # Παραγωγή βιομάζας κατά Metcalf & Eddy (σύνθεση + κυτταρικό υπόλειμμα).
    yobs = y * (1.0 + fd * kd * srt) / (1.0 + kd * srt)
    px_vss = yobs * paroxi * dbod / 1000.0           # kg VSS/d

    # Μάζα MLVSS στον αντιδραστήρα και όγκος από τη συγκέντρωση.
    maza_mlvss = px_vss * srt                        # kg VSS
    v = maza_mlvss * 1000.0 / mlvss                  # m3

    hrt_h = v / paroxi * 24.0
    fm = fortio / maza_mlvss                         # kg BOD/kg VSS/d
    bv = fortio / v                                  # kg BOD/m3/d

    # Απαίτηση οξυγόνου: άνθρακας (τελικό BOD μείον βιομάζα) και νιτροποίηση.
    o2_anthraka = paroxi * dbod / 1000.0 / F_BOD5_BODU - 1.42 * px_vss
    o2_nitrop = 0.0
    nox = 0.0
    if tkn is not None:
        if tkn < 0:
            raise ValueError("Το TKN δίνεται μη αρνητικό.")
        nox = KLASMA_NITROPOIISIS * paroxi * tkn / 1000.0   # kg N/d
        o2_nitrop = O2_ANA_N * nox
    o2_synolo = o2_anthraka + o2_nitrop

    # Αυτοέλεγχος συνέπειας: F/M επί τη συγκέντρωση MLVSS (kg/m3) πρέπει να
    # ξαναδίνει την ογκομετρική φόρτιση. Συνδέει το F/M (από τη μάζα) με τη BV
    # (από τον όγκο) μέσω της συγκέντρωσης: πιάνει αριθμητική ή μοναδιαία ολίσθηση.
    elegxos = fm * (mlvss / 1000.0)
    if abs(elegxos - bv) > 1e-9 * bv:
        raise RuntimeError("Ασυνέπεια F/M και ογκομετρικής φόρτισης: εσωτερικό σφάλμα.")
    if yobs > y:
        raise RuntimeError("Η φαινόμενη απόδοση ξεπέρασε την πραγματική: εσωτερικό σφάλμα.")

    return {
        "eisodos": {"IK": ik, "fortio_kgBOD_d": round(fortio, 2),
                    "paroxi_m3_d": round(paroxi, 2), "So_mg_L": round(so, 1),
                    "Se_mg_L": se, "SRT_d": srt, "MLSS_mg_L": mlss,
                    "MLVSS_MLSS": vss_ratio, "Y": y, "kd": kd, "fd": fd,
                    "TKN_mg_L": tkn},
        "ogkos_m3": round(v, 1),
        "ydravlikos_xronos_h": round(hrt_h, 1),
        "F_M_kgBOD_kgMLSS_d": round(fm, 3),
        "ogkometriki_fortisi_kgBOD_m3_d": round(bv, 3),
        "fainomeni_apodosi_Yobs": round(yobs, 3),
        "paragogi_ilyos_kgVSS_d": round(px_vss, 1),
        "apaitisi_o2_kgO2_d": round(o2_synolo, 1),
        "apaitisi_o2_anthraka_kgO2_d": round(o2_anthraka, 1),
        "apaitisi_o2_nitropoiisis_kgO2_d": round(o2_nitrop, 1),
        "nitropoioumeno_N_kgN_d": round(nox, 2),
        "elegxoi": {
            "F_M": _entos(fm, EYRI["F_M"]),
            "ydravlikos_xronos": _entos(hrt_h, EYRI["HRT_h"]),
            "SRT": _entos(srt, EYRI["SRT_d"]),
            "MLSS": _entos(mlss, EYRI["MLSS"]),
            "ogkometriki_fortisi": _entos(bv, EYRI["BV"]),
        },
        "simeiosi": (
            f"Προδιαστασιολόγηση παρατεταμένου αερισμού. Παράμετροι σχεδιασμού "
            f"{_VERIFY} στη βάση σχεδιασμού του έργου (Metcalf & Eddy, DWA-A 131). "
            f"Όριο εκροής {se} mg/L BOD5 κατά 91/271 Παράρτημα Ι· ο αποδέκτης "
            f"μπορεί να επιβάλλει αυστηρότερα. Η καθίζηση, ο αεριστικός εξοπλισμός "
            f"και η γραμμή ιλύος διαστασιολογούνται χωριστά."
        ),
    }


def tafros(v_m3, vathos, platos, kanalia=1, taxytita=TAXYTITA_AIORISIS,
           perithorio=PERITHORIO):
    """Γεωμετρία οξειδωτικής τάφρου από τον όγκο διεργασίας.

    v_m3: όγκος βιολογικής βαθμίδας. vathos: βάθος υγρού ανά κανάλι (m).
    platos: πλάτος καναλιού (m). kanalia: πλήθος παράλληλων καναλιών. taxytita:
    ταχύτητα κυκλοφορίας στόχος. Επιστρέφει διατομή, μήκος βρόχου και την
    παροχή κυκλοφορίας που χρειάζεται για τη διατήρηση της ταχύτητας.
    """
    if v_m3 <= 0:
        raise ValueError("Ο όγκος πρέπει να είναι θετικός.")
    if vathos <= 0 or platos <= 0:
        raise ValueError("Βάθος και πλάτος πρέπει να είναι θετικά.")
    if kanalia < 1:
        raise ValueError("Το πλήθος καναλιών πρέπει να είναι τουλάχιστον 1.")
    if taxytita <= 0 or perithorio < 0:
        raise ValueError("Ταχύτητα θετική, περιθώριο μη αρνητικό.")

    diatomi = kanalia * platos * vathos              # m2, συνολική ενεργός διατομή
    mikos = v_m3 / diatomi                           # m, μήκος βρόχου (κεντρική γραμμή)
    q_kykl = taxytita * diatomi                      # m3/s, παροχή κυκλοφορίας για την ταχύτητα

    # Αυτοέλεγχος: διατομή επί μήκος πρέπει να ξαναδίνει τον όγκο.
    if abs(diatomi * mikos - v_m3) > 1e-9 * v_m3:
        raise RuntimeError("Ασυνέπεια όγκου και γεωμετρίας: εσωτερικό σφάλμα.")

    return {
        "eisodos": {"ogkos_m3": v_m3, "vathos_ygrou_m": vathos, "platos_m": platos,
                    "kanalia": kanalia, "taxytita_stoxos_m_s": taxytita,
                    "perithorio_m": perithorio},
        "diatomi_m2": round(diatomi, 3),
        "mikos_vroxou_m": round(mikos, 1),
        "synoliko_vathos_m": round(vathos + perithorio, 2),
        "paroxi_kykloforias_m3_s": round(q_kykl, 3),
        "paroxi_kykloforias_m3_h": round(q_kykl * 3600.0, 1),
        "simeiosi": (
            f"Η ταχύτητα κυκλοφορίας {taxytita} m/s {_VERIFY} την ορίζουν οι "
            f"αεριστήρες και οι αναδευτήρες, όχι η παροχή διεργασίας: η "
            f"παροχή κυκλοφορίας εδώ είναι η ζητούμενη ώθηση. Στις στροφές 180 "
            f"μοιρών χρειάζονται μεσαίο διαχωριστικό τοιχίο και οδηγά πτερύγια "
            f"ώστε να μη χαμηλώνει η ταχύτητα στην εσωτερική ακτίνα. Πολύ μεγάλο "
            f"μήκος βρόχου σημαίνει αύξησε βάθος, πλάτος ή κανάλια."
        ),
    }


def _print_diast(r):
    e = r["eisodos"]
    kef = f"{e['IK']} ι.κ." if e["IK"] is not None else \
        f"{e['fortio_kgBOD_d']} kg BOD/d, {e['paroxi_m3_d']} m3/d"
    print(f"Διαστασιολόγηση ενεργού ιλύος ({kef})")
    print(f"  Είσοδος So = {e['So_mg_L']} mg/L, εκροή Se = {e['Se_mg_L']} mg/L, "
          f"SRT = {e['SRT_d']} d, MLSS = {e['MLSS_mg_L']} mg/L")
    print(f"  Όγκος αντιδραστήρα: {r['ogkos_m3']} m3")
    print(f"  Υδραυλικός χρόνος παραμονής: {r['ydravlikos_xronos_h']} h "
          f"({r['elegxoi']['ydravlikos_xronos']})")
    print(f"  F/M: {r['F_M_kgBOD_kgMLSS_d']} kg BOD/kg MLSS/d ({r['elegxoi']['F_M']})")
    print(f"  Ογκομετρική φόρτιση: {r['ogkometriki_fortisi_kgBOD_m3_d']} kg BOD/m3/d "
          f"({r['elegxoi']['ogkometriki_fortisi']})")
    print(f"  Παραγωγή ιλύος: {r['paragogi_ilyos_kgVSS_d']} kg VSS/d")
    print(f"  Απαίτηση οξυγόνου: {r['apaitisi_o2_kgO2_d']} kg O2/d "
          f"(άνθρακας {r['apaitisi_o2_anthraka_kgO2_d']}, "
          f"νιτροποίηση {r['apaitisi_o2_nitropoiisis_kgO2_d']})")
    print(f"  {r['simeiosi']}")


def _print_tafros(r):
    e = r["eisodos"]
    print(f"Οξειδωτική τάφρος για όγκο {e['ogkos_m3']} m3 "
          f"({e['kanalia']} κανάλι/α, βάθος {e['vathos_ygrou_m']} m, πλάτος {e['platos_m']} m)")
    print(f"  Ενεργός διατομή: {r['diatomi_m2']} m2")
    print(f"  Μήκος βρόχου: {r['mikos_vroxou_m']} m")
    print(f"  Συνολικό βάθος με περιθώριο: {r['synoliko_vathos_m']} m")
    print(f"  Παροχή κυκλοφορίας για {e['taxytita_stoxos_m_s']} m/s: "
          f"{r['paroxi_kykloforias_m3_s']} m3/s = {r['paroxi_kykloforias_m3_h']} m3/h")
    print(f"  {r['simeiosi']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diastasiologisi", help="Διαστασιολόγηση ενεργού ιλύος")
    d.add_argument("--ik", type=float, help="Ισοδύναμοι κάτοικοι")
    d.add_argument("--fortio", type=float, help="Οργανικό φορτίο σε kg BOD5/d")
    d.add_argument("--paroxi", type=float, help="Παροχή σε m3/d")
    d.add_argument("--so", type=float, help="Συγκέντρωση εισόδου BOD5 σε mg/L")
    d.add_argument("--se", type=float, default=EKROI_BOD5, help="Όριο εκροής BOD5 σε mg/L")
    d.add_argument("--srt", type=float, default=SRT_TYPIKO, help="Ηλικία ιλύος σε ημέρες")
    d.add_argument("--mlss", type=float, default=MLSS_TYPIKO, help="MLSS σε mg/L")
    d.add_argument("--r", type=float, default=VSS_MLSS, dest="vss_ratio",
                   help="Λόγος MLVSS προς MLSS")
    d.add_argument("--y", type=float, default=Y_APODOSI, help="Απόδοση kg VSS/kg BOD5")
    d.add_argument("--kd", type=float, default=KD_APOSYNTHESI, help="Ενδογενής αποσύνθεση 1/d")
    d.add_argument("--fd", type=float, default=FD_YPOLEIMMA, help="Κλάσμα κυτταρικού υπολείμματος")
    d.add_argument("--tkn", type=float, help="TKN εισόδου σε mg/L (ενεργοποιεί νιτροποίηση)")
    d.add_argument("--json", action="store_true")

    t = sub.add_parser("tafros", help="Γεωμετρία οξειδωτικής τάφρου")
    t.add_argument("--v", type=float, required=True, help="Όγκος διεργασίας σε m3")
    t.add_argument("--vathos", type=float, required=True, help="Βάθος υγρού ανά κανάλι σε m")
    t.add_argument("--platos", type=float, required=True, help="Πλάτος καναλιού σε m")
    t.add_argument("--kanalia", type=int, default=1, help="Πλήθος παράλληλων καναλιών")
    t.add_argument("--taxytita", type=float, default=TAXYTITA_AIORISIS,
                   help="Ταχύτητα κυκλοφορίας στόχος σε m/s")
    t.add_argument("--perithorio", type=float, default=PERITHORIO, help="Ελεύθερο ύψος σε m")
    t.add_argument("--json", action="store_true")

    i = sub.add_parser("ides", help="Τυπικές τιμές σχεδιασμού")
    i.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "diastasiologisi":
        r = diastasiologisi(args.ik, args.fortio, args.paroxi, args.so, args.se,
                            args.srt, args.mlss, args.vss_ratio, args.y, args.kd,
                            args.fd, args.tkn)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            _print_diast(r)
    elif args.cmd == "tafros":
        r = tafros(args.v, args.vathos, args.platos, args.kanalia, args.taxytita,
                   args.perithorio)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            _print_tafros(r)
    elif args.cmd == "ides":
        payload = {
            "rypansi_ana_ik_gBOD": RYPANSI_ANA_IK,
            "paroxi_ana_ik_L": PAROXI_ANA_IK,
            "ekroi_BOD5_mg_L": EKROI_BOD5,
            "Y_kgVSS_kgBOD5": Y_APODOSI,
            "kd_1_d": KD_APOSYNTHESI,
            "fd": FD_YPOLEIMMA,
            "MLVSS_MLSS": VSS_MLSS,
            "MLSS_typiko_mg_L": MLSS_TYPIKO,
            "SRT_typiko_d": SRT_TYPIKO,
            "taxytita_aiorisis_m_s": TAXYTITA_AIORISIS,
            "perithorio_m": PERITHORIO,
            "eyri_elegxou": EYRI,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Τυπικές τιμές σχεδιασμού αστικών λυμάτων {_VERIFY}")
            print(f"  Ρύπανση: {RYPANSI_ANA_IK} g BOD5 ανά ι.κ. ανά ημέρα")
            print(f"  Παροχή: {PAROXI_ANA_IK} L ανά ι.κ. ανά ημέρα")
            print(f"  Όριο εκροής: {EKROI_BOD5} mg/L BOD5 (91/271 Παράρτημα Ι)")
            print(f"  Απόδοση Y: {Y_APODOSI} kg VSS/kg BOD5, αποσύνθεση kd: {KD_APOSYNTHESI} 1/d")
            print(f"  MLSS: {MLSS_TYPIKO} mg/L (εύρος {EYRI['MLSS'][0]:.0f} έως {EYRI['MLSS'][1]:.0f})")
            print(f"  SRT: {SRT_TYPIKO} d (εύρος {EYRI['SRT_d'][0]:.0f} έως {EYRI['SRT_d'][1]:.0f})")
            print(f"  F/M: {EYRI['F_M'][0]} έως {EYRI['F_M'][1]} kg BOD/kg MLSS/d")
            print(f"  Υδραυλικός χρόνος: {EYRI['HRT_h'][0]:.0f} έως {EYRI['HRT_h'][1]:.0f} h")
            print(f"  Ταχύτητα αιώρησης: {TAXYTITA_AIORISIS} m/s, περιθώριο {PERITHORIO} m")
            print("  Πηγές και εύρη: reference/perivallontikos.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
