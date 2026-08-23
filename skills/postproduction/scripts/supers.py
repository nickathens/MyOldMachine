#!/usr/bin/env python3
"""Supers: derive the geometry from the font, and audit the legibility.

Two jobs, and the second one is the signature feature of this skill.

DERIVE, do not copy. A supers family is two edges and two steps, and if you
write the pixel numbers down you have described one job in one typeface. Derive
them from the font's own metrics and the family survives a change of face, a
change of raster and a change of copy.

MEASURE the ink against its ACTUAL ground, in CIE Lab, per block, at 1:1 on the
graded picture. Safe area is necessary and nowhere near sufficient. What hides a
glyph is ground the same COLOUR as the ink, not ground the same brightness, so a
luma only check cannot choose between two inks: run `contrast` below and watch
white and turquoise come out within 2 per cent of each other on a pale ground by
luma, and 4x apart in Lab.

Commands:
  metrics   the font's own numbers, and the measured width of a line
  plan      line positions from the anchors, with the safe area verdict
  contrast  ink against ground: Lab distance, and the luma number it beats
  safe      is this box inside action and title safe
  audit     the real measurement, on a rendered frame  (needs Pillow and numpy)

Usage:
  python supers.py metrics --font "/System/Library/Fonts/Supplemental/Arial Bold.ttf" --size 102 --text "MAKE IT COUNT"
  python supers.py plan SUPERS.json
  python supers.py contrast --ink "#FFFFFF" --ground "#E8E4DC"
  python supers.py safe --raster 3840x2160 --box 2100,1800,1500,200
  python supers.py audit FRAME.png --spec SUPERS.json --block b1
  (add --json for structured output)
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _colour as COL  # noqa: E402
import _common as C  # noqa: E402
import _ttf  # noqa: E402

# A dE2000 below this is where a glyph edge starts to disappear into its ground.
# It is a working threshold, not a standard: pick the shadow dose by rendering
# three or four at 1:1 on the worst grounds and LOOKING.
DE_FLOOR = 20.0


# ---------------------------------------------------------------- geometry


def plan(spec):
    """Place every line of every block from the anchors.

    Definitions, stated because renderers disagree:

      em_top    the ASCENDER line. Pillow's default 'la' anchor puts y here.
      em box    the box of height `size` whose top is em_top. The house anchor
                rule 'the last line's em box bottom sits on Y' means
                em_top(last) + size = Y.
      baseline  em_top + ascender scaled to size, which is where Pillow's 'ls'
                anchor wants y.
      ink       the real glyph extent, from the font's own per glyph boxes.
                Not the advance width: a line ends at its last glyph's ink, and
                on flush right anchoring that difference is visible.
    """
    font_path = spec["font"]
    if not os.path.exists(font_path):
        raise FileNotFoundError(f"No font at {font_path}")
    face = _ttf.Font(font_path)
    size = float(spec["size"])
    pitch = float(spec.get("pitch", size * 1.2))
    raster = spec.get("raster") or [1920, 1080]
    rw, rh = int(raster[0]), int(raster[1])
    safe = spec.get("safe") or {"action": 0.93, "title": 0.90}
    anchor = spec.get("anchor") or {}
    inks = spec.get("ink") or ["#FFFFFF"]
    vert = face.vertical(size)
    ascender_px = vert["ascender"]

    blocks = []
    for b in spec.get("blocks", []):
        lines = b.get("lines") or []
        if not lines:
            continue
        measured = [face.measure(line, size) for line in lines]
        n = len(lines)

        # Vertical: anchor on the last line's em box bottom, or on the first
        # line's em top, whichever the spec names.
        if "em_box_bottom_y" in anchor:
            top0 = float(anchor["em_box_bottom_y"]) - size - (n - 1) * pitch
        elif "em_top_y" in anchor:
            top0 = float(anchor["em_top_y"])
        elif "baseline_y" in anchor:
            top0 = float(anchor["baseline_y"]) - ascender_px
        else:
            raise ValueError("The spec's anchor must name one of em_box_bottom_y, "
                             "em_top_y or baseline_y. A vertical position is a "
                             "project fact and this will not invent one.")

        align = (b.get("align") or spec.get("align") or "flush_left").lower()
        placed = []
        for i, (line, m) in enumerate(zip(lines, measured)):
            em_top = top0 + i * pitch
            placed.append({"line": line, "index": i, "em_top": em_top,
                           "baseline": em_top + ascender_px,
                           "advance": m["advance"],
                           "ink_left_rel": m["ink_left"], "ink_right_rel": m["ink_right"],
                           "ink_top_rel": m["ink_top"], "ink_bottom_rel": m["ink_bottom"],
                           "ink_available": m["ink_available"],
                           "missing_glyphs": m["missing_glyphs"],
                           "ink": inks[i % len(inks)]})

        # Horizontal: one origin for the whole block on flush left, so the
        # widest line's INK edge is what the anchor pins.
        if align == "flush_left":
            if "ink_right_x" in anchor:
                widest = max(p["ink_right_rel"] or p["advance"] for p in placed)
                origin = float(anchor["ink_right_x"]) - widest
            elif "ink_left_x" in anchor:
                leftmost = min(p["ink_left_rel"] or 0.0 for p in placed)
                origin = float(anchor["ink_left_x"]) - leftmost
            elif "origin_x" in anchor:
                origin = float(anchor["origin_x"])
            else:
                raise ValueError("Flush left needs one of ink_right_x, ink_left_x "
                                 "or origin_x in the anchor.")
            for p in placed:
                p["origin_x"] = origin
        elif align == "centre":
            cx = float(anchor.get("centre_x", rw / 2.0))
            for p in placed:
                ink_w = ((p["ink_right_rel"] or p["advance"])
                         - (p["ink_left_rel"] or 0.0))
                p["origin_x"] = cx - ink_w / 2.0 - (p["ink_left_rel"] or 0.0)
        else:
            raise ValueError(f"Unknown align {align}. Use flush_left or centre.")

        for p in placed:
            p["ink_box"] = [p["origin_x"] + (p["ink_left_rel"] or 0.0),
                            p["baseline"] - (p["ink_top_rel"] or 0.0),
                            (p["ink_right_rel"] or p["advance"]) - (p["ink_left_rel"] or 0.0),
                            (p["ink_top_rel"] or 0.0) - (p["ink_bottom_rel"] or 0.0)]

        xs = [p["ink_box"][0] for p in placed]
        ys = [p["ink_box"][1] for p in placed]
        x2 = [p["ink_box"][0] + p["ink_box"][2] for p in placed]
        y2 = [p["ink_box"][1] + p["ink_box"][3] for p in placed]
        box = [min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)]
        blocks.append({"id": b.get("id", f"block{len(blocks) + 1}"),
                       "in": b.get("in"), "out": b.get("out"),
                       "align": align, "lines": placed, "ink_box": box,
                       "safe": safe_check(box, rw, rh, safe)})

    return {"font": os.path.abspath(font_path),
            "font_metrics": vert, "size": size, "pitch": pitch,
            "raster": [rw, rh], "blocks": blocks,
            "note": "Advance widths are UNKERNED: this reader does not apply "
                    "GPOS. At display sizes kerning moves a line by a pixel or "
                    "two, so check one real render against these numbers before "
                    "the family is locked."}


def safe_check(box, rw, rh, safe=None):
    """Is a box inside action safe and title safe, concentric per EBU R95."""
    safe = safe or {"action": 0.93, "title": 0.90}
    x, y, w, h = box
    out = {}
    for name, frac in (("action", safe.get("action", 0.93)),
                       ("title", safe.get("title", 0.90))):
        iw, ih = rw * frac, rh * frac
        left, top = (rw - iw) / 2.0, (rh - ih) / 2.0
        right, bottom = left + iw, top + ih
        overflow = {"left": max(0.0, left - x), "top": max(0.0, top - y),
                    "right": max(0.0, (x + w) - right),
                    "bottom": max(0.0, (y + h) - bottom)}
        out[name] = {"fraction": frac,
                     "area": [round(left, 2), round(top, 2), round(iw, 2), round(ih, 2)],
                     "inside": all(v <= 0.001 for v in overflow.values()),
                     "overflow_px": {k: round(v, 2) for k, v in overflow.items()}}
    return out


# ---------------------------------------------------------------- contrast


def contrast(ink, ground):
    """Ink against ground, in Lab and in luma, side by side.

    The luma number is printed only to show what it cannot do. On a pale ground
    white and turquoise sit within a couple of per cent of each other by
    contrast ratio, and four times apart in Lab, and it is the Lab number that
    matches what the eye reports.
    """
    ink_rgb, ground_rgb = COL.hex_to_rgb(ink), COL.hex_to_rgb(ground)
    lab_a, lab_b = COL.rgb_to_lab(ink_rgb), COL.rgb_to_lab(ground_rgb)
    de = COL.delta_e_2000(lab_a, lab_b)
    ratio = COL.contrast_ratio(ink_rgb, ground_rgb)
    return {"ink": ink, "ground": ground,
            "ink_lab": [round(v, 2) for v in lab_a],
            "ground_lab": [round(v, 2) for v in lab_b],
            "delta_e_2000": round(de, 2),
            "wcag_contrast_ratio": round(ratio, 2),
            "floor": DE_FLOOR,
            "verdict": ("separated" if de >= DE_FLOOR else
                        "TOO CLOSE: at this distance the glyph edge dissolves "
                        "into its ground. Either change the ink or put "
                        "something between them, which usually means a soft "
                        "shadow scaled to the type."),
            "note": "dE2000 is the measure that agrees with the eye here. The "
                    "contrast ratio is shown so the two can be compared: a luma "
                    "only check cannot tell two inks apart on a coloured ground."}


def shadow_dose(size, strength="soft"):
    """A shadow scaled to the type, not to the frame.

    Doses measured on real pale films: the soft one is invisible on dark shots
    and load bearing on pale ones. These are starting points to look at, never
    numbers to ship unseen.
    """
    table = {
        "hairline": (0.02, 0.09, 0.40),
        "soft": (0.04, 0.14, 0.70),
        "heavy": (0.06, 0.18, 0.85),
    }
    if strength not in table:
        raise ValueError(f"Unknown dose {strength}. Try: {', '.join(table)}")
    dy, blur, alpha = table[strength]
    return {"strength": strength, "offset_y_px": round(size * dy, 2),
            "blur_px": round(size * blur, 2), "alpha": alpha,
            "recipe": f"black at {int(alpha * 100)} per cent, offset "
                      f"{size * dy:.1f}px down, blurred {size * blur:.1f}px",
            "note": "Scaled to the type size so it holds across rasters. Render "
                    "three doses at 1:1 on the WORST ground in the film and pick "
                    "by eye; a number picked from a table has never been looked "
                    "at on the shot it has to survive."}


# ---------------------------------------------------------------- audit


def audit(frame_path, spec, block_id=None, ring=6):
    """Measure each block's ink against the ground it actually sits on.

    Renders the block's glyph mask at 1:1, takes a ring of pixels just outside
    the glyph edges, and measures the Lab distance from the ink to that ring's
    real colours. Reports the FRACTION of the surround that falls below the
    floor, because a block does not fail all over: it fails where one bright
    object sits behind two words.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "The audit needs Pillow and numpy, which live in this skill's own "
            "environment, never the bot's:\n"
            "  python3 -m venv ~/.venvs/post\n"
            "  ~/.venvs/post/bin/pip install pillow numpy\n"
            "then run this with ~/.venvs/post/bin/python. Everything else in "
            f"supers.py works without them. ({exc})"
        ) from exc

    laid = plan(spec)
    frame = Image.open(frame_path).convert("RGB")
    if list(frame.size) != laid["raster"]:
        raise ValueError(
            f"The frame is {frame.size[0]}x{frame.size[1]} and the spec's raster "
            f"is {laid['raster'][0]}x{laid['raster'][1]}. Measure at 1:1 or the "
            "answer is about a resampled picture, not the film."
        )
    pixels = np.asarray(frame).astype(np.float64) / 255.0
    font = ImageFont.truetype(spec["font"], int(round(float(spec["size"]))))

    results = []
    for block in laid["blocks"]:
        if block_id and block["id"] != block_id:
            continue
        per_line = []
        for line in block["lines"]:
            mask = Image.new("L", frame.size, 0)
            draw = ImageDraw.Draw(mask)
            draw.text((line["origin_x"], line["em_top"]), line["line"],
                      font=font, fill=255, anchor="la")
            glyph = np.asarray(mask) > 128
            grown = np.asarray(mask.filter(ImageFilter.MaxFilter(2 * ring + 1))) > 128
            surround = grown & ~glyph
            count = int(surround.sum())
            if not count:
                per_line.append({"line": line["line"],
                                 "error": "no surround pixels; is the line off frame"})
                continue
            ground = pixels[surround]
            ink_lab = COL.rgb_to_lab(COL.hex_to_rgb(line["ink"]))
            # Quantise the ground so the Lab conversion runs on unique colours
            # rather than on every pixel: same answer, seconds instead of minutes.
            q = np.round(ground * 255.0).astype(np.int64)
            keys, counts = np.unique(q[:, 0] * 65536 + q[:, 1] * 256 + q[:, 2],
                                     return_counts=True)
            des = []
            for key in keys:
                r, g, b = (key >> 16) & 255, (key >> 8) & 255, key & 255
                des.append(COL.delta_e_2000(
                    ink_lab, COL.rgb_to_lab((r / 255.0, g / 255.0, b / 255.0))))
            des = np.asarray(des)
            weights = counts / counts.sum()
            order = np.argsort(des)
            sorted_de, sorted_w = des[order], weights[order]
            cum = np.cumsum(sorted_w)
            median = float(sorted_de[int(np.searchsorted(cum, 0.5))])
            p05 = float(sorted_de[int(np.searchsorted(cum, 0.05))])
            below = float(sorted_w[sorted_de < DE_FLOOR].sum())
            per_line.append({
                "line": line["line"], "ink": line["ink"],
                "surround_pixels": count,
                "de_median": round(median, 2),
                "de_5th_percentile": round(p05, 2),
                "fraction_below_floor": round(below, 4),
                "verdict": ("holds" if below < 0.02 else
                            f"{below * 100:.1f} per cent of this line's glyph "
                            f"surround is within dE {DE_FLOOR} of the ink"),
            })
        worst = max((ln.get("fraction_below_floor", 0.0) for ln in per_line),
                    default=0.0)
        results.append({
            "block": block["id"], "safe": block["safe"], "lines": per_line,
            "worst_fraction_below_floor": round(worst, 4),
            "recommendation": (
                "nothing needed" if worst < 0.02 else
                "put a soft shadow under the type, scaled to the size: "
                + shadow_dose(laid["size"], "soft")["recipe"]),
        })
    return {"frame": os.path.abspath(frame_path), "ring_px": ring,
            "floor_de2000": DE_FLOOR, "blocks": results,
            "note": "Measured at 1:1 on the frame given. Run it on the GRADED "
                    "picture: a grade moves the ground, and the ground is half "
                    "of this measurement."}


# ---------------------------------------------------------------- cli


def _raster(text):
    m = re.match(r"^(\d+)\s*[xX*]\s*(\d+)$", text.strip())
    if not m:
        raise ValueError("Raster looks like 1920x1080")
    return int(m.group(1)), int(m.group(2))


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    me = sub.add_parser("metrics", help="Font metrics and a measured line")
    me.add_argument("--font", required=True)
    me.add_argument("--size", type=float)
    me.add_argument("--text")
    me.add_argument("--index", type=int, default=0, help="Face inside a .ttc")
    C.add_json(me)

    pl = sub.add_parser("plan", help="Line positions from the anchors")
    pl.add_argument("spec")
    C.add_json(pl)

    co = sub.add_parser("contrast", help="Ink against ground")
    co.add_argument("--ink", required=True)
    co.add_argument("--ground", required=True)
    C.add_json(co)

    sf = sub.add_parser("safe", help="Box against action and title safe")
    sf.add_argument("--raster", required=True)
    sf.add_argument("--box", required=True, help="x,y,w,h")
    C.add_json(sf)

    do = sub.add_parser("shadow", help="A shadow dose scaled to the type")
    do.add_argument("--size", type=float, required=True)
    do.add_argument("--strength", default="soft")
    C.add_json(do)

    au = sub.add_parser("audit", help="Legibility on a real frame")
    au.add_argument("frame")
    au.add_argument("--spec", required=True)
    au.add_argument("--block")
    au.add_argument("--ring", type=int, default=6)
    C.add_json(au)

    args = ap.parse_args(argv)

    if args.cmd == "metrics":
        face = _ttf.Font(args.font, args.index)
        out = {"font": os.path.abspath(args.font), "vertical": face.vertical(args.size)}
        if args.text:
            out["line"] = face.measure(args.text, args.size)
        return C.emit(out, args.json, _print_metrics)
    if args.cmd == "plan":
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
        return C.emit(plan(spec), args.json, _print_plan)
    if args.cmd == "contrast":
        return C.emit(contrast(args.ink, args.ground), args.json, _print_contrast)
    if args.cmd == "safe":
        rw, rh = _raster(args.raster)
        box = [float(v) for v in re.findall(r"-?\d+\.?\d*", args.box)]
        if len(box) != 4:
            raise ValueError("--box wants x,y,w,h")
        res = {"raster": [rw, rh], "box": box, "safe": safe_check(box, rw, rh)}
        return C.emit(res, args.json, lambda r: [
            print(f"  {name}: {'inside' if v['inside'] else 'OUTSIDE'} "
                  f"({int(v['fraction'] * 100)} per cent area "
                  f"{v['area'][2]:.0f}x{v['area'][3]:.0f})"
                  + ("" if v["inside"] else
                     "  overflow " + ", ".join(f"{k} {x}" for k, x in
                                               v["overflow_px"].items() if x > 0)))
            for name, v in r["safe"].items()])
    if args.cmd == "shadow":
        return C.emit(shadow_dose(args.size, args.strength), args.json,
                      lambda r: (print(f"  {r['recipe']}"), print(f"  {r['note']}")))
    if args.cmd == "audit":
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
        return C.emit(audit(args.frame, spec, args.block, args.ring), args.json,
                      _print_audit)
    return 0


def _print_metrics(out):
    v = out["vertical"]
    unit = "px" if v.get("size") else "font units"
    print(f"{os.path.basename(out['font'])}  ({v['flavour']}, {v['glyphs']} glyphs, "
          f"{v['units_per_em']} units per em)")
    for key in ("ascender", "descender", "line_gap", "cap_height", "x_height"):
        if v.get(key) is not None:
            print(f"  {key:<12} {v[key]:.2f} {unit}")
    if "line" in out:
        m = out["line"]
        print(f"\n  '{m['text']}'")
        if not m["ink_available"]:
            print("  ink box unavailable: this face has CFF outlines, so per "
                  "glyph boxes are not readable here. Advance widths are exact.")
        else:
            print(f"  advance {m['advance']:.2f}, ink {m['ink_left']:.2f} to "
                  f"{m['ink_right']:.2f} ({m['ink_right'] - m['ink_left']:.2f} wide), "
                  f"height {m['ink_top']:.2f} to {m['ink_bottom']:.2f}")
        if m["missing_glyphs"]:
            print(f"  MISSING GLYPHS: {''.join(m['missing_glyphs'])}. They will "
                  "render as the notdef box, and nothing about the width below "
                  "is right.")
        print("  advance widths are UNKERNED")


def _print_plan(p):
    print(f"{os.path.basename(p['font'])} at {p['size']}, pitch {p['pitch']}, "
          f"raster {p['raster'][0]}x{p['raster'][1]}\n")
    for b in p["blocks"]:
        print(f"  block {b['id']}  ({b['align']})")
        for line in b["lines"]:
            print(f"    '{line['line']}'  ink {line['ink']}")
            print(f"      draw at x {line['origin_x']:.2f}, y {line['em_top']:.2f} "
                  f"(Pillow anchor 'la'), baseline {line['baseline']:.2f}")
            box = line["ink_box"]
            print(f"      ink box {box[0]:.1f},{box[1]:.1f} {box[2]:.1f}x{box[3]:.1f}")
        for name, v in b["safe"].items():
            state = "inside" if v["inside"] else "OUTSIDE"
            extra = ("" if v["inside"] else
                     "  " + ", ".join(f"{k} by {x}px" for k, x in
                                      v["overflow_px"].items() if x > 0))
            print(f"      {name} safe: {state}{extra}")
        print()
    print(f"  {p['note']}")


def _print_contrast(r):
    print(f"  ink {r['ink']} on ground {r['ground']}")
    print(f"  dE2000 {r['delta_e_2000']}   (floor {r['floor']})")
    print(f"  WCAG contrast ratio {r['wcag_contrast_ratio']}")
    print(f"  {r['verdict']}")
    print(f"  {r['note']}")


def _print_audit(r):
    print(f"{os.path.basename(r['frame'])}, ring {r['ring_px']}px, "
          f"floor dE {r['floor_de2000']}\n")
    for b in r["blocks"]:
        print(f"  block {b['block']}")
        for line in b["lines"]:
            if "error" in line:
                print(f"    '{line['line']}': {line['error']}")
                continue
            print(f"    '{line['line']}' in {line['ink']}: median dE "
                  f"{line['de_median']}, 5th percentile {line['de_5th_percentile']}, "
                  f"{line['fraction_below_floor'] * 100:.1f} per cent below floor")
            print(f"      {line['verdict']}")
        for name, v in b["safe"].items():
            print(f"    {name} safe: {'inside' if v['inside'] else 'OUTSIDE'}")
        print(f"    {b['recommendation']}")
        print()
    print(f"  {r['note']}")


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
