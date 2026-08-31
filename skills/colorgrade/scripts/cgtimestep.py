#!/usr/bin/env python3
"""Measure the phase RIFE actually DELIVERS on THIS shot, before trusting a curve.

`cgrife.TIMESTEP_CURVE` is a DEFAULT measured on one shot of one film, not a
constant of the network, and correcting with it on a shot that does not share it
is worse than not correcting at all. Two real shots measured 31 Aug 2026, RIFE
4.25, both 24 fps, agree at the ends and part company in the upper middle:

    asked                  0.10   0.25   0.50   0.75   0.90
    the default's shot    0.003  0.211  0.520  0.832  1.000
    a second real shot    0.007  0.205  0.480  0.755  0.999
    a synthetic pattern   0.000  0.177  0.454  0.748  1.000

The flat ends are the network's timestep conditioning and hold; the middle is
the shot's own and does not. Correcting the second shot with the first's curve
rescues its ends (error at wanted phase 0.10 falls from 0.093 of a gap to 0.004)
and spoils its middle (at 0.75 it rises from 0.005 to 0.059). Which of those
matters depends on the phases the job actually asks for, which is why this is a
measurement and not an argument.

    cgtimestep.py SHOT.mov --pairs 4,5 18,19 30,31
    cgtimestep.py frames_dir/

It prints the curve to paste in as `timestep_curve=`, and it prints what the
three available choices would COST on this shot: no correction, the built in
default, and the shot's own measurement. Choose on those numbers.

Delivered phase is measured and never assumed: dense optical flow from the first
frame of the pair to the picture that was built, projected onto the flow across
the whole gap, over the pixels that actually move.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ASK = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _flow(a, b, w=960):
    h = max(64, int(round(a.shape[0] * w / a.shape[1])) // 2 * 2)
    ga = cv2.cvtColor(cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA),
                      cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(cv2.resize(b, (w, h), interpolation=cv2.INTER_AREA),
                      cv2.COLOR_BGR2GRAY)
    d = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    return d.calc(ga, gb, None)


def _phase(f_ref, f_built):
    """How far along the gap the built picture actually travelled.

    Projected onto the reference flow rather than compared in magnitude, so a
    frame that moved the right distance in the wrong direction cannot pass, and
    taken over the moving pixels only, because a locked off background carries
    no information about phase at all.
    """
    mag = np.linalg.norm(f_ref, axis=2)
    m = mag > np.percentile(mag, 92)
    proj = ((f_built[..., 0] * f_ref[..., 0] + f_built[..., 1] * f_ref[..., 1])
            / (mag ** 2 + 1e-6))
    return float(np.median(proj[m])), float(np.median(mag[m]))


def between(i0, i1, t):
    """One picture between two, through the SAME path a rebuild uses.

    `correct_timestep=False` on purpose: this measures what the network does
    with the number it is handed, which is the thing a curve is a model of.
    """
    import torch

    import cgrife
    dev = cgrife.device()
    i0, i1 = i0.to(dev), i1.to(dev)
    flow, mask = cgrife.flow_mask(i0, i1, t, correct_timestep=False)
    H, W = i0.shape[-2:]
    F = torch.nn.functional
    out = []
    for c in range(3):
        g0 = cgrife._warp(i0[:, c:c + 1], flow[:, :2], W, H)
        g1 = cgrife._warp(i1[:, c:c + 1], flow[:, 2:4], W, H)
        m = F.interpolate(mask, g0.shape[-2:], mode="bilinear",
                          align_corners=False)
        out.append((g0 * m + g1 * (1 - m))[0, 0].clamp(0, 1))
    return torch.stack(out)


def _read(path, want):
    """Frames as BGR uint8, from a directory of stills or straight from a file."""
    if os.path.isdir(path):
        fs = sorted(glob.glob(os.path.join(path, "*.png"))
                    + glob.glob(os.path.join(path, "*.jpg"))
                    + glob.glob(os.path.join(path, "*.tif")))
        return {i: cv2.imread(fs[i]) for i in want if i < len(fs)}, len(fs)
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    got, i = {}, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i in want:
            got[i] = fr
        i += 1
        if want and i > max(want):
            break
    cap.release()
    return got, (n or i)


def measure(frames, pairs, ask, quiet=False):
    import torch
    rows = {t: [] for t in ask}
    for a, b in pairs:
        ia, ib = frames[a], frames[b]
        fref = _flow(ia, ib)
        ta, tb = (torch.from_numpy(x[:, :, ::-1].copy()).permute(2, 0, 1)[None]
                  .float() / 255.0 for x in (ia, ib))
        for t in ask:
            y = between(ta, tb, float(t))
            arr = (y.permute(1, 2, 0).cpu().numpy() * 255.0).round() \
                .clip(0, 255).astype(np.uint8)[:, :, ::-1]
            p, gap = _phase(fref, _flow(ia, arr))
            rows[t].append(p)
            if not quiet:
                print(f"  pair {a}->{b}  asked {t:.2f}  delivered {p:.3f}"
                      f"   (gap {gap:.2f} px)", flush=True)
    return [(float(t), float(np.median(rows[t]))) for t in ask]


def cost(curve, model):
    """Worst error over the measured phases if `model` is used to invert.

    `model` None means no correction at all: ask for the phase and take what
    comes. Anything else is a curve, and it is inverted the way cgrife does it.
    """
    import cgrife
    asked = np.array([a for a, _ in curve])
    got = np.array([g for _, g in curve])

    def delivered(x):
        return float(np.interp(x, asked, got))

    worst, where = 0.0, 0.0
    for want, _ in curve:
        ask = want if model is None else cgrife.solve_timestep(want, model)
        e = abs(delivered(ask) - want)
        if e > worst:
            worst, where = e, want
    return worst, where


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shot", help="a video file, a directory of frames, or a glob")
    ap.add_argument("--pairs", default=None,
                    help="adjacent frame pairs, e.g. '4,5 18,19 30,31'. Three "
                         "well separated pairs is the useful minimum: one pair "
                         "measures that pair and not the shot.")
    ap.add_argument("--ask", default=",".join(f"{t:g}" for t in ASK))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    ask = [float(x) for x in a.ask.split(",")]
    if a.pairs:
        pairs = [tuple(int(x) for x in p.split(",")) for p in a.pairs.split()]
        frames, n = _read(a.shot, {i for p in pairs for i in p})
    else:
        _, n = _read(a.shot, set())
        if n < 8:
            raise SystemExit(f"{n} frames found, need at least 8")
        q = n // 4
        pairs = [(q, q + 1), (2 * q, 2 * q + 1), (3 * q, 3 * q + 1)]
        frames, _ = _read(a.shot, {i for p in pairs for i in p})
    missing = [i for p in pairs for i in p if frames.get(i) is None]
    if missing:
        raise SystemExit(f"frames {missing} could not be read out of {n}")

    curve = measure(frames, pairs, ask, quiet=a.json)
    import cgrife
    raw_worst, raw_at = cost(curve, None)
    def_worst, def_at = cost(curve, cgrife.TIMESTEP_CURVE)
    own = ((0.0, 0.0),) + tuple(curve) + ((1.0, 1.0),)
    own_worst, own_at = cost(curve, own)

    if a.json:
        import json
        print(json.dumps({"shot": os.path.abspath(a.shot), "pairs": pairs,
                          "curve": curve,
                          "worst_error": {"none": raw_worst,
                                          "built_in_default": def_worst,
                                          "this_shot": own_worst}}, indent=1))
        return 0

    print("\n  asked   delivered   error")
    for t, g in curve:
        print(f"  {t:5.2f} {g:11.3f} {g - t:+8.3f}")
    print("\n  Paste this in as timestep_curve= :\n")
    print("  TIMESTEP_CURVE = (")
    print("      (0.0, 0.000),")
    for t, g in curve:
        print(f"      ({t:.2f}, {g:.3f}),")
    print("      (1.0, 1.000),\n  )")
    print("\n  Worst error over the phases measured, of a whole gap:")
    print(f"    no correction at all          {raw_worst:.3f}  (at asked {raw_at:.2f})")
    print(f"    the built in default curve    {def_worst:.3f}  (at asked {def_at:.2f})")
    print(f"    this shot's own curve         {own_worst:.3f}  (at asked {own_at:.2f})")
    if def_worst > raw_worst:
        print("\n  THE DEFAULT IS WORSE THAN NOTHING ON THIS SHOT. Pass this "
              "shot's own curve, or correct_timestep=False.")
    flat = [t for t, g in curve if g < 0.02 or g > 0.98]
    if flat:
        print(f"\n  Flat at asked {flat}: inverting there is ill conditioned. A "
              "slot whose wanted phase is within 0.02 of a source frame should "
              "be COPIED from that frame rather than built. It is free and exact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
