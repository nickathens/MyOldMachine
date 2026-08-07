#!/usr/bin/env python3
"""Find and rebuild frames that do not advance. Picture repair, before colour.

A cut arrives with frames that repeat instead of moving: a frame rate round trip
somewhere upstream, a render that dropped frames, an export at the wrong rate.
It reads as a hitch. This module finds those frames, decides whether the shot
needs its timing rebuilt or only its holes filled, and rebuilds them.

Four things here were each wrong before they were right, on real footage, and
each one is now a rule rather than a preference.

**A stall is not just a dead repeat.** An absolute threshold catches frames that
froze completely and lets through the half steps beside them, and rebuilding a
dead frame against a half step leaves two frames crawling at a quarter of the
shot's motion, which is a worse hitch than the one you started with. A frame is
stalled when THREE things hold together: it moves far less than the frames right
around it, it is genuinely hold-like against the shot, and it is part of a run
no longer than a few frames. Drop the third and the rule wants a quarter of a
film, including a settled close up where the actor is simply still, and rebuilding
that invents movement that was never shot.

**Filling a hole and rebuilding timing are different jobs.** Filling puts a
picture where a repeat was and leaves every surviving picture where the editor
put it. That is right when stalls are scattered. It is wrong after a frame rate
round trip, because then the survivors are not on even instants either: nine
pictures over twelve frames sit at 0,1,3,4,5,6,8,10,11 when they belong at
0,1.33,2.67,4... Up to two thirds of a frame out, twice a second. `cadence`
measures how far the survivors sit from even and says which job this is.

**Mean error against a held out real frame rewards blur.** A soft rebuild is
closer on average to the truth than a crisp one whose detail is a pixel out of
place, so a holdout scored on error alone systematically selects the softest
method. That is exactly how a rebuild shipped whose frames measure 10 to 25 per
cent softer than their neighbours, reading to the viewer as the subject twitching
sideways for one frame, which no motion measurement can see. Score three numbers:
error, gradient ENERGY against the real frame, and correlation of the gradient
MAP with the real frame's. The second catches blur. The third is needed because
Laplacian variance alone once reported rebuilt frames holding 178 per cent of the
real frame's detail: a double edge has more high frequency than a single one, so
ghosting reads as extra sharpness.

**Warping both frames and blending is what makes the blur.** Two warps that
disagree by a pixel average into a two tap blur. Use a network that produces one
picture, and apply its flow and fusion mask to the file's own y, u and v planes
so the picture never round trips through RGB.

    $PY scripts/cgframes.py detect IN.mp4
    $PY scripts/cgframes.py detect IN.mp4 --band 1940,3840 --cuts 182,263,361
    $PY scripts/cgframes.py holdout IN.mp4 --range 361,411
    $PY scripts/cgframes.py repair IN.mp4 --out FIXED.mp4
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


# ---------------------------------------------------------------- measurement


_STEP_CACHE = {}


def step_signal(path, x0=None, x1=None, scale=320):
    """Per frame, for the WHOLE film: how far it moved from the frame before.

    `d[i]` is the step into frame i+1. One decode for the film, cached, and every
    number downstream is a slice of this array. It is written that way for two
    reasons. Decoding per shot means ffmpeg walks the file from the start each
    time, which on a 4K piece with twenty five shots is twenty five full decodes
    and takes longer than the grade. And a per-shot decode is a second decode
    path, which is the error that once invented an 11 code level step at every
    repaired frame.
    """
    key = (path, x0, x1, scale)
    if key in _STEP_CACHE:
        return _STEP_CACHE[key]
    info = P.probe(path)
    arr, sig = P.stream(path, 0, info["frames"] - 1, x0, x1, scale=scale)
    f = arr.astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    _STEP_CACHE[key] = (d, sig)
    return d, sig


def _shot_steps(d, a, b):
    """The steps INTO frames a+1..b-1, as a slice of the film wide signal."""
    return d[a:b - 1]


def shot_bounds(path, x0=None, x1=None, cuts=None):
    """Shot edges inside a column band, as [start, end) pairs."""
    info = P.probe(path)
    if cuts is None:
        cuts, _, _ = P.cuts(path, x0, x1)
    edges = [0] + sorted(int(c) for c in cuts) + [info["frames"]]
    edges = sorted(set(e for e in edges if 0 <= e <= info["frames"]))
    return [(a, b) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def stalls(path, x0=None, x1=None, cuts=None, scale=320,
           rel=0.35, hold=0.30, max_run=3, min_shot=12):
    """Frames that do not advance, by the three condition rule.

    rel      how far below its own NEIGHBOURHOOD a frame's step must fall.
             Local, not shot wide: a shot wide threshold marks a settled close
             up as stalled and rebuilding it invents movement.
    hold     how far below the SHOT's typical step it must also fall. This is
             the absolute, hold-like condition, and on its own it only ever
             catches dead repeats.
    max_run  the longest run of consecutive stalls that still counts as a
             fault. Longer than this and the subject is genuinely still.

    All three must hold. Returns a list of dicts, one per stalled frame.
    """
    out = []
    film, _ = step_signal(path, x0, x1, scale=scale)
    for a, b in shot_bounds(path, x0, x1, cuts):
        if b - a < min_shot:
            continue
        d = _shot_steps(film, a, b)
        if len(d) < 6:
            continue
        moving = d[d > 0.35]
        if len(moving) < 5:
            continue                                  # a static graphic, not a shot
        base = float(np.median(moving))
        if base < 0.7:
            continue

        # local neighbourhood: the median step of the frames either side,
        # excluding the frame itself
        k = 5
        pad = np.pad(d, k, mode="edge")
        local = np.array([np.median(np.delete(pad[i:i + 2 * k + 1], k))
                          for i in range(len(d))])

        flagged = (d < rel * np.maximum(local, 1e-6)) & (d < hold * base)
        idx = np.where(flagged)[0]
        if not len(idx):
            continue
        for run in np.split(idx, np.where(np.diff(idx) > 1)[0] + 1):
            if not len(run) or len(run) > max_run:
                continue                              # a real hold, leave it alone
            for i in run:
                out.append({"frame": int(a + 1 + i), "shot": [int(a), int(b)],
                            "step": float(d[i]), "local": float(local[i]),
                            "shot_step": base, "run": int(len(run))})
    return out


def cadence(path, x0=None, x1=None, cuts=None, scale=320, ratio_limit=1.45,
            min_shot=12, min_pairs=5, survivor_limit=0.92):
    """Per shot: fill the holes in place, or rebuild the timing?

    The two faults look identical in a list of stalled frames and want opposite
    repairs, so this has to be decided by measurement.

    **Frames merely dropped.** The survivors still sit on their true instants.
    A two frame gap therefore carries twice the movement of a one frame gap,
    because twice as much time passed. Fill the holes and leave every real
    picture exactly where the editor put it.

    **A frame rate round trip.** Every picture is on a wrong instant, so a two
    frame gap and a one frame gap carry the SAME movement. Filling holes here
    leaves the shot wobbling; the timing has to be rebuilt.

    So: the ratio of the movement carried by two frame gaps to that carried by
    one frame gaps. Near 2 means fill. Near 1 means retime. This replaced a
    measure of how far the survivors sat from even instants, which is a weaker
    proxy: on a shot that had merely dropped frames it read 0.53 frames of wobble
    and called for a retime, while the gap ratio read 1.72 and correctly called
    for a fill. An earlier version of the same idea wanted to rebuild the timing
    of nine shots including a static graphic where half the frames are identical
    on purpose, so shots with too few real steps are skipped rather than judged.
    """
    st = {d["frame"] for d in stalls(path, x0, x1, cuts, scale=scale)}
    film, _ = step_signal(path, x0, x1, scale=scale)
    info = P.probe(path)
    rows = []
    for a, b in shot_bounds(path, x0, x1, cuts):
        if b - a < min_shot:
            continue
        d = _shot_steps(film, a, b)
        moving = d[d > 0.35]
        if len(moving) < 5 or float(np.median(moving)) < 0.7:
            continue
        surv = [f for f in range(a, b) if f not in st]
        n = len(surv)
        rate = info["fps"] * n / (b - a)
        row = {"shot": [int(a), int(b)], "survivors": n, "of": b - a, "rate": rate}
        if n < 4 or n == b - a:
            rows.append({**row, "plan": "nothing to do", "ratio": None, "pairs": [0, 0]})
            continue

        # movement carried across each gap between consecutive survivors, keyed
        # by the size of the gap. The step INTO frame f is d[f - 1 - a].
        by_gap = {}
        for f0, f1 in zip(surv[:-1], surv[1:]):
            i = f1 - 1 - a
            if 0 <= i < len(d):
                by_gap.setdefault(f1 - f0, []).append(float(d[i]))
        one, two = by_gap.get(1, []), by_gap.get(2, [])
        if len(one) < min_pairs or len(two) < min_pairs:
            rows.append({**row, "plan": "fill in place", "ratio": None,
                         "pairs": [len(one), len(two)],
                         "why": "not enough gaps of both sizes to tell the two "
                                "faults apart, so take the lighter touch"})
            continue
        ratio = float(np.mean(two) / max(np.mean(one), 1e-6))
        # A rate conversion loses a large, fixed share of the frames: 24 to 18
        # loses a quarter. A shot that kept 96 per cent of its frames cannot be
        # one however its gap ratio reads, and calling it one would move every
        # real picture in it to fix two stalls.
        kept = n / (b - a)
        plan = ("retime" if ratio < ratio_limit and kept < survivor_limit
                else "fill in place")
        rows.append({**row, "ratio": ratio, "pairs": [len(one), len(two)],
                     "kept": float(kept), "plan": plan})
    return rows


def judder(path, a, b, x0=None, x1=None, scale=320):
    """How rough a shot plays: movement that ALTERNATES frame to frame.

    Movement is allowed to speed up and slow down. What the eye reads as judder
    is a step that is short, then long, then short. Measured as each step's
    distance from the average of its two neighbours, over the shot's own mean
    step, so it is comparable between shots. A shot with no stalls at all gives
    the floor this can reach on real footage, which is the only honest baseline.
    """
    film, _ = step_signal(path, x0, x1, scale=scale)
    d = _shot_steps(film, a, b)
    if len(d) < 6 or d.mean() < 0.2:
        return None
    return float(np.abs(d[1:-1] - (d[:-2] + d[2:]) / 2).mean() / d.mean())


# ---------------------------------------------------------------- scoring


def score(rebuilt, truth, previous):
    """Three numbers, because one of them selects for blur.

    error   mean absolute difference from the held out real frame. Lower is
            better, AND ON ITS OWN IT PREFERS A BLURRY ANSWER.
    detail  gradient energy as a fraction of the real frame's. 1.0 is right.
            Below 1 is soft. Above 1 is usually ghosting, not extra sharpness:
            a double edge carries more high frequency than a single one.
    placed  correlation of the gradient map with the real frame's. This is the
            one that separates a crisp frame in the right place from a crisp
            frame in the wrong place, which `detail` alone cannot do.
    """
    a = np.asarray(rebuilt, dtype=np.float32)
    b = np.asarray(truth, dtype=np.float32)
    err = float(np.abs(a - b).mean())

    def grad(x):
        gx = np.diff(x, axis=1)[:-1, :]
        gy = np.diff(x, axis=0)[:, :-1]
        return np.hypot(gx, gy)

    ga, gb = grad(a), grad(b)
    detail = float(ga.mean() / max(gb.mean(), 1e-6))
    va, vb = ga.ravel() - ga.mean(), gb.ravel() - gb.mean()
    placed = float((va * vb).sum() / max(np.sqrt((va * va).sum() * (vb * vb).sum()), 1e-9))
    return {"error": err, "detail": detail, "placed": placed,
            "vs_previous": float(np.abs(b - np.asarray(previous, np.float32)).mean())}


def holdout_triplets(path, a, b, stalled, want=12):
    """(before, hidden, after) where all three are real consecutive frames.

    The hidden frame must be a real photograph, never another rebuild, or the
    test measures how well a method copies itself.
    """
    real = [f for f in range(a, b) if f not in stalled]
    trip = [(f - 1, f, f + 1) for f in real
            if f - 1 in real and f + 1 in real and f - 1 >= a and f + 1 < b]
    step = max(1, len(trip) // want)
    return trip[::step][:want]


# ---------------------------------------------------------------- rebuilding


def _planes(buf, W, H):
    y = np.frombuffer(bytes(buf[:W * H]), np.uint8).reshape(H, W)
    o = W * H
    u = np.frombuffer(bytes(buf[o:o + o // 4]), np.uint8).reshape(H // 2, W // 2)
    v = np.frombuffer(bytes(buf[o + o // 4:]), np.uint8).reshape(H // 2, W // 2)
    return y, u, v


def read_yuv(path, a, b, W, H):
    """Frames a..b inclusive as raw yuv420p buffers, one decode."""
    fb = W * H * 3 // 2
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"select='between(n\\,{a}\\,{b})'", "-vsync", "0",
         "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        capture_output=True, check=True).stdout
    n = len(raw) // fb
    if n != b - a + 1:
        raise RuntimeError(f"decoded {n} frames, wanted {b - a + 1}")
    return {a + i: bytearray(raw[i * fb:(i + 1) * fb]) for i in range(n)}


def rebuild_plan(stalled):
    """Each stalled frame and the two REAL pictures it sits between.

    Brackets are always real pictures out of the source, never another rebuild,
    or error compounds along a run. Frames at the very end of a shot have no real
    picture after them; they are returned separately, to be handled by respreading
    the shot's last real pictures rather than by inventing one.
    """
    bad = {}
    for d in stalled:
        bad.setdefault(tuple(d["shot"]), set()).add(d["frame"])

    plan, tails = [], []
    for (a, b), frames in sorted(bad.items()):
        for f in sorted(frames):
            prev = [g for g in range(f - 1, a - 1, -1) if g not in frames]
            nxt = [g for g in range(f + 1, b) if g not in frames]
            if not prev:
                continue                              # nothing real before it either
            if not nxt:
                reals = [g for g in range(f - 1, a - 1, -1) if g not in frames][:4][::-1]
                tails.append({"frame": f, "shot": [a, b], "reals": reals})
                continue
            p0, p1 = prev[0], nxt[0]
            plan.append({"frame": f, "p0": p0, "p1": p1,
                         "t": (f - p0) / (p1 - p0), "shot": [a, b]})
    return plan, tails


def _rife():
    try:
        import cgrife
        return cgrife
    except Exception as exc:                          # noqa: BLE001
        raise SystemExit(
            "The rebuild needs RIFE, which is not set up on this machine.\n"
            f"  {exc}\n"
            "  Run: bash scripts/setup_rife.sh\n"
            "Detection, cadence and judder need none of this and work now.")


def synth(bufs, p0, p1, t, W, H, x0=0, x1=None, scale=0.5):
    """One rebuilt frame's y, u and v planes, for the columns [x0, x1).

    RGB is used to estimate motion and for nothing else. What comes back from
    the network is a vector field and a fusion weight; those are applied to the
    file's own planes, so the picture never round trips through RGB and the
    untouched columns stay byte identical.
    """
    R = _rife()
    x1 = W if x1 is None else x1
    x0, x1 = P.snap_band(x0, x1, W)
    return R.warp_planes(_planes(bufs[p0], W, H), _planes(bufs[p1], W, H),
                         float(t), x0, x1, H, scale=scale)


# ---------------------------------------------------------------- cli


def _fmt_time(f, fps):
    return f"{int(f / fps // 60):d}:{f / fps % 60:05.2f}"


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, helptext in (("detect", "stalled frames and the plan per shot"),
                           ("holdout", "compare rebuild methods on hidden real frames"),
                           ("repair", "rebuild the stalled frames")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("video")
        p.add_argument("--band", default=None,
                       help="x0,x1 to work on one panel of a split screen")
        p.add_argument("--cuts", default=None, help="comma separated cut frames")
        p.add_argument("--scale", type=int, default=320)
        p.add_argument("--json", action="store_true")
        if name == "holdout":
            p.add_argument("--range", required=True, help="a,b of one shot")
        if name == "repair":
            p.add_argument("--out", required=True)
            p.add_argument("--crf", type=int, default=14)

    args = ap.parse_args(argv)
    band = tuple(int(v) for v in args.band.split(",")) if args.band else (None, None)
    cuts = [int(v) for v in args.cuts.split(",")] if args.cuts else None
    info = P.probe(args.video)
    fps = info["fps"]

    if args.cmd == "detect":
        st = stalls(args.video, band[0], band[1], cuts, scale=args.scale)
        cd = cadence(args.video, band[0], band[1], cuts, scale=args.scale)
        if args.json:
            print(json.dumps({"stalls": st, "cadence": cd}, indent=1))
            return 0
        print(f"{args.video}  {info['frames']} frames at {fps:.3f} fps"
              + (f", columns {band[0]}..{band[1]}" if band[0] is not None else ""))
        print(f"\n{len(st)} frames do not advance:")
        by_shot = {}
        for d in st:
            by_shot.setdefault(tuple(d["shot"]), []).append(d["frame"])
        for shot, frames in sorted(by_shot.items()):
            print(f"  shot {shot[0]:5d}-{shot[1] - 1:<5d} "
                  f"({_fmt_time(shot[0], fps)})  {len(frames):3d} frames: "
                  + ", ".join(str(f) for f in frames[:10])
                  + (" ..." if len(frames) > 10 else ""))
        print("\nper shot plan  (gap ratio near 2 = frames dropped, near 1 = rate "
              "conversion):")
        for r in cd:
            if r["plan"] == "nothing to do":
                continue
            ratio = ("  --  " if r["ratio"] is None else f"{r['ratio']:5.2f}")
            print(f"  shot {r['shot'][0]:5d}-{r['shot'][1] - 1:<5d} "
                  f"{r['survivors']:3d}/{r['of']:<3d} real pictures = "
                  f"{r['rate']:4.1f}/s   gap ratio {ratio} "
                  f"(from {r['pairs'][0]}+{r['pairs'][1]} gaps)  -> {r['plan'].upper()}")
        rt = [r for r in cd if r["plan"] == "retime"]
        print(f"\n{len(rt)} shot(s) need their timing rebuilt, the rest only need "
              f"their holes filled.")
        print("Holds inside cards and end boards are deliberate. Check any shot "
              "listed here against the picture before rebuilding it.")
        return 0

    if args.cmd == "holdout":
        a, b = (int(v) for v in args.range.split(","))
        st = {d["frame"] for d in stalls(args.video, band[0], band[1], cuts,
                                         scale=args.scale)}
        trips = holdout_triplets(args.video, a, b, st)
        if not trips:
            print("no run of three consecutive real frames in that range")
            return 1
        W, H = info["width"], info["height"]
        x0, x1 = (band[0] or 0), (band[1] or W)
        bufs = read_yuv(args.video, a, b - 1, W, H)
        methods = {}
        for p0, hid, p1 in trips:
            truth = _planes(bufs[hid], W, H)[0][:, x0:x1]
            prev = _planes(bufs[p0], W, H)[0][:, x0:x1]
            methods.setdefault("repeat the frame before", []).append(
                score(prev, truth, prev))
            blend = (_planes(bufs[p0], W, H)[0].astype(np.float32)[:, x0:x1] +
                     _planes(bufs[p1], W, H)[0].astype(np.float32)[:, x0:x1]) / 2
            methods.setdefault("average the two", []).append(score(blend, truth, prev))
            try:
                y = synth(bufs, p0, p1, 0.5, W, H, x0, x1)[0]
                methods.setdefault("motion, one picture (RIFE)", []).append(
                    score(y, truth, prev))
            except SystemExit:
                pass
        print(f"{len(trips)} real frames hidden and rebuilt, frames {a}..{b - 1}\n")
        print(f"{'method':30s} {'error':>7s} {'detail':>8s} {'placed':>8s}")
        print("-" * 56)
        for name, rows in methods.items():
            print(f"{name:30s} {np.mean([r['error'] for r in rows]):7.3f} "
                  f"{np.mean([r['detail'] for r in rows]) * 100:7.1f}% "
                  f"{np.mean([r['placed'] for r in rows]):8.4f}")
        print("\nerror alone prefers a blurry answer. Read detail with it: under "
              "100% is soft, over 100% is usually ghosting rather than sharpness, "
              "and `placed` is the tiebreak.")
        return 0

    if args.cmd == "repair":
        return _repair(args, info, band, cuts)
    return 1


def _repair(args, info, band, cuts):
    W, H = info["width"], info["height"]
    x0, x1 = (band[0] or 0), (band[1] or W)
    st = stalls(args.video, band[0], band[1], cuts, scale=args.scale)
    if not st:
        print("nothing to repair")
        return 0
    plan, tails = rebuild_plan(st)
    cd = {tuple(r["shot"]): r for r in cadence(args.video, band[0], band[1], cuts,
                                               scale=args.scale)}
    retime = [s for s, r in cd.items() if r["plan"] == "retime"]
    if retime:
        print(f"NOTE: {len(retime)} shot(s) measure as a frame rate round trip and "
              f"want their timing rebuilt, not their holes filled: "
              + ", ".join(f"{a}-{b - 1}" for a, b in retime))
        print("This command fills in place. Retiming moves real pictures and is a "
              "decision for a person, so it is not automatic.")

    _rife()                                            # fail early with instructions
    bufs = read_yuv(args.video, 0, info["frames"] - 1, W, H)
    fb = W * H * 3 // 2
    done = 0
    for d in plan:
        y, u, v = synth(bufs, d["p0"], d["p1"], d["t"], W, H, x0, x1)
        buf = bufs[d["frame"]]
        sx0, sx1 = P.snap_band(x0, x1, W)
        yy, uu, vv = _planes(buf, W, H)
        yy = yy.copy(); uu = uu.copy(); vv = vv.copy()
        yy[:, sx0:sx1] = y
        uu[:, sx0 // 2:sx1 // 2] = u
        vv[:, sx0 // 2:sx1 // 2] = v
        bufs[d["frame"]] = bytearray(yy.tobytes() + uu.tobytes() + vv.tobytes())
        done += 1
    for d in tails:
        print(f"  frame {d['frame']} is the last of its shot and has no real picture "
              f"after it; respread {d['reals']} rather than invent one")

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p",
         "-s", f"{W}x{H}", "-r", str(info["fps"]), "-i", "-",
         "-i", args.video, "-map", "0:v", "-map", "1:a?", "-c:a", "copy",
         "-c:v", "libx264", "-crf", str(args.crf), "-preset", "slow",
         "-pix_fmt", "yuv420p", args.out], stdin=subprocess.PIPE)
    for n in range(info["frames"]):
        proc.stdin.write(bytes(bufs[n]))
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("encode failed")
    print(f"rebuilt {done} frames, wrote {args.out}")
    print(f"{len(tails)} shot-final frames left for a person to decide on.")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
