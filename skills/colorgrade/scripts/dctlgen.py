#!/usr/bin/env python3
"""Write a LensIsolate DCTL already aimed at one object in one frame.

    python scripts/dctlgen.py FRAME.png --at 0.877,0.745 --out MyLens.dctl
    python scripts/dctlgen.py FRAME.png --at 0.877,0.745 --hue-shift -25 --preview p.png

The point of a DCTL rather than a LUT: a DCTL is handed the pixel coordinate,
so it can gate on WHERE a pixel is as well as what colour it is. A .cube LUT
is colour in, colour out and structurally cannot do that. This matters because
a brand colour is usually shared by something else in frame, and the only
professional answer is a colour qualifier plus a spatial window.

The generator clicks once for you: it grows the object out from a seed point,
measures the hue band and the bounding box that actually contain it, and bakes
those in as the slider defaults. Then it proves the aim on the still, counting
how many pixels the matte took inside the object and how many it took anywhere
else, before the file ever reaches Resolve.

Route into Resolve, verified on a real Studio 21 machine: Effects, OpenFX,
ResolveFX Color, DCTL, dragged onto the node, then pick the file in the DCTL
List dropdown. Dragging a .dctl from the LUT browser does not work for a DCTL
that carries UI parameters, and never will.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgcore as C

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "dctl", "LensIsolate.dctl")


def load_rgb(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0


def hsv_planes(rgb):
    h, s, v = C.rgb_to_hsv(rgb)
    return h, s, v


def grow_region(h, s, v, seed_xy, hue_tol=18.0, sat_tol=0.30, val_tol=0.45):
    """Connected region around the seed that shares its colour.

    Deliberately simple and explainable. It is a seeded threshold plus one
    connected component, not a segmentation model: the whole point is that the
    result can be read off as four hue numbers and a box, which is what the
    DCTL can actually act on.
    """
    from scipy import ndimage

    H, W = h.shape
    px = int(round(np.clip(seed_xy[0], 0, 1) * (W - 1)))
    py = int(round(np.clip(seed_xy[1], 0, 1) * (H - 1)))

    # seed colour from a small patch, so one odd pixel cannot set the target
    r = max(2, min(H, W) // 200)
    sl = (slice(max(py - r, 0), py + r + 1), slice(max(px - r, 0), px + r + 1))
    hs = h[sl].ravel()
    ang = np.radians(hs)
    hue0 = float(np.degrees(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360.0)
    sat0, val0 = float(np.median(s[sl])), float(np.median(v[sl]))

    dh = np.abs(((h - hue0 + 180.0) % 360.0) - 180.0)
    mask = (dh <= hue_tol) & (np.abs(s - sat0) <= sat_tol) & (np.abs(v - val0) <= val_tol)

    lab, n = ndimage.label(mask)
    if n == 0 or lab[py, px] == 0:
        # the seed itself fell outside, take the largest blob that touches the
        # seed's neighbourhood instead of failing
        lab2 = lab[sl]
        ids = [i for i in np.unique(lab2) if i]
        if not ids:
            raise SystemExit("nothing found at that point, try another seed")
        sizes = [(lab == i).sum() for i in ids]
        comp = ids[int(np.argmax(sizes))]
    else:
        comp = lab[py, px]
    region = lab == comp
    return region, hue0, sat0, val0


def measure_params(h, s, v, region, pad=1.35, soft=0.25):
    ys, xs = np.nonzero(region)
    H, W = h.shape
    cx, cy = (xs.mean() + 0.5) / W, (ys.mean() + 0.5) / H
    # half extents from percentiles, so a few stray pixels cannot inflate the box
    rx = (np.percentile(xs, 99) - np.percentile(xs, 1)) / 2.0 / W * pad
    ry = (np.percentile(ys, 99) - np.percentile(ys, 1)) / 2.0 / H * pad

    hh = h[region]
    ang = np.radians(hh)
    centre = float(np.degrees(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360.0)
    rel = ((hh - centre + 180.0) % 360.0) - 180.0
    lo, hi = np.percentile(rel, 2), np.percentile(rel, 98)
    lo_s, hi_s = np.percentile(rel, 0.2), np.percentile(rel, 99.8)

    ss, vv = s[region], v[region]
    return _pack(centre, lo, hi, lo_s, hi_s, ss, vv, cx, cy, rx, ry, soft)


def _pack(centre, lo, hi, lo_s, hi_s, ss, vv, cx, cy, rx, ry, soft):
    return dict(
        hueLowSoft=(centre + lo_s) % 360.0,
        hueLow=(centre + lo) % 360.0,
        hueHigh=(centre + hi) % 360.0,
        hueHighSoft=(centre + hi_s) % 360.0,
        satLo=max(0.0, float(np.percentile(ss, 2)) * 0.6),
        satHi=max(0.02, float(np.percentile(ss, 10))),
        valLo=max(0.0, float(np.percentile(vv, 2)) * 0.6),
        valHi=max(0.02, float(np.percentile(vv, 10))),
        winX=float(cx), winY=float(cy),
        winRX=float(rx), winRY=float(ry), winSoft=float(soft),
    )


def fill_inside_window(h, s, v, p, hue_tol=32.0):
    """Re measure the colour gate over everything inside the window.

    Measured, not guessed: a seeded region grow finds the bright rim of a glass
    object and leaves the pale interior behind, because the interior is too
    washed out to match on colour. On a glass lens that left a hollow ring
    where the disc should be solid.

    Once the window is holding the area, the colour gate can be loosened a very
    long way, because nothing else is inside the window to go wrong. That is
    what makes one file enough for this class of shot, with no rendered matte
    and no object model. The window is doing the separating, and the colour gate
    is only there to keep the edge honest.
    """
    H, W = h.shape
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.hypot(((xx + 0.5) / W - p["winX"]) / max(p["winRX"], 1e-6),
                 ((yy + 0.5) / H - p["winY"]) / max(p["winRY"], 1e-6))
    inside = d <= 1.0
    if inside.sum() < 64:
        return p

    centre = (p["hueLow"] + ((p["hueHigh"] - p["hueLow"] + 540) % 360 - 180) / 2.0) % 360
    rel_all = ((h - centre + 180.0) % 360.0) - 180.0
    near = inside & (np.abs(rel_all) <= hue_tol)
    if near.sum() < 64:
        return p

    # Loosen SATURATION, hold VALUE. The pale interior of a glassy object is
    # desaturated but still bright, so dropping the saturation floor claims it.
    # Dropping the value floor as well is what let the dark denim behind
    # a glass lens into the matte, because denim shares the blue band and only
    # brightness told them apart. The value gate stays where the seed region
    # put it for exactly that reason.
    rel = rel_all[near]
    ss = s[near]
    q = dict(p)
    q["hueLow"] = (centre + np.percentile(rel, 1)) % 360.0
    q["hueHigh"] = (centre + np.percentile(rel, 99)) % 360.0
    q["hueLowSoft"] = (centre + np.percentile(rel, 1) - 6.0) % 360.0
    q["hueHighSoft"] = (centre + np.percentile(rel, 99) + 6.0) % 360.0
    q["satLo"] = max(0.0, float(np.percentile(ss, 1)) * 0.5)
    q["satHi"] = max(q["satLo"] + 0.02, float(np.percentile(ss, 5)))
    return q


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def matte_from_params(h, s, v, p):
    """The same gate the DCTL applies, in numpy, so the aim can be scored."""
    centre = (p["hueLow"] + ((p["hueHigh"] - p["hueLow"] + 540) % 360 - 180) / 2.0) % 360
    rel = ((h - centre + 180.0) % 360.0) - 180.0
    rl_s = ((p["hueLowSoft"] - centre + 180.0) % 360.0) - 180.0
    rl = ((p["hueLow"] - centre + 180.0) % 360.0) - 180.0
    rh = ((p["hueHigh"] - centre + 180.0) % 360.0) - 180.0
    rh_s = ((p["hueHighSoft"] - centre + 180.0) % 360.0) - 180.0
    m = _smoothstep(rl_s, rl, rel) * (1.0 - _smoothstep(rh, rh_s, rel))
    m = m * _smoothstep(p["satLo"], p["satHi"], s) * _smoothstep(p["valLo"], p["valHi"], v)

    H, W = h.shape
    yy, xx = np.mgrid[0:H, 0:W]
    nx = (xx + 0.5) / W
    ny = (yy + 0.5) / H
    d = np.hypot((nx - p["winX"]) / max(p["winRX"], 1e-6),
                 (ny - p["winY"]) / max(p["winRY"], 1e-6))
    w = 1.0 - _smoothstep(1.0 - np.clip(p["winSoft"], 0.0, 0.999), 1.0, d)
    return (m * w).astype(np.float32)


def write_dctl(params, out_path, hue_shift=0.0, sat_gain=1.0, template=TEMPLATE_PATH):
    with open(template) as f:
        src = f.read()
    vals = dict(params)
    vals["hueShift"] = hue_shift
    vals["satGain"] = sat_gain
    out = []
    for line in src.splitlines():
        if line.startswith("DEFINE_UI_PARAMS("):
            inner = line[len("DEFINE_UI_PARAMS("):line.rindex(")")]
            parts = [x.strip() for x in inner.split(",")]
            name = parts[0]
            if name in vals and parts[2] == "DCTLUI_SLIDER_FLOAT":
                parts[3] = f"{float(vals[name]):.4f}"
                line = "DEFINE_UI_PARAMS(" + ", ".join(parts) + ")"
        out.append(line)
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame", help="a still from the shot, PNG or JPG")
    ap.add_argument("--at", required=True,
                    help="seed point inside the object, as x,y in 0..1 from top left")
    ap.add_argument("--out", required=True, help="where to write the .dctl")
    ap.add_argument("--hue-shift", type=float, default=0.0, help="degrees")
    ap.add_argument("--sat-gain", type=float, default=1.0)
    ap.add_argument("--window-pad", type=float, default=1.35)
    ap.add_argument("--window-soft", type=float, default=0.25)
    ap.add_argument("--hue-tol", type=float, default=18.0,
                    help="tolerance for the seeded region grow")
    ap.add_argument("--fill-tol", type=float, default=20.0,
                    help="tolerance for the loosened gate measured inside the window")
    ap.add_argument("--no-fill", action="store_true",
                    help="keep the tight gate from the region grow, which finds "
                         "the rim of a glassy object and leaves the centre hollow")
    ap.add_argument("--preview", help="write a proof image: matte and result")
    args = ap.parse_args()

    rgb = load_rgb(args.frame)
    h, s, v = hsv_planes(rgb)
    seed = tuple(float(x) for x in args.at.split(","))
    region, hue0, sat0, val0 = grow_region(h, s, v, seed, hue_tol=args.hue_tol)
    p = measure_params(h, s, v, region, pad=args.window_pad, soft=args.window_soft)
    if not args.no_fill:
        p = fill_inside_window(h, s, v, p, hue_tol=args.fill_tol)

    print(f"seed colour: hue {hue0:.1f}  sat {sat0:.2f}  value {val0:.2f}")
    print(f"seed region:  {int(region.sum())} px, "
          f"{region.sum() / region.size * 100:.2f}% of frame")
    print(f"hue band  {p['hueLowSoft']:.1f} / {p['hueLow']:.1f} .. "
          f"{p['hueHigh']:.1f} / {p['hueHighSoft']:.1f}")
    print(f"window    x {p['winX']:.3f}  y {p['winY']:.3f}  "
          f"w {p['winRX']:.3f}  h {p['winRY']:.3f}  soft {p['winSoft']:.2f}")

    # score the aim: with the window, and without it, so the window's
    # contribution is a number rather than a claim
    m_win = matte_from_params(h, s, v, p)
    p_open = dict(p, winRX=9.0, winRY=9.0)
    m_open = matte_from_params(h, s, v, p_open)
    H, W = h.shape
    yy, xx = np.mgrid[0:H, 0:W]
    dwin = np.hypot(((xx + 0.5) / W - p["winX"]) / max(p["winRX"], 1e-6),
                    ((yy + 0.5) / H - p["winY"]) / max(p["winRY"], 1e-6))
    outside = dwin > 1.0
    print(f"\nmatte, colour gate alone:   {m_open.sum():10.0f} px, "
          f"of which {m_open[outside].sum():.0f} elsewhere in the frame")
    print(f"matte, with the window:     {m_win.sum():10.0f} px, "
          f"of which {m_win[outside].sum():.0f} elsewhere in the frame")
    partial = int(((m_win > 0.01) & (m_win < 0.99)).sum())
    print(f"soft rim pixels:            {partial} "
          f"(these are what a hard matte would get wrong)")

    write_dctl(p, args.out, hue_shift=args.hue_shift, sat_gain=args.sat_gain)
    print(f"\nwrote {args.out}")
    print("In Resolve: Effects, OpenFX, ResolveFX Color, DCTL onto the node, "
          "then pick it in the DCTL List dropdown.")

    if args.preview:
        from PIL import Image
        graded = rgb.copy()
        if args.hue_shift or args.sat_gain != 1.0:
            lab = C.lin_to_lab(C.code_to_lin(rgb))
            ang = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) + args.hue_shift * m_win
            ch = np.hypot(lab[..., 1], lab[..., 2]) * (1.0 + (args.sat_gain - 1.0) * m_win)
            lab[..., 1] = np.cos(np.radians(ang)) * ch
            lab[..., 2] = np.sin(np.radians(ang)) * ch
            graded = np.clip(C.lin_to_code(np.maximum(C.lab_to_lin(lab), 0)), 0, 1)
        strip = np.concatenate([rgb, np.repeat(m_win[..., None], 3, axis=2), graded], axis=1)
        Image.fromarray(np.clip(strip * 255 + 0.5, 0, 255).astype(np.uint8)).save(args.preview)
        print(f"wrote {args.preview}  (source, matte, result)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
