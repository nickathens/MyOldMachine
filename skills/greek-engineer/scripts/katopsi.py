#!/usr/bin/env python3
"""Παραγωγή σχεδίων DXF: διάγραμμα κάλυψης και απλή κάτοψη χώρου.

Το AutoCAD δεν τρέχει σε Linux, το format του όμως γράφεται και διαβάζεται
προγραμματιστικά. Αυτό το εργαλείο παράγει καθαρά DXF (R2018) που ανοίγουν σε
AutoCAD, QCAD, LibreCAD, BricsCAD: στρώσεις με ελληνικά ονόματα, εμβαδά με τον
τύπο του κανόνα των παπουτσιών (shoelace), ετικέτες, και έλεγχο εγκυρότητας
με επανάγνωση και audit πριν αναφερθεί επιτυχία.

Δύο μέρη, με σαφή διαχωρισμό ευθύνης:

1. ΝΤΕΤΕΡΜΙΝΙΣΤΙΚΗ ΓΕΩΜΕΤΡΙΑ (εμβαδά, έλεγχοι). Καθαρά μαθηματικά χωρίς
   εξαρτήσεις, ώστε να ελέγχονται παντού.

2. ΓΡΑΦΗ DXF (diagramma, plano). Απαιτεί το πακέτο ezdxf (pip install ezdxf).
   Χωρίς αυτό, το εργαλείο αρνείται ευγενικά με οδηγία εγκατάστασης: ποτέ
   μισοτελειωμένο αρχείο.

Το παραγόμενο σχέδιο είναι ΥΠΟΒΑΘΡΟ ΕΡΓΑΣΙΑΣ, όχι συμβατικό σχέδιο άδειας:
το επίσημο διάγραμμα κάλυψης συντάσσεται και υπογράφεται από τον μελετητή
πάνω σε εξαρτημένο τοπογραφικό.

Usage:
  python katopsi.py diagramma --oikopedo "0,0 25,0 25,20 0,20" --ktirio "5,5 17,5 17,15 5,15" \
      --out /tmp/diagramma.dxf
  python katopsi.py plano --domatia "ΚΑΘΙΣΤΙΚΟ:0,0,5.2,4.1 ΚΟΥΖΙΝΑ:5.2,0,3.0,4.1" \
      --out /tmp/plano.dxf
  python katopsi.py emvadon --polygono "0,0 25,0 25,20 0,20"
  (πρόσθεσε --json για δομημένη έξοδο)
"""
from __future__ import annotations

import argparse
import json
import sys

_VERIFY = "[ΕΠΑΛΗΘΕΥΣΕ]"

WALL = 0.25  # m, ενδεικτικό πάχος τοίχου στο απλό πλάνο


def parse_points(text):
    """Ανάλυση «x,y x,y ...» σε λίστα σημείων. Τουλάχιστον τρία για πολύγωνο."""
    pts = []
    for token in text.replace(";", " ").split():
        parts = token.split(",")
        if len(parts) != 2:
            raise ValueError(f"Μη έγκυρο σημείο: {token}. Μορφή: x,y")
        pts.append((float(parts[0]), float(parts[1])))
    return pts


def shoelace(points):
    """Εμβαδόν απλού πολυγώνου με τον κανόνα των παπουτσιών, πάντοτε θετικό."""
    if len(points) < 3:
        raise ValueError("Ένα πολύγωνο θέλει τουλάχιστον τρία σημεία.")
    s = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def centroid(points):
    """Κεντροειδές πολυγώνου για την τοποθέτηση ετικέτας."""
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def parse_rooms(text):
    """Ανάλυση «ΟΝΟΜΑ:x,y,πλάτος,ύψος ...» σε λίστα δωματίων."""
    rooms = []
    for token in text.split():
        if ":" not in token:
            raise ValueError(f"Μη έγκυρο δωμάτιο: {token}. Μορφή: ΟΝΟΜΑ:x,y,w,h")
        name, geom = token.split(":", 1)
        parts = [float(v) for v in geom.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Το δωμάτιο {name} θέλει x,y,w,h.")
        x, y, w, h = parts
        if w <= 0 or h <= 0:
            raise ValueError(f"Το δωμάτιο {name} θέλει θετικές διαστάσεις.")
        rooms.append({"onoma": name, "x": x, "y": y, "w": w, "h": h})
    if not rooms:
        raise ValueError("Δώσε τουλάχιστον ένα δωμάτιο.")
    return rooms


def _require_ezdxf():
    try:
        import ezdxf
        return ezdxf
    except ImportError:
        raise SystemExit(
            "Η γραφή DXF απαιτεί το πακέτο ezdxf. Εγκατάσταση: pip install ezdxf. "
            "Οι υπολογισμοί εμβαδών (emvadon) δουλεύουν και χωρίς αυτό.")


def _new_doc(ezdxf):
    doc = ezdxf.new("R2018", setup=True)
    for name, color in [("ΟΙΚΟΠΕΔΟ", 1), ("ΚΤΙΡΙΟ", 5), ("ΤΟΙΧΟΙ", 7),
                        ("ΔΙΑΣΤΑΣΕΙΣ", 3), ("ΚΕΙΜΕΝΑ", 4)]:
        doc.layers.add(name, color=color)
    return doc


def _audit_roundtrip(ezdxf, path):
    doc2 = ezdxf.readfile(path)
    auditor = doc2.audit()
    return len(auditor.errors), len(doc2.modelspace())


def diagramma(oikopedo_pts, ktirio_pts, out_path):
    """Διάγραμμα κάλυψης: οικόπεδο, περίγραμμα κτιρίου, εμβαδά και ποσοστό.

    Επιστρέφει τα μεγέθη και γράφει το DXF, επιβεβαιωμένο με audit επανάγνωσης.
    """
    e_oik = shoelace(oikopedo_pts)
    e_kti = shoelace(ktirio_pts)
    if e_kti > e_oik:
        raise ValueError("Το κτίριο βγαίνει μεγαλύτερο από το οικόπεδο: έλεγξε τα σημεία.")
    kalypsi_pct = e_kti / e_oik * 100.0

    ezdxf = _require_ezdxf()
    from ezdxf.enums import TextEntityAlignment
    doc = _new_doc(ezdxf)
    msp = doc.modelspace()
    msp.add_lwpolyline(oikopedo_pts, close=True, dxfattribs={"layer": "ΟΙΚΟΠΕΔΟ"})
    msp.add_lwpolyline(ktirio_pts, close=True, dxfattribs={"layer": "ΚΤΙΡΙΟ"})
    cx, cy = centroid(ktirio_pts)
    span = max(p[0] for p in oikopedo_pts) - min(p[0] for p in oikopedo_pts)
    h_text = max(span / 40.0, 0.2)
    msp.add_text("ΚΤΙΡΙΟ", dxfattribs={"layer": "ΚΕΙΜΕΝΑ", "height": h_text}
                 ).set_placement((cx, cy + h_text), align=TextEntityAlignment.MIDDLE_CENTER)
    msp.add_text(f"E = {e_kti:.2f} τ.μ.", dxfattribs={"layer": "ΚΕΙΜΕΝΑ", "height": h_text * 0.8}
                 ).set_placement((cx, cy - h_text), align=TextEntityAlignment.MIDDLE_CENTER)
    ox, oy = centroid(oikopedo_pts)
    miny = min(p[1] for p in oikopedo_pts)
    label = (f"ΟΙΚΟΠΕΔΟ E = {e_oik:.2f} τ.μ.   ΚΑΛΥΨΗ = {e_kti:.2f} τ.μ. "
             f"({kalypsi_pct:.1f}%)")
    msp.add_text(label, dxfattribs={"layer": "ΚΕΙΜΕΝΑ", "height": h_text}
                 ).set_placement((ox, miny - 2.5 * h_text), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(out_path)
    errors, entities = _audit_roundtrip(ezdxf, out_path)
    if errors:
        raise RuntimeError(f"Το DXF γράφτηκε με {errors} σφάλματα audit: μην το χρησιμοποιήσεις.")
    return {
        "arxeio": out_path,
        "emvadon_oikopedou_m2": round(e_oik, 2),
        "emvadon_ktiriou_m2": round(e_kti, 2),
        "kalypsi_pct": round(kalypsi_pct, 2),
        "audit_sfalmata": errors,
        "ontotites": entities,
        "simeiosi": (
            "Υπόβαθρο εργασίας. Το επίσημο διάγραμμα κάλυψης συντάσσεται σε "
            f"εξαρτημένο τοπογραφικό και ελέγχεται με τους όρους δόμησης {_VERIFY}."
        ),
    }


def plano(rooms, out_path, wall=WALL):
    """Απλή κάτοψη από ορθογώνια δωμάτια: τοίχοι ως διπλή γραμμή, ετικέτες, εμβαδά.

    Κάθε δωμάτιο σχεδιάζεται ως εσωτερικό περίγραμμα συν εξωτερικό offset κατά
    το πάχος τοίχου. Απλουστευμένο υπόβαθρο: ανοίγματα και κουφώματα μπαίνουν
    στο CAD από τον μελετητή.
    """
    ezdxf = _require_ezdxf()
    from ezdxf.enums import TextEntityAlignment
    doc = _new_doc(ezdxf)
    msp = doc.modelspace()
    total = 0.0
    for r in rooms:
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        inner = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        outer = [(x - wall, y - wall), (x + w + wall, y - wall),
                 (x + w + wall, y + h + wall), (x - wall, y + h + wall)]
        msp.add_lwpolyline(inner, close=True, dxfattribs={"layer": "ΤΟΙΧΟΙ"})
        msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "ΤΟΙΧΟΙ"})
        area = w * h
        total += area
        cx, cy = x + w / 2, y + h / 2
        h_text = max(min(w, h) / 8.0, 0.12)
        msp.add_text(r["onoma"], dxfattribs={"layer": "ΚΕΙΜΕΝΑ", "height": h_text}
                     ).set_placement((cx, cy + h_text * 0.8), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(f"{area:.1f} τ.μ.", dxfattribs={"layer": "ΚΕΙΜΕΝΑ", "height": h_text * 0.7}
                     ).set_placement((cx, cy - h_text * 0.8), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(out_path)
    errors, entities = _audit_roundtrip(ezdxf, out_path)
    if errors:
        raise RuntimeError(f"Το DXF γράφτηκε με {errors} σφάλματα audit: μην το χρησιμοποιήσεις.")
    return {
        "arxeio": out_path,
        "domatia": len(rooms),
        "katharo_emvadon_m2": round(total, 2),
        "paxos_toixou_m": wall,
        "audit_sfalmata": errors,
        "ontotites": entities,
        "simeiosi": "Απλουστευμένο υπόβαθρο εργασίας, όχι σχέδιο άδειας.",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    di = sub.add_parser("diagramma", help="Διάγραμμα κάλυψης σε DXF")
    di.add_argument("--oikopedo", required=True, help="Κορυφές οικοπέδου: 'x,y x,y ...'")
    di.add_argument("--ktirio", required=True, help="Κορυφές κτιρίου: 'x,y x,y ...'")
    di.add_argument("--out", required=True, help="Διαδρομή εξόδου .dxf")
    di.add_argument("--json", action="store_true")

    pl = sub.add_parser("plano", help="Απλή κάτοψη από ορθογώνια δωμάτια")
    pl.add_argument("--domatia", required=True, help="'ΟΝΟΜΑ:x,y,w,h ΟΝΟΜΑ:x,y,w,h ...'")
    pl.add_argument("--out", required=True, help="Διαδρομή εξόδου .dxf")
    pl.add_argument("--toixos", type=float, default=WALL, help="Πάχος τοίχου σε m")
    pl.add_argument("--json", action="store_true")

    em = sub.add_parser("emvadon", help="Εμβαδόν πολυγώνου χωρίς DXF")
    em.add_argument("--polygono", required=True, help="Κορυφές: 'x,y x,y ...'")
    em.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "diagramma":
        r = diagramma(parse_points(args.oikopedo), parse_points(args.ktirio), args.out)
    elif args.cmd == "plano":
        r = plano(parse_rooms(args.domatia), args.out, args.toixos)
    else:
        pts = parse_points(args.polygono)
        r = {"simeia": len(pts), "emvadon_m2": round(shoelace(pts), 3)}

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        for key, val in r.items():
            print(f"  {key}: {val}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
