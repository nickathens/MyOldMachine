#!/usr/bin/env python3
"""Split screen support: find the panels, then measure and cut each one alone.

Why this exists. A film that is two pictures side by side breaks every whole
frame instrument in this skill, and it breaks them quietly. On the piece that
produced this module, whole frame cut detection found 17 cuts and per panel
found 23; whole frame stall detection found 14 frozen frames and per panel
found 54. The real fault was that the right hand panel ran at 18.0 to 19.8
unique pictures a second for five seconds while the left panel beside it ran at
23.5. Averaged across the whole frame that is invisible, because half the frame
is always moving normally.

So the rule is blunt: **on a split screen film, every measurement and every
repair is per panel.** This module finds the panels and hands out the column
bands. Nothing else in the skill should be hardcoding a split column.

Two more rules are enforced here rather than left to the caller.

**One decode path.** Two arrays that came out of different ffmpeg invocations,
or the same one at different scales, cannot be compared. Splicing a numpy box
downscale into ffmpeg's scaled gray once invented an 11 code level step at every
repaired frame and reported a repair as making three shots 48 per cent rougher.
Every read here goes through `stream`, which returns the exact argument list
that produced it, and `require_same_path` refuses a comparison across two.

**Measure the seam, do not assume it.** The nominal split column and the column
a shot is actually flat at are not the same number, and the change a compositor
makes to one panel does not necessarily stop at the panel edge. `gutter` finds
the flattest run per shot, and it takes absolute frame numbers, so it asserts
its own answer rather than returning an argmin over an empty slice.

    $PY scripts/cgpanel.py panels IN.mp4
    $PY scripts/cgpanel.py gutter IN.mp4 --range 361,411
    $PY scripts/cgpanel.py cuts   IN.mp4 --band 0,1936
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import numpy as np


# ---------------------------------------------------------------- decoding


def probe(path):
    """Width, height, frame count and frame rate, from the file itself."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate",
         "-of", "json", path], capture_output=True, check=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    num, den = (s["r_frame_rate"].split("/") + ["1"])[:2]
    return {"width": int(s["width"]), "height": int(s["height"]),
            "frames": int(s["nb_read_frames"]), "fps": float(num) / float(den)}


def snap_band(x0, x1, W):
    """Crop offsets and widths must be even on subsampled chroma.

    ffmpeg does not refuse an odd crop, it silently rounds the width down, and
    the caller then reshapes the buffer with the width it asked for and gets a
    frame count that is off by a fraction. That is how an 81 column band turned
    into 97.8 frames. Snap here, once, and tell the caller what it really got.
    """
    x0 = max(0, int(x0) & ~1)
    x1 = min(W, int(x1))
    if (x1 - x0) % 2:
        x1 = x1 - 1 if x1 - 1 > x0 else min(W, x1 + 1)
    if x1 - x0 < 2:
        raise ValueError(f"band {x0},{x1} is narrower than two columns")
    return x0, x1


def stream(path, a, b, x0=None, x1=None, scale=None, pix="gray"):
    """Frames a..b inclusive, cropped to columns [x0, x1), as one array.

    Returns (array, sig). `sig` is the filter chain and pixel format that
    produced the array. Pass both sigs to `require_same_path` before comparing
    two arrays, always, including when it is obvious they match.
    """
    info = probe(path)
    W, H = info["width"], info["height"]
    x0 = 0 if x0 is None else int(x0)
    x1 = W if x1 is None else int(x1)
    if not (0 <= x0 < x1 <= W):
        raise ValueError(f"band {x0},{x1} outside a {W} pixel wide frame")
    x0, x1 = snap_band(x0, x1, W)

    # `after` is everything that shapes the pixels. The select filter is kept
    # apart from it deliberately: the signature must describe HOW the pixels
    # were made, not which frames were asked for, so two ranges of the same band
    # at the same scale stay comparable. Splitting the joined chain on its first
    # comma does not work, because select's own expression contains commas.
    after = []
    if (x0, x1) != (0, W):
        after.append(f"crop={x1 - x0}:{H}:{x0}:0")
    w, h = x1 - x0, H
    if scale:
        w = int(scale)
        h = max(2, int(round(H * w / (x1 - x0))) // 2 * 2)
        after.append(f"scale={w}:{h}:flags=bicubic")
    after.append(f"format={pix}")
    chain = ",".join([f"select='between(n\\,{int(a)}\\,{int(b)})'"] + after)

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", chain, "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", pix, "-"],
        capture_output=True, check=True).stdout

    per = w * h * (3 if pix == "rgb24" else 1)
    n = len(raw) // per
    if n != int(b) - int(a) + 1:
        raise RuntimeError(f"decoded {n} frames, asked for {int(b) - int(a) + 1}")
    shape = (n, h, w, 3) if pix == "rgb24" else (n, h, w)
    arr = np.frombuffer(raw, np.uint8).reshape(shape)
    return arr, f"{path}|{','.join(after)}|{pix}"


def require_same_path(*sigs):
    """Refuse to compare arrays that did not come out of the same decode."""
    if len(set(sigs)) > 1:
        raise ValueError(
            "these arrays came from different decode paths and cannot be "
            "compared:\n  " + "\n  ".join(sorted(set(sigs))))


# ---------------------------------------------------------------- panels


def flat_runs(grad, tol=1.2, min_len=8, margin=0.02):
    """Runs of columns whose left to right gradient stays under `tol` levels."""
    W = len(grad)
    lo, hi = int(W * margin), int(W * (1 - margin))
    flat = (grad < tol)
    runs, start = [], None
    for x in range(lo, hi):
        if flat[x] and start is None:
            start = x
        elif not flat[x] and start is not None:
            if x - start >= min_len:
                runs.append((start, x - 1))
            start = None
    if start is not None and hi - start >= min_len:
        runs.append((start, hi - 1))
    return runs


_MOTION_CACHE = {}


def motion_image(path, width=960, rows=64):
    """|frame[t] - frame[t-1]| averaged down each column: a (T-1, width) array.

    ONE decode for the whole film, deliberately squashed vertically because
    nothing here needs vertical detail and 64 rows makes a 4K feature fit in
    memory. Everything else in this module reads from this array rather than
    decoding again, which is both the speed and the one-decode-path rule.
    """
    key = (path, width, rows)
    if key in _MOTION_CACHE:
        return _MOTION_CACHE[key]
    info = probe(path)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"scale={width}:{rows}:flags=area,format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    n = len(raw) // (width * rows)
    arr = np.frombuffer(raw, np.uint8).reshape(n, rows, width).astype(np.float32)
    m = np.abs(np.diff(arr, axis=0)).mean(axis=1)     # (T-1, width)
    _MOTION_CACHE[key] = (m, info)
    return m, info


def find_panels(path, width=960, still=0.12, min_frames=None):
    """Candidate vertical dividers, each with the evidence for and against.

    A flat column band is necessary and nowhere near sufficient: a wall, a sky,
    a letterbox bar or the temporal average of a film that changes worlds all
    give one. The first version of this looked for flatness in the averaged
    picture and nominated a 1755 column wide stretch of office wall.

    What actually distinguishes a divider is that it **holds still while the
    frame around it moves**, for a long unbroken stretch of frames. So the test
    is relative: per frame, per column, motion as a fraction of that frame's own
    motion. A card where the whole frame is frozen scores zero everywhere and is
    excluded by construction, because the ratio is taken against the frame.

    Returns the column, the frame range it is present over, and how many frames
    of evidence there are, strongest first.
    """
    m, info = motion_image(path, width=width)
    W = info["width"]
    T = m.shape[0]
    min_frames = max(24, T // 8) if min_frames is None else int(min_frames)

    per_frame = m.mean(axis=1, keepdims=True)
    moving = (per_frame[:, 0] > 0.15)                 # frames where anything happens
    rel = m / np.maximum(per_frame, 1e-3)
    quiet = (rel < still) & moving[:, None]

    scale = W / width
    out = []
    for xs in range(2, width - 2):
        col = quiet[:, xs]
        if not col.any():
            continue
        idx = np.where(col)[0]
        runs = np.split(idx, np.where(np.diff(idx) > 3)[0] + 1)
        best = max(runs, key=len)
        if len(best) < min_frames:
            continue
        centre_frac = xs / width
        if centre_frac < 0.15 or centre_frac > 0.85:
            continue                                   # a letterbox bar, not a divider
        out.append({"x_small": xs, "split_x": int(round(xs * scale)),
                    "frames": [int(best[0]), int(best[-1]) + 1],
                    "evidence": int(len(best)),
                    "still_ratio": float(rel[best, xs].mean())})
    if not out:
        return []

    # merge adjacent columns into one candidate
    out.sort(key=lambda d: d["x_small"])
    merged, cur = [], [out[0]]
    for d in out[1:]:
        if d["x_small"] - cur[-1]["x_small"] <= 2:
            cur.append(d)
        else:
            merged.append(cur)
            cur = [d]
    merged.append(cur)

    res = []
    for grp in merged:
        pick = max(grp, key=lambda d: d["evidence"])
        xs0, xs1 = grp[0]["x_small"], grp[-1]["x_small"]
        near = int(round((xs0 + xs1) / 2 * scale))
        search = int((xs1 - xs0 + 2) / 2 * scale) + 24

        # The divider is present over one span, but it is not quiet for every
        # frame of it: content crosses the seam, a graphic spans the full width,
        # a title fades over it. Two things follow. Pool the evidence across the
        # whole divider band rather than trusting its best single column, since
        # what crosses the seam rarely crosses all of it: on the piece this was
        # built from, one column gave 44 per cent coverage of an 800 frame split
        # screen and the band gave 74. And take the span between the first and
        # last run worth believing rather than the longest unbroken one, which
        # on its own reported 283 frames of those 800.
        col = quiet[:, xs0:xs1 + 1].any(axis=1)
        idx = np.where(col)[0]
        runs = [r for r in np.split(idx, np.where(np.diff(idx) > 3)[0] + 1) if len(r)]
        longest = max(len(r) for r in runs)
        keep = [r for r in runs if len(r) >= max(24, 0.2 * longest)]
        lo, hi = int(keep[0][0]), int(keep[-1][-1]) + 1
        covered = sum(len(r) for r in keep) / max(1, hi - lo)

        mid = lo + (hi - lo) // 2
        x, flat = gutter(path, max(lo, mid - 40), min(hi - 1, mid + 40),
                         near=near, search=search)
        res.append({
            "split_x": x,
            "approx_from_scan": near,
            "gutter_gradient": flat,
            "frames": [lo, hi],
            "coverage": float(covered),
            "evidence_frames": int(sum(len(r) for r in keep)),
            "still_ratio": pick["still_ratio"],
            "verdict": ("split screen" if longest >= min_frames
                        else "not enough evidence"),
        })
    res.sort(key=lambda d: -d["evidence_frames"])
    return res


def split_range(path, split_x, width=960, still=0.12):
    """The frame range over which the divider is actually present.

    A split screen usually opens and closes on a full frame card, and grading or
    repairing those frames per panel would put a seam down a title.
    """
    m, info = motion_image(path, width=width)
    xs = int(round(split_x * width / info["width"]))
    xs = min(max(xs, 0), width - 1)
    per_frame = m.mean(axis=1)
    rel = m[:, max(0, xs - 1):xs + 2].mean(axis=1) / np.maximum(per_frame, 1e-3)
    quiet = (rel < still) & (per_frame > 0.15)
    idx = np.where(quiet)[0]
    if not len(idx):
        return None
    runs = np.split(idx, np.where(np.diff(idx) > 3)[0] + 1)
    best = max(runs, key=len)
    return int(best[0]), int(best[-1]) + 1


def gutter(path, a, b, near=None, search=90, tol=1.2):
    """The flattest column to switch panels on, for this shot only.

    Takes ABSOLUTE frame numbers and asserts its own answer. The version of this
    that took a pre sliced array indexed it with absolute numbers anyway, got an
    empty slice, and returned argmin of nothing: a nan and a column of 0, which
    put the panel boundary 84 pixels off and was announced only as a numpy
    RuntimeWarning. Any helper that indexes by absolute frame number must check
    what it produced.
    """
    info = probe(path)
    W = info["width"]
    near = W // 2 if near is None else int(near)
    lo, hi = max(0, near - search), min(W, near + search + 1)
    if hi - lo < 4:
        raise ValueError(f"search window {lo},{hi} is too narrow")

    arr, _ = stream(path, a, b, lo, hi)
    if arr.shape[0] < 1:
        raise RuntimeError(f"no frames decoded for {a}..{b}")
    # The gradient of the AVERAGE is not the average of the gradient, and here
    # the difference decides the answer. A moving texture averages to flat grey
    # over a shot, so measuring the temporal mean nominates the moving picture
    # as a place to switch panels and puts the seam 19 columns into the wrong
    # half. Measure the gradient inside each frame, then average the magnitudes.
    f = arr.astype(np.float32)
    grad = np.abs(np.diff(f, axis=2)).mean(axis=(0, 1))
    if not np.isfinite(grad).all():
        raise RuntimeError("column gradient is not finite, the decode is wrong")

    runs = flat_runs(np.concatenate([grad, grad[-1:]]), tol=tol, min_len=4, margin=0.0)
    if runs:
        r0, r1 = max(runs, key=lambda r: r[1] - r[0])
        x = lo + (r0 + r1) // 2
        flat = float(grad[r0:max(r1, r0 + 1)].max())
    else:
        x = lo + int(np.argmin(grad))
        flat = float(grad.min())
    if not (lo <= x < hi):
        raise RuntimeError(f"gutter {x} landed outside its own search window {lo},{hi}")
    return int(x), flat


# ---------------------------------------------------------------- cuts


def cuts(path, x0=None, x1=None, a=None, b=None, thresh=None, scale=320, min_len=4):
    """Cut frames inside one column band.

    Content based, on a small gray version, one decode. The threshold is derived
    from the band's own distribution rather than fixed, because a graphics panel
    and a camera panel in the same film have completely different noise floors.
    """
    info = probe(path)
    a = 0 if a is None else int(a)
    b = (info["frames"] - 1) if b is None else int(b)
    arr, _ = stream(path, a, b, x0, x1, scale=scale)
    f = arr.astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    if thresh is None:
        med = float(np.median(d))
        mad = float(np.median(np.abs(d - med))) or 1e-3
        thresh = med + 8.0 * 1.4826 * mad
    hits = [a + 1 + i for i, v in enumerate(d) if v > thresh]
    out = []
    for h in hits:
        if not out or h - out[-1] >= min_len:
            out.append(h)
    return out, float(thresh), d


# ---------------------------------------------------------------- cli


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("panels", help="find vertical dividers and judge them")
    p.add_argument("video")
    p.add_argument("--still", type=float, default=0.12,
                   help="a column counts as held when it moves this fraction "
                        "of what its own frame moves")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("gutter", help="flattest switch column for one shot")
    p.add_argument("video")
    p.add_argument("--range", required=True, help="a,b absolute frame numbers")
    p.add_argument("--near", type=int, default=None)

    p = sub.add_parser("cuts", help="cut detection inside one column band")
    p.add_argument("video")
    p.add_argument("--band", default=None, help="x0,x1")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "panels":
        info = probe(args.video)
        found = find_panels(args.video, still=args.still)
        if args.json:
            print(json.dumps({"probe": info, "candidates": found}, indent=1))
            return 0
        print(f"{args.video}\n  {info['width']}x{info['height']}, "
              f"{info['frames']} frames at {info['fps']:.3f} fps\n")
        if not found:
            print("  no vertical divider found. Treat as a single picture.")
            return 0
        for d in found:
            f0, f1 = d["frames"]
            print(f"  divider at x={d['split_x']}  ({d['verdict']})")
            print(f"      held still for {d['evidence_frames']} frames while the frame moved,"
                  f" at {d['still_ratio'] * 100:.1f}% of the frame's own motion")
            print(f"      present over frames {f0}..{f1}"
                  f"  ({f0 / info['fps']:.2f}s to {f1 / info['fps']:.2f}s),"
                  f" held for {d['coverage'] * 100:.0f}% of them.")
            print("      Those edges are where the EVIDENCE starts and stops, not"
                  " necessarily the cut. Check the head and tail against the cut"
                  " list: a split screen that dissolves in from a card reads late.")
            print(f"      gradient across the seam {d['gutter_gradient']:.2f} levels"
                  f"  ({'safe to switch on' if d['gutter_gradient'] < 1.2 else 'TOO STEEP to switch on'})")
            print(f"      left panel  = columns 0..{d['split_x'] - 1}")
            print(f"      right panel = columns {d['split_x']}..{info['width'] - 1}")
        return 0

    if args.cmd == "gutter":
        a, b = (int(v) for v in args.range.split(","))
        x, flat = gutter(args.video, a, b, near=args.near)
        print(f"frames {a}..{b}: switch at x={x}, gradient there {flat:.3f} levels")
        print("under about 1.2 levels a hard switch cannot show" if flat < 1.2
              else "WARNING: not flat enough to switch on, feather or move the seam")
        return 0

    if args.cmd == "cuts":
        band = tuple(int(v) for v in args.band.split(",")) if args.band else (None, None)
        c, t, _ = cuts(args.video, band[0], band[1])
        if args.json:
            print(json.dumps({"band": band, "threshold": t, "cuts": c}))
        else:
            print(f"band {band[0]}..{band[1]}  threshold {t:.3f}  {len(c)} cuts")
            print("  " + ", ".join(str(v) for v in c))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
