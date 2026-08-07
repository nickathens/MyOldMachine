#!/usr/bin/env python3
"""Match a new film to films already approved in the same series.

The mistake this exists to stop: **matching the recipe instead of the result.**

Three films in one corporate series were given the same look values and the
third came back as only adequate. Measuring what each grade actually added to
its own footage showed the grades were nearly identical: same lift in colour,
same push on skin, same cool shadows. The films looked different because the
FOOTAGE was different. One arrived colourful and dark, a tungsten interior. The
next arrived pale and neutral, a daylight office. The same recipe, a flatter
result. Matching a series means landing in the same place on the things two
films should share, and re-deriving every magnitude from the new film's own
measurements. Carry the directions, not the numbers.

Two mechanisms under that, both measurable and both caught the hard way.

**The contrast pivot has to sit where the film's pixels are.** The engine pivots
at Cineon mid grey. A film whose own mid sits well below that gets four and a
half times less tonal work out of the identical setting, and reads flat. But the
rule must be RE-DERIVED and not copied: a film with two worlds, a dark half and
a bright half, has its own median sitting in the VALLEY between them where almost
nothing lives. Pivoting there drove the bright half up 17 levels and clipped 6.8
per cent of it. Pivot on the bright world's own mid instead and the bright half
holds while the whole contrast goes into deepening the dark half. `pivot` reports
whether the film is bimodal and refuses to give one answer when it is.

**Read brand colours off flat artwork, never off a mask.** Masks failed three
times running on this material: an HSV hue band "skin" mask selected 46 per cent
of a film and rendered magenta showed wood and walls; a "shirt" mask turned out
to be the whole blurred background. A title card is flat, known artwork, and the
same card usually opens every film in the series, so it is the one honest common
reference between two pieces. `primaries` reads them there.

And the trap under that: a synthetic Lab probe built at a brand's real chroma
can fall OUTSIDE Rec.709. Seven of seven test teals had a negative linear channel
and the clip rotated their hue 6.9 degrees before any look ran, which reported as
18.5 degrees of brand drift that did not exist. Every synthetic probe here
asserts it round trips before it is allowed to measure anything.

    $PY scripts/cgseries.py landing  FILM.mp4
    $PY scripts/cgseries.py pivot    FILM.mp4
    $PY scripts/cgseries.py primaries FILM.mp4 --cards 0,80 977,1127
    $PY scripts/cgseries.py compare  NEW.mp4 APPROVED.mp4
    $PY scripts/cgseries.py mask     FRAME.png --hue 25 --width 22 --out check.png
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cgcore as C
import cgpanel as P


# ---------------------------------------------------------------- sampling


def sample_pixels(path, n=400_000, scale=480, seed=0):
    """A random sample of the film's pixels, in code values.

    Sampled rather than held whole on purpose. The first look sweep built on
    full frames reached 10 GB and drove the machine into swap; rebuilt on a
    sampled population it gave the same numbers with forty times less memory.
    Do this first, not after the machine complains.
    """
    info = P.probe(path)
    arr, sig = P.stream(path, 0, info["frames"] - 1, scale=scale, pix="rgb24")
    flat = arr.reshape(-1, 3)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(flat), min(n, len(flat)), replace=False)
    return flat[idx].astype(np.float32) / 255.0, sig


# ---------------------------------------------------------------- where it lands


def landing(path, n=400_000, scale=480):
    """Where a film LANDS. The numbers two films in a series should share.

    Deliberately not "what the grade does". Two grades can be identical and land
    in completely different places, which is exactly the failure this module
    exists for.
    """
    code, _ = sample_pixels(path, n=n, scale=scale)
    lin = C.code_to_lin(code)
    lg = C.lin_to_log(lin)
    lab = C.lin_to_lab(lin)
    y = C.luma(lin)
    ylog = C.lin_to_log(y)

    chroma = np.hypot(lab[..., 1], lab[..., 2])
    lit = y > np.percentile(y, 60)
    shadow = y < np.percentile(y, 20)

    return {
        "mid_log": float(np.median(ylog)),
        "mid_code": float(np.median(code.mean(-1)) * 255),
        "p05": float(np.percentile(code, 5) * 255),
        "p95": float(np.percentile(code, 95) * 255),
        "clipped_pct": float((code.max(-1) >= 254.5 / 255).mean() * 100),
        "crushed_pct": float((code.max(-1) <= 0.5 / 255).mean() * 100),
        "chroma": float(chroma.mean()),
        "chroma_lit": float(chroma[lit].mean()),
        "shadow_lean": float((lab[..., 2] - lab[..., 1])[shadow].mean()),
        "contrast_log": float(np.percentile(ylog, 90) - np.percentile(ylog, 10)),
    }


def contribution(path, look_name, looks_dir=None, n=200_000, scale=480):
    """What the grade ADDS to this film's own footage, as a delta on `landing`.

    Two films whose contributions match and whose landings do not are the exact
    signature of the recipe-versus-result mistake.
    """
    looks_dir = looks_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "looks")
    code, _ = sample_pixels(path, n=n, scale=scale)
    look = C.load_look(look_name, looks_dir) if isinstance(look_name, str) else look_name
    graded = C.apply_grade(code, C.Grade(look=look))

    def stats(c):
        lin = C.code_to_lin(c)
        lab = C.lin_to_lab(lin)
        y = C.luma(lin)
        return {"mid_log": float(np.median(C.lin_to_log(y))),
                "chroma": float(np.hypot(lab[..., 1], lab[..., 2]).mean()),
                "contrast_log": float(np.percentile(C.lin_to_log(y), 90)
                                      - np.percentile(C.lin_to_log(y), 10)),
                "shadow_lean": float((lab[..., 2] - lab[..., 1])[
                    y < np.percentile(y, 20)].mean())}

    a, b = stats(code), stats(graded)
    return {k: b[k] - a[k] for k in a}


# ---------------------------------------------------------------- the pivot


def frame_mids(path, scale=240):
    """Each frame's own mid grey, in log. One decode."""
    info = P.probe(path)
    arr, _ = P.stream(path, 0, info["frames"] - 1, scale=scale)
    y = arr.astype(np.float32) / 255.0
    lin = C.code_to_lin(y)
    return np.median(C.lin_to_log(lin).reshape(len(arr), -1), axis=1)


def pivot(path, n=400_000, scale=480, bins=64, valley_share=0.06):
    """Where to put the contrast pivot, and whether one number will even do.

    Returns the film's own mid, the modes of its tonal distribution, and a
    verdict. When a film is bimodal its median is a VALLEY, and pivoting in a
    valley spends the contrast pushing the bright world into the ceiling instead
    of shaping either world.
    """
    code, _ = sample_pixels(path, n=n, scale=scale)
    ylog = C.lin_to_log(C.luma(C.code_to_lin(code)))
    hist, edges = np.histogram(ylog, bins=bins, range=(0.0, 1.0))
    centres = (edges[:-1] + edges[1:]) / 2
    med = float(np.median(ylog))

    # Peaks, then merged into lobes. Two adjacent bins on the same hump are not
    # two worlds, and the peak of a lobe is not where that world sits anyway:
    # a bright world with a long tail downward peaks well above its own middle.
    # So find the lobes, split the pixels at the valley between the two biggest,
    # and take each world's MEDIAN. Recommending a peak instead put the pivot
    # 0.07 log above where the film wanted it.
    peaks = [i for i in range(1, bins - 1)
             if hist[i] > hist[i - 1] and hist[i] >= hist[i + 1]
             and hist[i] > 0.05 * hist.max()]
    peaks.sort(key=lambda i: -hist[i])
    lobes = []
    for i in peaks:
        if all(abs(centres[i] - centres[j]) > 0.15 for j in lobes):
            lobes.append(i)
    at_median = float(hist[np.argmin(np.abs(centres - med))] / max(hist.sum(), 1))
    bimodal = len(lobes) >= 2 and at_median < valley_share

    out = {"mid_log": med, "peaks": [float(centres[i]) for i in peaks[:4]],
           "share_at_median": at_median,
           "engine_default": float(C.Look().pivot), "bimodal": bool(bimodal)}
    if bimodal:
        lo_i, hi_i = sorted(lobes[:2], key=lambda i: centres[i])
        valley = float(centres[lo_i + int(np.argmin(hist[lo_i:hi_i + 1]))])
        out["valley"] = valley

        # Split the film into worlds by FRAME, not by pixel value. Splitting
        # pixels puts every bright pixel of the dark world into the bright
        # bucket and drags its middle up; a world is a stretch of the film, not
        # a range of values. Each world's mid is then measured on its own frames.
        mids = frame_mids(path)
        dark_f = np.where(mids < valley)[0]
        bright_f = np.where(mids >= valley)[0]
        code_all, _ = P.stream(path, 0, len(mids) - 1, scale=160, pix="rgb24")
        lg_all = C.lin_to_log(C.luma(C.code_to_lin(code_all.astype(np.float32) / 255.0)))
        out["worlds"] = {
            "dark": {"mid": float(np.median(lg_all[dark_f])) if len(dark_f) else None,
                     "frames": int(len(dark_f))},
            "bright": {"mid": float(np.median(lg_all[bright_f])) if len(bright_f) else None,
                       "frames": int(len(bright_f))},
        }
        out["recommend"] = (out["worlds"]["bright"]["mid"]
                            if out["worlds"]["bright"]["mid"] is not None else med)
        out["why"] = ("the film has two worlds and its own median sits in the "
                      "valley between them. Pivot on the world you must not "
                      "move, usually the bright one, and the contrast goes into "
                      "the other. Pivoting in the valley pushes the bright world "
                      "into the ceiling and shapes neither.")
    else:
        out["recommend"] = med
        out["why"] = ("one population, so pivot on the film's own mid. The "
                      "engine default does less work the further the film's mid "
                      "sits from it.")
    out["moved_from_default"] = float(abs(out["recommend"] - out["engine_default"]))
    return out


# ---------------------------------------------------------------- brand colour


def _assert_in_gamut(lin, what):
    """A synthetic probe must survive the round trip before it measures anything."""
    code = C.lin_to_code(np.maximum(lin, 0.0))
    if np.any(lin < -1e-6) or np.any(code > 1.0 + 1e-6):
        raise ValueError(
            f"{what} falls outside Rec.709 and will be clipped before any grade "
            f"runs. A probe like that reports drift that does not exist. Read the "
            f"colour off the film's own artwork instead of synthesising it.")
    back = C.code_to_lin(np.clip(code, 0, 1))
    err = float(np.abs(back - lin).max())
    if err > 2e-3:
        raise ValueError(f"{what} does not round trip: {err:.2e}")


def primaries(path, cards, min_area=0.0008, tol=6.0, look_at=400):
    """Brand hue angles read off flat artwork, in BOTH conventions.

    `cards` is a list of (first, last) frame ranges holding title cards. Flat
    artwork means large runs of nearly identical pixels, so the colours are
    found by clustering on quantised values and keeping clusters that cover a
    real share of the frame.

    Both the HSV hue and the Lab hue angle are reported for every colour, because
    the engine gates in one and rotates in the other, and a centre carried into
    the wrong one silently does nothing.
    """
    out = []
    for a, b in cards:
        # FULL resolution, a few frames from the middle of the card. Reading a
        # card at a working scale mixes a small brand mark with the background
        # it sits on and rotates its hue toward that background, which is the
        # same class of error as measuring a colour through a bad mask.
        mid = (a + b) // 2
        lo, hi = max(a, mid - 2), min(b, mid + 1)
        arr, _ = P.stream(path, lo, hi, pix="rgb24")
        px = arr.reshape(-1, 3).astype(np.float32) / 255.0
        q = np.round(px * 32).astype(np.int32)
        keys, counts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
        # Look deep into the list, not at the top few. A card is mostly its
        # background, so the brand mark itself can sit a couple of hundred
        # clusters down and still be the only thing on the card that matters.
        order = np.argsort(-counts)
        for i in order[:look_at]:
            share = counts[i] / len(px)
            if share < min_area:
                break
            sel = (q == keys[i]).all(-1)
            mean = px[sel].mean(0)[None, None, :]
            lin = C.code_to_lin(mean)
            _assert_in_gamut(lin, f"card colour at frames {a}-{b}")
            h, s, _ = C.rgb_to_hsv(lin)
            lab = C.lin_to_lab(lin)[0, 0]
            # Gate on Lab chroma, not on HSV saturation. A near white with a
            # faint cast passes an HSV saturation test and is not a brand colour;
            # chroma says how far it actually is from grey.
            if float(np.hypot(lab[1], lab[2])) < 10.0:
                continue
            out.append({"card": [a, b], "share": float(share),
                        "rgb": [round(float(v) * 255, 1) for v in mean[0, 0]],
                        "hsv_hue": float(h[0, 0]),
                        "lab_hue": float(C.lab_hue(lin)[0, 0]),
                        "chroma": float(np.hypot(*C.lin_to_lab(lin)[0, 0, 1:]))})
    # merge colours that are the same to within tol degrees of Lab hue
    merged = []
    for c in sorted(out, key=lambda d: -d["share"]):
        if not any(abs(((c["lab_hue"] - m["lab_hue"] + 180) % 360) - 180) < tol
                   for m in merged):
            merged.append(c)
    return merged


def brand_drift(path_a, path_b, cards_a, cards_b, tol=6.0):
    """How far the same artwork sits apart between two films.

    The only honest common reference between two films is a piece of artwork
    they both carry. Everything else differs because the footage differs.
    """
    A, B = primaries(path_a, cards_a), primaries(path_b, cards_b)
    rows = []
    for a in A:
        near = min(B, key=lambda b: abs(((a["lab_hue"] - b["lab_hue"] + 180) % 360) - 180))
        d = abs(((a["lab_hue"] - near["lab_hue"] + 180) % 360) - 180)
        if d < 25.0:
            rows.append({"rgb_a": a["rgb"], "rgb_b": near["rgb"],
                         "lab_hue_a": a["lab_hue"], "lab_hue_b": near["lab_hue"],
                         "apart_deg": d,
                         "chroma_apart": a["chroma"] - near["chroma"]})
    return rows


# ---------------------------------------------------------------- masks


def mask_preview(frame_png, out_png, hue=None, width=22.0, space="hsv", lab_order=False):
    """Render a mask in magenta over the frame, and LOOK at it.

    This is not a nicety. Rendering the mask settled five separate disputes on
    one series, every time by showing that a mask everyone believed selected one
    thing selected something else: a "skin" hue band that took 46 per cent of the
    frame including wood and walls, and a "shirt" mask that was the entire blurred
    background. Do this before any claim that rests on a mask.

    `lab_order` adds the r > g > b ordering test, which is what an actual skin
    mask needs; a hue band alone is a mask for "warm".
    """
    from PIL import Image
    img = np.asarray(Image.open(frame_png).convert("RGB"), np.float32) / 255.0
    lin = C.code_to_lin(img)
    h, s, _ = C.rgb_to_hsv(lin)
    gate = C.lab_hue(lin) if space == "lab" else h
    m = C.hue_weight(gate, float(hue), float(width)) if hue is not None else np.ones_like(h)
    if lab_order:
        r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
        m = m * ((r > g) & (g > b)).astype(np.float32)
    m = m * (s > 0.10)
    tint = np.stack([np.ones_like(m), np.zeros_like(m), np.ones_like(m)], -1)
    shown = img * (1 - m[..., None] * 0.75) + tint * (m[..., None] * 0.75)
    Image.fromarray(np.clip(shown * 255, 0, 255).astype(np.uint8)).save(out_png)
    return float(m.mean())


# ---------------------------------------------------------------- cli


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("landing", help="where a film lands")
    p.add_argument("video")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pivot", help="where to put the contrast pivot")
    p.add_argument("video")

    p = sub.add_parser("primaries", help="brand colours read off title cards")
    p.add_argument("video")
    p.add_argument("--cards", nargs="+", required=True, help="first,last ranges")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("compare", help="a new film against an approved one")
    p.add_argument("new")
    p.add_argument("approved")

    p = sub.add_parser("mask", help="render a mask in magenta and look at it")
    p.add_argument("frame")
    p.add_argument("--out", required=True)
    p.add_argument("--hue", type=float, default=None)
    p.add_argument("--width", type=float, default=22.0)
    p.add_argument("--space", choices=["hsv", "lab"], default="hsv")
    p.add_argument("--skin", action="store_true", help="add the r>g>b ordering test")

    args = ap.parse_args(argv)

    if args.cmd == "landing":
        d = landing(args.video)
        if args.json:
            print(json.dumps(d, indent=1))
            return 0
        print(f"{args.video}")
        for k, v in d.items():
            print(f"  {k:16s} {v:9.3f}")
        return 0

    if args.cmd == "pivot":
        d = pivot(args.video)
        print(f"{args.video}")
        print(f"  the film's own mid      log {d['mid_log']:.3f}")
        print(f"  engine default pivot    log {d['engine_default']:.3f}")
        print(f"  tonal peaks             " + ", ".join(f"{m:.3f}" for m in d["peaks"]))
        print(f"  pixels near the median  {d['share_at_median'] * 100:.1f}%")
        print()
        if d["bimodal"]:
            w = d["worlds"]
            print(f"  BIMODAL, valley at log {d['valley']:.3f}")
            print(f"    dark world   mid log {w['dark']['mid']:.3f}  "
                  f"({w['dark']['frames']} frames)")
            print(f"    bright world mid log {w['bright']['mid']:.3f}  "
                  f"({w['bright']['frames']} frames)")
            print()
        print(f"  pivot at {d['recommend']:.3f}")
        print(f"  {d['why']}")
        print("\n  A full frame title card is not the film. If the piece opens or "
              "closes on one, cut it out of the file before measuring or the "
              "card's own level drags the answer.")
        return 0

    if args.cmd == "primaries":
        cards = [tuple(int(v) for v in c.split(",")) for c in args.cards]
        rows = primaries(args.video, cards)
        if args.json:
            print(json.dumps(rows, indent=1))
            return 0
        print(f"{len(rows)} brand colours on the cards\n")
        print(f"{'rgb':>20s} {'share':>7s} {'HSV hue':>9s} {'Lab hue':>9s} {'chroma':>7s}")
        print("-" * 58)
        for r in rows:
            print(f"{str(r['rgb']):>20s} {r['share'] * 100:6.2f}% "
                  f"{r['hsv_hue']:9.2f} {r['lab_hue']:9.2f} {r['chroma']:7.2f}")
        print("\nThese are CANDIDATES. Antialiased edges between a mark and its "
              "background land here too, so pick by share and chroma and check "
              "the rgb against the brand sheet.")
        print("Gate hue_shifts in the convention you read the centre in. The two "
              "hue columns above are the same colour and they are not the same "
              "number.")
        return 0

    if args.cmd == "compare":
        a, b = landing(args.new), landing(args.approved)
        print(f"{'measure':16s} {'new':>10s} {'approved':>10s} {'apart':>9s}")
        print("-" * 50)
        for k in a:
            print(f"{k:16s} {a[k]:10.3f} {b[k]:10.3f} {a[k] - b[k]:+9.3f}")
        print("\nThese are LANDINGS. Close them by re-deriving the new film's own "
              "numbers, not by copying the approved film's look values. Skin and "
              "exposure differences that come from the lighting are not faults to "
              "close: a tungsten interior and a daylight office should not match on "
              "brightness.")
        return 0

    if args.cmd == "mask":
        frac = mask_preview(args.frame, args.out, hue=args.hue, width=args.width,
                            space=args.space, lab_order=args.skin)
        print(f"the mask covers {frac * 100:.1f}% of the frame. Wrote {args.out}.")
        print("LOOK AT IT before believing any number that rests on it. A hue band "
              "alone is a mask for 'warm', not for skin.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
