#!/usr/bin/env python3
"""Repair a colour change the edit carried in, on part of the frame only.

The case this was built for: a film cuts on one half of a split screen and the
colour of the OTHER half changes at the same frame, with no cut there. The
picture runs straight through; only the colour steps. Nothing in the grader can
see it, because it is not a shot boundary and it is not a drift.

Four instruments, in the order they have to be used.

**Cut or colour change.** A step in the picture can be either, and they want
opposite treatment. Edge correlation settles it: correlate the gradient map
across the suspect frame. A real cut in the same shot reads about +0.01. A
colour change reads whatever the neighbouring frames read, because the picture
is continuous and only its colour moved. On the piece this came from: +0.843
across the suspect frame against +0.842 and +0.946 either side, while a genuine
cut in the same panel read +0.007.

**How big, measured only where nothing moves.** Anything that moves between the
two states, a rotating disc, a title fading in, contaminates the number. Divide
the frame into cells, keep only cells that are still in BOTH states and are not
flat noise, and measure on those. Two ordinary frames in the same film give the
floor: 0.29 and 0.73 code levels, against 5.06 at the fault.

**How far it reaches.** Measure it, do not assume the panel edge. The change on
that piece covered the picture AND the divider bar, out to a column 44 past the
seam. Assuming the edge would have left a visible strip.

**What model can express it.** A hue selective change cannot be written as a 3x3
matrix; test that rather than assume it. And fit the DELTA, not the map. Fitting
B to A directly means the ridge term pulls the answer toward the zero FUNCTION,
which is black, and that put a 2.3 level blue lift into pure black on a film
whose black anchor was a headline result. Fitting A minus B pulls toward no
change, which is the right prior everywhere the fit has no data.

One honest limit, stated because it is measurable: some of a change like this is
not a function of colour at all. On that piece about a fifth of it was not, and
no colour correction of any kind can remove that part.

    $PY scripts/cgfix.py iscut  IN.mp4 --at 479 --band 0,1940
    $PY scripts/cgfix.py extent IN.mp4 --before 474,478 --after 479,483
    $PY scripts/cgfix.py model  IN.mp4 --before 474,478 --after 479,483 --band 0,1940
    $PY scripts/cgfix.py apply  IN.mp4 --out FIXED.mp4 --frames 479,510 \\
        --before 474,478 --after 479,483 --band 0,1940
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cgpanel as P


# ---------------------------------------------------------------- cut or not


def edge_corr(path, frame, x0=None, x1=None, span=4, scale=480):
    """Gradient map correlation across each frame near `frame`.

    Returns [(n, corr)]. A cut breaks the correlation to near zero. A colour
    change leaves it where its neighbours are, because the picture is continuous.
    """
    a, b = max(0, frame - span), frame + span - 1
    arr, _ = P.stream(path, a, b, x0, x1, scale=scale)
    f = arr.astype(np.float32)

    def corr(u, v):
        gxu, gxv = np.diff(u, axis=1), np.diff(v, axis=1)
        gyu, gyv = np.diff(u, axis=0), np.diff(v, axis=0)
        out = []
        for p, q in ((gxu, gxv), (gyu, gyv)):
            p = p.ravel() - p.mean()
            q = q.ravel() - q.mean()
            out.append((p * q).sum() / max(np.sqrt((p * p).sum() * (q * q).sum()), 1e-9))
        return float(np.mean(out))

    return [(a + i, corr(f[i - 1], f[i])) for i in range(1, len(f))]


# ---------------------------------------------------------------- how big


def static_cells(path, before, after, x0=None, x1=None,
                 rows=48, cols=44, still=1.2, flat=9.0, dark=12.0):
    """Mean colour of every cell that holds still in BOTH states.

    `before` and `after` are (first, last) frame ranges either side of the step,
    deliberately adjacent so as little content as possible drifts between them.
    A cell is kept when it barely changes within each range (still), is not a
    detailed patch whose mean means nothing (flat), and is not crushed black.
    """
    A, sa = P.stream(path, before[0], before[1], x0, x1, pix="rgb24")
    B, sb = P.stream(path, after[0], after[1], x0, x1, pix="rgb24")
    P.require_same_path(sa, sb)
    A = A.astype(np.float32)
    B = B.astype(np.float32)

    H, W = A.shape[1], A.shape[2]
    ch, cw = H // rows, W // cols

    def cells(V):
        v = V[:, :rows * ch, :cols * cw].reshape(len(V), rows, ch, cols, cw, 3)
        m = v.mean((2, 4))
        return m.mean(0), m.std(0).max(-1), v.std((2, 4)).mean(-1).mean(0)

    mA, tA, spA = cells(A)
    mB, tB, spB = cells(B)
    keep = ((tA < still) & (tB < still) & (spA < flat) & (spB < flat)
            & (mA.mean(-1) > dark) & (mB.mean(-1) > dark))
    return mA[keep], mB[keep], int(keep.sum()), int(keep.size)


def extent(path, before, after, still=1.4, min_rows=60):
    """Where in x the colour actually changed, column by column.

    Measured on static pixels only, for the same reason the cells are: a moving
    object would put a false step at its own edge.
    """
    A, sa = P.stream(path, before[0], before[1], pix="rgb24")
    B, sb = P.stream(path, after[0], after[1], pix="rgb24")
    P.require_same_path(sa, sb)
    A = A.astype(np.float32)
    B = B.astype(np.float32)
    mA, mB = A.mean(0), B.mean(0)
    static = (A.std(0).max(2) < still) & (B.std(0).max(2) < still)
    d = np.abs(mA - mB).mean(2)
    prof = np.array([d[:, x][static[:, x]].mean() if static[:, x].sum() > min_rows
                     else np.nan for x in range(d.shape[1])])
    return prof


# ---------------------------------------------------------------- the model


def _rbf(x, centres, eps):
    return np.exp(-((x[:, None, :] - centres[None, :, :]) ** 2).sum(-1) / (2 * eps * eps))


def _linear(x):
    return np.hstack([x, np.ones((len(x), 1), dtype=x.dtype)])


def fit_delta(A, B, ncentres=48, eps=0.14, lam=1e-4, reps=8, seed=11):
    """Fit A minus B as a function of B, scored on held out cells.

    Fitting the DELTA and not the map is the whole point. A ridge term pulls the
    solution toward the zero function; on a delta that is "change nothing", which
    is the correct prior wherever there is no data, and on a map it is "output
    black", which invented a 2.3 level blue lift in pure black.
    """
    A = np.asarray(A, np.float64) / 255.0
    B = np.asarray(B, np.float64) / 255.0
    D = A - B
    rng = np.random.default_rng(seed)

    def design(x, centres):
        if centres is None:
            return _linear(x)
        return np.hstack([_rbf(x, centres, eps), _linear(x)])

    results = {}
    for name, nc in (("do nothing", "none"), ("3x3 matrix", None),
                     (f"rbf {ncentres} eps{eps}", ncentres)):
        tr, te, worst = [], [], []
        for _ in range(reps):
            idx = rng.permutation(len(A))
            cut = int(0.7 * len(A))
            i0, i1 = idx[:cut], idx[cut:]
            if nc == "none":
                tr.append(np.abs(D[i0]).mean() * 255)
                te.append(np.abs(D[i1]).mean() * 255)
                worst.append(np.abs(D[i1]).mean(1).max() * 255)
                continue
            centres = None if nc is None else B[i0][rng.choice(cut, min(nc, cut),
                                                               replace=False)]
            Xtr, Xte = design(B[i0], centres), design(B[i1], centres)
            W = np.linalg.solve(Xtr.T @ Xtr + lam * np.eye(Xtr.shape[1]), Xtr.T @ D[i0])
            tr.append(np.abs(Xtr @ W - D[i0]).mean() * 255)
            te.append(np.abs(Xte @ W - D[i1]).mean() * 255)
            worst.append(np.abs(Xte @ W - D[i1]).mean(1).max() * 255)
        results[name] = {"train": float(np.mean(tr)), "held_out": float(np.mean(te)),
                         "worst": float(np.mean(worst))}

    centres = B[rng.choice(len(B), min(ncentres, len(B)), replace=False)]
    X = design(B, centres)
    W = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ D)
    return {"scores": results, "centres": centres, "weights": W, "eps": eps}


def correction_lut(model, size=33):
    """Bake the fitted delta into a cube, so it can be applied like any LUT.

    The identity is added back here, so what comes out is a normal 3D LUT and
    the delta never has to be carried around separately.
    """
    g = np.linspace(0, 1, size, dtype=np.float64)
    lat = np.stack(np.meshgrid(g, g, g, indexing="ij"), axis=-1).reshape(-1, 3)
    X = np.hstack([_rbf(lat, model["centres"], model["eps"]), _linear(lat)])
    return np.clip(lat + X @ model["weights"], 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------- applying


def apply_fix(path, out, frames, model, x0, x1, seam=None, crf=14):
    """Rewrite `frames` with the correction, inside columns [x0, x1) only.

    Everything outside the band comes out byte identical: the correction is
    applied to a cropped copy and pasted back. The paste boundary should sit
    inside a flat gutter, which `cgpanel.gutter` finds, not at the nominal panel
    edge and not where the change happens to stop.
    """
    info = P.probe(path)
    W, H = info["width"], info["height"]
    x0, x1 = P.snap_band(x0, x1, W)
    if seam is not None and not (x0 <= seam <= x1):
        raise ValueError(f"seam {seam} is not inside the band {x0},{x1}")

    lut = correction_lut(model, 33)
    size = 33
    fb = W * H * 3
    lo, hi = int(frames[0]), int(frames[1])

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    n = len(raw) // fb
    if n != info["frames"]:
        raise RuntimeError(f"decoded {n} frames, the file says {info['frames']}")

    import cgcore as C
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(info["fps"]), "-i", "-",
         "-i", path, "-map", "0:v", "-map", "1:a?", "-c:a", "copy",
         "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
         "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    touched = 0
    for i in range(n):
        f = np.frombuffer(raw[i * fb:(i + 1) * fb], np.uint8).reshape(H, W, 3)
        if lo <= i <= hi:
            f = f.copy()
            band = f[:, x0:x1].astype(np.float32) / 255.0
            fixed = C.apply_lut_trilinear(band.reshape(-1, 3), lut, size)
            f[:, x0:x1] = np.round(np.clip(fixed, 0, 1) * 255).astype(np.uint8).reshape(
                H, x1 - x0, 3)
            touched += 1
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("encode failed")
    return touched


# ---------------------------------------------------------------- cli


def _pair(s):
    return tuple(int(v) for v in s.split(","))


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("iscut", help="is the step at this frame a cut or a grade change")
    p.add_argument("video")
    p.add_argument("--at", type=int, required=True)
    p.add_argument("--band", default=None)

    p = sub.add_parser("extent", help="where in x the change actually reaches")
    p.add_argument("video")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)

    p = sub.add_parser("model", help="how big, and what can express it")
    p.add_argument("video")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--band", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("apply", help="write the corrected file")
    p.add_argument("video")
    p.add_argument("--out", required=True)
    p.add_argument("--frames", required=True, help="first,last to correct")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--band", required=True)
    p.add_argument("--seam", type=int, default=None)

    args = ap.parse_args(argv)
    band = _pair(args.band) if getattr(args, "band", None) else (None, None)

    if args.cmd == "iscut":
        rows = edge_corr(args.video, args.at, band[0], band[1])
        print(f"gradient map correlation across each frame near {args.at}"
              + (f", columns {band[0]}..{band[1]}" if band[0] is not None else ""))
        for n, c in rows:
            print(f"   f{n:5d}   {c:+.4f}" + ("   <- the step" if n == args.at else ""))
        here = dict(rows).get(args.at)
        others = [c for n, c in rows if n != args.at]
        if here is None:
            return 1
        print()
        if here > 0.5 * float(np.median(others)):
            print(f"  {here:+.4f} against a neighbourhood median of "
                  f"{np.median(others):+.4f}: the picture runs straight through.")
            print("  This is a COLOUR CHANGE, not a cut. A real cut reads near zero.")
        else:
            print(f"  {here:+.4f} against a neighbourhood median of "
                  f"{np.median(others):+.4f}: the picture is discontinuous.")
            print("  This is a CUT. Treat it as a shot boundary, not as a fault.")
        return 0

    if args.cmd == "extent":
        prof = extent(args.video, _pair(args.before), _pair(args.after))
        W = len(prof)
        sm = np.convolve(np.nan_to_num(prof), np.ones(9) / 9, mode="same")
        peak = float(np.nanmax(sm))
        print("mean colour change on static pixels, by column band (levels of 255)")
        step = max(1, W // 16)
        for a in range(0, W, step):
            v = prof[a:a + step]
            v = v[~np.isnan(v)]
            print(f"   x {a:5d}-{min(a + step, W) - 1:<5d}   "
                  f"{(v.mean() if len(v) else float('nan')):6.2f}")
        thresh = 0.25 * peak
        inside = np.where(sm > thresh)[0]
        if len(inside):
            print(f"\n  the change is above a quarter of its peak from x={inside[0]} "
                  f"to x={inside[-1]}")
            print("  Mask to THAT, not to the panel edge. On the piece this was "
                  "built from the change covered the divider bar as well.")
        return 0

    if args.cmd == "model":
        A, B, kept, total = static_cells(args.video, _pair(args.before),
                                         _pair(args.after), band[0], band[1])
        raw = float(np.abs(A - B).mean())
        m = fit_delta(A, B)
        if args.json:
            print(json.dumps({"cells": kept, "of": total, "raw_levels": raw,
                              "scores": m["scores"]}, indent=1))
            return 0
        print(f"{kept} static cells of {total}, raw difference {raw:.2f} levels of 255\n")
        print(f"{'model':22s} {'train':>7s} {'HELD OUT':>9s} {'worst':>7s}")
        print("-" * 50)
        for name, s in m["scores"].items():
            print(f"{name:22s} {s['train']:7.2f} {s['held_out']:9.2f} {s['worst']:7.2f}")
        best = min((s["held_out"], n) for n, s in m["scores"].items() if n != "do nothing")
        floor = m["scores"]["do nothing"]["held_out"]
        print(f"\n  {best[1]} leaves {best[0]:.2f} levels of the {floor:.2f} that were "
              f"there.")
        print(f"  {max(0.0, best[0]):.2f} levels is the part that is NOT a function of "
              f"colour, so no correction can remove it. Say so rather than implying "
              f"the fix is total.")
        return 0

    if args.cmd == "apply":
        A, B, kept, _ = static_cells(args.video, _pair(args.before), _pair(args.after),
                                     band[0], band[1])
        m = fit_delta(A, B)
        n = apply_fix(args.video, args.out, _pair(args.frames), m,
                      band[0], band[1], seam=args.seam)
        print(f"corrected {n} frames inside columns {band[0]}..{band[1]}, "
              f"fitted on {kept} static cells")
        print(f"wrote {args.out}. Everything outside the band is untouched; verify "
              f"that with a frame by frame difference before shipping.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
