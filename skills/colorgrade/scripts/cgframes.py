#!/usr/bin/env python3
"""Frame repair: find frozen frames, rebuild them, prove the rebuild, splice it in.

    python scripts/cgframes.py repair IN.mp4 --work WORK --out OUT.mp4

Two faults hide under the same symptom and take opposite repairs.

A shot with **holes** is running at the right rate with individual frames stuck.
The repair is to fill each hole with a frame built from the two real frames
either side. Every other frame in the shot is left exactly where the editor put
it.

A shot with a **broken cadence** was retimed by repeating frames instead of by
interpolating them, so it advances at perhaps 18 unique pictures per second
inside a 24 fps film, in a repeating long short short pattern. Filling its holes
leaves a residual wobble at the same period as the original stutter, because the
surviving frames are not evenly spaced in time either. The repair is to throw
the repeats away, work out where the survivors really sit, and rebuild the whole
shot at its stated rate.

In a list of frozen frames the two look identical. `plan` separates them by how
dense and how regular the repeats are, and names its decision per shot so a
wrong call is visible rather than silent.

Every delivered frame is built in the film's own colour space, never in RGB, and
every rebuild is judged on rulers calibrated against the film's own frames. See
`cgyuv` for why, and `reference/07_frames.md` for the whole method.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgvideo as V
import cgyuv as Y

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

# A frame counts as frozen when it carries less than this share of the motion its
# own shot typically carries. Scoped to the shot so a genuinely still shot is not
# accused of stuttering.
FROZEN_RATIO = 0.10
# Above this share of frozen frames, arriving on a regular beat, the shot was
# retimed rather than damaged.
CADENCE_DENSITY = 0.15
CADENCE_REGULARITY = 0.35     # coefficient of variation of the gaps
MOVE_FLOOR = 0.5              # levels; below this the shot is not moving at all
# A run of frozen frames longer than this is a DELIBERATE HOLD and not a fault.
# No frame rate conversion produces long runs: 24 from 18 repeats one frame at a
# time, 24 from 12 every other frame, 24 from 8 in pairs. What does produce a
# long run is a title settling, an end board, or an actor simply being still.
# Without this cap a single 20 frame hold inside real footage was measured
# arriving at `plan` as density 0.26 with a gap spread of 0.00, because a solid
# run has every gap equal to 1 and so looks PERFECTLY regular, which is the
# exact signature the cadence test rewards. It was called a retimed shot and
# planned to move 48 real frames. Detection has to reject it, because by the
# time it reaches the cadence test it looks more like a retime than a retime does.
MAX_STALL_RUN = 3
MIN_REAL = 12                 # real frames a shot needs before it sets its own floor
HALO_CUT = 1.25               # against the real frames either side of that same frame
SOFT_PCT = 1                  # softness floor, centile of real frames
RATIO_PCT = 98                # position cut, centile of real frames
# A rebuilt frame is an average of two frames, so its whole frame colour offset
# against its neighbours can only be flatter than a real frame's. Past this
# multiple of the real median it is carrying a cast that the film does not have.
# That rebuild read 1.01 when it was right and 17.2 when it was wrong.
FLAT_RATIO = 1.5


# ---------------------------------------------------------------- work dir


def work_paths(work):
    return {
        "state": f"{work}/state.json",
        "scan": f"{work}/scan.npz",
        "census": f"{work}/census.json",
        "jobs": f"{work}/jobs.json",
        "src_yuv": f"{work}/src_yuv",
        "png": f"{work}/png",
        "built": f"{work}/built",
        "sharp": f"{work}/sharp",
        "gate": f"{work}/gate.json",
        "keep": f"{work}/keep.json",
    }


def load_state(work):
    p = work_paths(work)
    if not os.path.exists(p["state"]):
        sys.exit(f"no state in {work}. Run census first.")
    return json.load(open(p["state"]))


def spec_of(st) -> Y.Spec:
    return Y.Spec(st["width"], st["height"], st["color_range"], st["colorspace"])


def shot_bounds(st):
    """Shot start frames plus the end, so shot i covers b[i] to b[i+1] exclusive."""
    return [s[0] for s in st["shots"]] + [st["nb_frames"]]


def shot_index(st):
    b = shot_bounds(st)
    idx = np.zeros(st["nb_frames"], int)
    for i in range(len(b) - 1):
        idx[b[i]:b[i + 1]] = i
    return idx


# ------------------------------------------------- band scoped measurement
#
# The pipeline below works per shot off one whole frame scan held in a work
# directory. These three are the standalone measuring tools: they answer a
# question about a COLUMN BAND of a file, with no work directory and no state,
# which is what a split screen needs before anything else has been decided, and
# what `selftest_tools.py` measures against. They share the pipeline's rules,
# not its plumbing.


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
    import cgpanel as P
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


def band_shot_bounds(path, x0=None, x1=None, cuts=None):
    """Shot edges inside a column band, as [start, end) pairs."""
    import cgpanel as P
    info = P.probe(path)
    if cuts is None:
        cuts, _, _ = P.cuts(path, x0, x1)
    edges = [0] + sorted(int(c) for c in cuts) + [info["frames"]]
    edges = sorted(set(e for e in edges if 0 <= e <= info["frames"]))
    return [(a, b) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def stalls(path, x0=None, x1=None, cuts=None, scale=320,
           rel=0.35, hold=0.30, max_run=MAX_STALL_RUN, min_shot=12):
    """Frames that do not advance, by the three condition rule.

    rel      how far below its own NEIGHBOURHOOD a frame's step must fall.
             Local, not shot wide: a shot wide threshold marks a settled close
             up as stalled and rebuilding it invents movement.
    hold     how far below the SHOT's typical step it must also fall. This is
             the absolute, hold-like condition, and on its own it only ever
             catches dead repeats.
    max_run  the longest run of consecutive stalls that still counts as a
             fault. Longer than this and the subject is genuinely still. This is
             the same cap the pipeline applies in `_drop_holds`, and it is not
             optional: see MAX_STALL_RUN for what a long run does to the
             cadence test.

    All three must hold. Returns a list of dicts, one per stalled frame.
    """
    out = []
    film, _ = step_signal(path, x0, x1, scale=scale)
    for a, b in band_shot_bounds(path, x0, x1, cuts):
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


# ---------------------------------------------------------------- census


def cmd_census(args):
    os.makedirs(args.work, exist_ok=True)
    p = work_paths(args.work)
    media = V.probe(args.input)
    if args.cuts:
        shots = V.shots_from_cuts(media, [int(c) for c in args.cuts.split(",") if c.strip()])
        how = "supplied"
    else:
        shots = V.detect_shots(media, verbose=False)
        how = "detected"
    spec = Y.spec_from_media(media, args.color_range)
    print(f"{media.width}x{media.height} {media.fps:.3f} fps, {media.nb_frames} frames, "
          f"{len(shots)} shots ({how}), tagged {spec.colorspace} {spec.color_range}")

    ruler = Y.Ruler(spec, args.view_width)
    print("measuring every frame at viewing scale")
    lap, diff, span, area = ruler.scan(args.input)
    n = min(len(lap), media.nb_frames)
    np.savez(p["scan"], lap=lap[:n], diff=diff[:n], span=span[:n], area=area[:n])

    st = {
        "input": os.path.abspath(args.input),
        "width": media.width, "height": media.height, "fps": media.fps,
        "nb_frames": n, "has_audio": media.has_audio,
        "color_range": spec.color_range, "colorspace": spec.colorspace,
        "view_width": ruler.view_w, "view_height": ruler.view_h,
        "shots": [[s.start_frame, min(s.end_frame, n)] for s in shots if s.start_frame < n],
        "panel": [int(x) for x in args.panel.split(",")] if args.panel else None,
    }
    json.dump(st, open(p["state"], "w"), indent=1)

    census = census_frozen(st, diff[:n], area[:n])
    json.dump(census, open(p["census"], "w"), indent=1)
    report_census(st, census)
    return census


def census_frozen(st, diff, area=None):
    """Frozen frames per shot, judged against that shot's own typical motion.

    Two readings, and a frame is frozen if EITHER calls it frozen.

    `diff` is the mean step. It is the right test for a photographed shot and
    it is blind to a frozen plate under a moving graphic, because a small
    bright overlay lifts the mean without moving the picture underneath.

    `area` is the share of the frame that moved at all. It sees exactly that
    case, and it is the weaker test on flat motion graphics, where large parts
    of the frame are static by design and long runs of "frozen" frames are the
    normal state. Those are rejected by `_drop_holds` on run length, which is
    the same cap that already protects the mean test. See MAX_STALL_RUN.

    Reported per frame so the plan can say which reading found what.
    """
    b = shot_bounds(st)
    out = {}
    for i in range(len(b) - 1):
        a, e = b[i], b[i + 1]
        seg = diff[a + 1:e]
        if len(seg) < 6:
            continue
        med = float(np.median(seg))
        if med < 1e-5:                       # a shot that never moves has no stutter
            continue
        by_mean = {a + 1 + j for j, v in enumerate(seg) if v < FROZEN_RATIO * med}
        by_area = set()
        amed = 0.0
        if area is not None:
            aseg = area[a + 1:e]
            amed = float(np.median(aseg))
            if amed > 1e-6:
                by_area = {a + 1 + j for j, hit in enumerate(_local_frozen(aseg)) if hit}
        reps, held = _drop_holds(sorted(by_mean | by_area))
        if reps or held:
            out[str(i)] = {"span": [a, e - 1], "repeats": reps, "median_motion": med,
                           "held": held,
                           "median_area": amed,
                           "area_only": sorted((set(reps) & by_area) - by_mean)}
    return out


def _local_frozen(seg, rel=0.35, hold=0.30, k=5):
    """The three condition stall rule, applied to one shot's series.

    This is the rule `stalls()` already uses and documents, reused here rather
    than reinvented. A frame must fall below both

      rel   this share of its own NEIGHBOURHOOD's typical step, which is what
            lets a shot that slows down keep its frames: a shot wide threshold
            marks a settled close up as stalled
      hold  this share of the SHOT's typical step, the absolute condition

    The shot wide ratio on its own is what `census_frozen` applies to the mean
    series, and on the film this was built for it found 20 of 36 repeated frames
    because it needs a frame to fall to a TENTH. The repeats sat at 0.07 to 0.19
    of the shot median while every real frame sat at 0.71 or above, so the
    separation was enormous and the threshold simply sat on the wrong side of
    it. Under detection is not a safe failure here: 20 of 36 read as density
    0.14 with a gap spread of 0.54, which the cadence test calls holes, and
    filling holes in a retimed shot leaves the wobble it was meant to remove.
    """
    seg = np.asarray(seg, float)
    base = float(np.median(seg))
    if base <= 0:
        return np.zeros(len(seg), bool)
    pad = np.pad(seg, k, mode="edge")
    local = np.array([np.median(np.delete(pad[i:i + 2 * k + 1], k))
                      for i in range(len(seg))])
    return (seg < rel * np.maximum(local, 1e-12)) & (seg < hold * base)


def _drop_holds(reps):
    """Split frozen frames into faults and deliberate holds. See MAX_STALL_RUN."""
    faults, held, run = [], [], []
    for f in sorted(reps):
        if run and f == run[-1] + 1:
            run.append(f)
        else:
            (faults if len(run) <= MAX_STALL_RUN else held).extend(run)
            run = [f]
    (faults if len(run) <= MAX_STALL_RUN else held).extend(run)
    return faults, held


def report_census(st, census):
    total = sum(len(c["repeats"]) for c in census.values())
    holds = sum(len(c.get("held", [])) for c in census.values())
    n = st["nb_frames"]
    fps = st["fps"]
    print(f"\n{'shot':>4} {'frames':>15} {'t':>8} {'len':>5} {'frozen':>7} {'unique fps':>11}  first gaps")
    for k in sorted(census, key=int):
        c = census[k]
        a, e = c["span"]
        L = e - a + 1
        r = c["repeats"]
        gaps = list(map(int, np.diff(r)))[:10] if len(r) > 1 else []
        print(f"{int(k):4d} {a:6d}-{e:<8d}{a / fps:8.2f} {L:5d} {len(r):7d} "
              f"{fps * (1 - len(r) / L):11.1f}  {gaps}")
    print(f"\nfrozen frames: {total} of {n} ({100 * total / max(n, 1):.1f}%)")
    if holds:
        print(f"plus {holds} frames in runs longer than {MAX_STALL_RUN}, left alone as "
              "deliberate holds:")
        for k in sorted(census, key=int):
            h = census[k].get("held", [])
            if h:
                runs, run = [], []
                for f in h:
                    if run and f == run[-1] + 1:
                        run.append(f)
                    else:
                        runs.append(run) if run else None
                        run = [f]
                runs.append(run)
                for rr in runs:
                    print(f"  shot {int(k)}: frames {rr[0]}-{rr[-1]} "
                          f"({len(rr)} frames, {len(rr) / fps:.2f}s) held")
        print("  A card, an end board or an actor being still. Look at these before\n"
              "  assuming the tool missed something: rebuilding a hold invents movement\n"
              "  that was never shot.")


# ---------------------------------------------------------------- plan


def cmd_plan(args):
    p = work_paths(args.work)
    st = load_state(args.work)
    census = json.load(open(p["census"]))
    if isinstance(getattr(args, "force_mode", None), str):
        args.force_mode = {int(k): v for k, v in
                           (s.split(":") for s in args.force_mode.split(",") if s.strip())}
    jobs, modes = plan_jobs(st, census, args)
    json.dump(jobs, open(p["jobs"], "w"), indent=0)
    st["shot_modes"] = {str(k): v for k, v in modes.items()}
    json.dump(st, open(p["state"], "w"), indent=1)
    fill = sum(1 for j in jobs if j[4] == "fill")
    rebuild = sum(1 for j in jobs if j[4] == "rebuild")
    copy = sum(1 for j in jobs if j[4] == "copy")
    print(f"\n{fill + rebuild} frames to synthesise: {fill} filling holes, "
          f"{rebuild} rebuilding cadence, plus {copy} real frames moved to new slots")
    print("wrote", p["jobs"])
    if rebuild:
        print("\nCheck these shots by eye before building. A cadence rebuild replaces")
        print("almost every frame of the shot, and calling it wrongly on a shot that")
        print("only has holes throws away frames the editor chose.")
    return jobs


def plan_jobs(st, census, args):
    """One job list covering both repairs: (frame, prev_real, next_real, t, mode)."""
    jobs, modes = [], {}
    print(f"\n{'shot':>4} {'frozen':>7} {'density':>8} {'gap cv':>7}  decision")
    for k in sorted(census, key=int):
        c = census[k]
        a, e = c["span"]
        reps = sorted(c["repeats"])
        L = e - a + 1
        density = len(reps) / L
        gaps = np.diff(reps).astype(float)
        cv = float(gaps.std() / gaps.mean()) if len(gaps) >= 3 and gaps.mean() > 0 else 9.9
        forced = (args.force_mode or {}).get(int(k))
        if forced:
            mode = forced
        elif density >= CADENCE_DENSITY and cv <= CADENCE_REGULARITY:
            mode = "rebuild"
        else:
            mode = "fill"
        modes[int(k)] = mode
        made = (rebuild_jobs(st, a, e, reps, args) if mode == "rebuild"
                else fill_jobs(a, e, reps))
        jobs.extend(made)
        why = ("dense and regular, this shot was retimed" if mode == "rebuild"
               else "sparse or irregular, these are holes")
        print(f"{int(k):4d} {len(reps):7d} {density:8.2f} {cv:7.2f}  {mode.upper():7s} "
              f"{len(made):4d} frames, {why}")
    return jobs, modes


def fill_jobs(a, e, reps):
    """Each frozen frame rebuilt from the nearest real frame either side."""
    frozen = set(reps)
    out = []
    for f in reps:
        p = f - 1
        while p in frozen and p > a:
            p -= 1
        q = f + 1
        while q in frozen and q < e:
            q += 1
        if p < a or q > e or p in frozen or q in frozen:
            continue                          # no real frame on one side, leave it
        out.append([int(f), int(p), int(q), (f - p) / (q - p), "fill"])
    return out


def rebuild_jobs(st, a, e, reps, args):
    """Throw the repeats away and respread the survivors across the whole shot.

    The survivors are not evenly spaced in time. Their real positions are
    recovered from the motion between them: with M survivors shown over N slots
    at unchanged speed, exactly N minus M of the M minus 1 steps have to span two
    originals, and the ones that do are the ones that moved most. Assuming even
    spacing instead leaves a wobble at the period of the original stutter.

    Every slot in the shot is written, not just the synthesised ones. A retime
    moves the surviving frames too, so a plan that only patched the new frames
    would leave the survivors at their old positions and the shot would come out
    incoherent. Slots that land on a survivor are emitted as `copy`, which moves
    a real frame and never synthesises anything.

    The first and last slots always land on originals, so no cut point moves and
    neither side of a cut is ever a synthesised frame.
    """
    frozen = set(reps)
    survivors = [f for f in range(a, e + 1) if f not in frozen]
    n_out = e - a + 1
    if len(survivors) < 3 or len(survivors) >= n_out:
        return fill_jobs(a, e, reps)
    pos = survivor_positions(st, survivors, n_out, args)
    out = []
    for k in range(n_out):
        i = int(np.clip(np.searchsorted(pos, k, side="right") - 1, 0, len(pos) - 2))
        span = pos[i + 1] - pos[i]
        t = 0.0 if span <= 0 else (k - pos[i]) / span
        slot = int(a + k)
        if t < 1e-6 or t > 1 - 1e-6:
            src = survivors[i] if t < 1e-6 else survivors[i + 1]
            if src != slot:
                out.append([slot, int(src), int(src), 0.0, "copy"])
            continue
        out.append([slot, int(survivors[i]), int(survivors[i + 1]), float(t), "rebuild"])
    return out


def survivor_positions(st, survivors, n_out, args, local=9):
    """Where each surviving frame really sits on the output timeline."""
    m = survivor_motion(st, survivors, args)
    M = len(survivors)
    n_double = n_out - M
    if n_double <= 0 or n_double >= M - 1 or m is None:
        return np.arange(M) * (n_out - 1) / (M - 1)
    pad = np.pad(m, local // 2, mode="edge")
    loc = np.array([np.median(pad[i:i + local]) for i in range(len(m))])
    score = m / np.maximum(loc, 1e-6)         # judge a still passage against its own
    gaps = np.ones(M - 1)
    gaps[np.argsort(score)[-n_double:]] = 2.0
    pos = np.concatenate([[0.0], np.cumsum(gaps)])
    return pos * (n_out - 1) / pos[-1]


def survivor_motion(st, survivors, args):
    """Motion between consecutive survivors, from one small decode of the shot."""
    a, e = survivors[0], survivors[-1]
    w = 480
    h = int(round(st["height"] * w / st["width"])) // 2 * 2
    p = subprocess.run(
        [FFMPEG, "-v", "error", "-i", st["input"],
         "-vf", f"select='between(n\\,{a}\\,{e})',scale={w}:{h}",
         "-vsync", "0", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True)
    n = w * h
    have = len(p.stdout) // n
    if have < e - a + 1:
        return None
    frames = {a + i: np.frombuffer(p.stdout[i * n:(i + 1) * n], np.uint8).reshape(h, w).astype(np.int16)
              for i in range(have)}
    return np.array([float((np.abs(frames[survivors[i]] - frames[survivors[i + 1]]) > 6).mean())
                     for i in range(len(survivors) - 1)])


# ---------------------------------------------------------------- build


def cmd_build(args):
    p = work_paths(args.work)
    st = load_state(args.work)
    spec = spec_of(st)
    jobs = [j for j in json.load(open(p["jobs"])) if j[4] != "copy"]
    copies = [j for j in json.load(open(p["jobs"])) if j[4] == "copy"]
    if not jobs:
        print("nothing to synthesise")
        return
    ok, why = _flow_ready()
    if not ok:
        sys.exit(why)
    import cgflow

    how = Y.engine()
    if how == "rife":
        print("building with rife")
    else:
        print("building with raft, THE FALLBACK. It averages two warps, which is both\n"
              "  soft and biased, and on real footage it has failed the colour verdict\n"
              "  where rife passed. Expect rejections. Run scripts/setup_rife.sh.")

    need = sorted({x for _, q0, q1, _, _ in jobs for x in (q0, q1)}
                  | {j[1] for j in copies})
    print(f"extracting {len(need)} source frames as raw planes")
    Y.extract(st["input"], need, p["src_yuv"], spec)
    # rife reads motion off the planes it was given, so the stills are only
    # needed for the raft path and for the holdout test.
    flow_need = sorted({x for _, q0, q1, _, _ in jobs for x in (q0, q1)})
    print(f"extracting {len(flow_need)} of them as stills, for motion only")
    _extract_pngs(st, flow_need, p["png"])

    os.makedirs(p["built"], exist_ok=True)
    panel = st.get("panel")
    cache_key, cache = None, None
    done = 0
    import time
    t0 = time.time()
    for k, (f, q0, q1, t, mode) in enumerate(jobs, 1):
        dst = f"{p['built']}/{f:06d}.yuv"
        if os.path.exists(dst) and os.path.getsize(dst) == spec.frame_bytes:
            continue
        if how == "raft" and cache_key != (q0, q1):
            cache = cgflow.flow_pair(f"{p['png']}/{q0:06d}.png", f"{p['png']}/{q1:06d}.png")
            cache_key = (q0, q1)
        a = Y.read_yuv(f"{p['src_yuv']}/{q0:06d}.yuv", spec)
        b = Y.read_yuv(f"{p['src_yuv']}/{q1:06d}.yuv", spec)
        built = Y.synth(a, b, t, *(cache or (None, None)), spec=spec, how=how)
        if panel:
            base = Y.read_yuv(f"{p['src_yuv']}/{q0:06d}.yuv", spec)
            built = Y.paste_panel(base, built, panel[0], panel[1], spec)
        with open(dst, "wb") as fh:
            fh.write(built)
        done += 1
        if done % 20 == 0:
            print(f"  built {done}  ({time.time() - t0:.0f}s)", flush=True)
    print(f"built {done} frames in {time.time() - t0:.0f}s, in the film's own colour space")


def _flow_ready():
    import importlib
    try:
        cgflow = importlib.import_module("cgflow")
    except ImportError as e:
        return False, str(e)
    return cgflow.available()


def _extract_pngs(st, need, out_dir):
    """Stills for the flow estimator only. Their colour does not matter, and
    nothing read from them reaches an output pixel."""
    os.makedirs(out_dir, exist_ok=True)
    todo = [f for f in need if not os.path.exists(f"{out_dir}/{f:06d}.png")]
    if not todo:
        print(f"  stills already present: {len(need)}")
        return
    want = set(todo)
    last = max(want)
    w, h = st["width"], st["height"]
    dec = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", st["input"], "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
    from PIL import Image
    n = w * h * 3
    i = done = 0
    try:
        while i <= last:
            buf = dec.stdout.read(n)
            if len(buf) < n:
                break
            if i in want:
                Image.frombytes("RGB", (w, h), buf).save(f"{out_dir}/{i:06d}.png", compress_level=1)
                done += 1
                if done % 100 == 0:
                    print(f"  stills {done}/{len(todo)}", flush=True)
            i += 1
    finally:
        dec.stdout.close()
        dec.terminate()
        dec.wait()
    missing = [f for f in todo if not os.path.exists(f"{out_dir}/{f:06d}.png")]
    if missing:
        raise RuntimeError(f"decode ended early: {len(missing)} stills never arrived, "
                           f"first missing {missing[0]}")
    print(f"  stills {done}/{len(todo)}")


# ---------------------------------------------------------------- gate


def cmd_gate(args):
    p = work_paths(args.work)
    st = load_state(args.work)
    spec = spec_of(st)
    scan = np.load(p["scan"])
    lap, span = scan["lap"], scan["span"]
    plan = {int(j[0]): j for j in json.load(open(p["jobs"]))}
    jobs = {f: j for f, j in plan.items() if j[4] != "copy"}
    if not jobs:
        print("nothing to judge")
        return

    ruler = Y.Ruler(spec, st["view_width"])
    # The position reading needs the frame either side of every rebuild, which is
    # not always one of the two it was built from. Without them the reading has
    # nothing to divide by and quietly scores zero, which passes everything.
    need = {f + d for f in jobs for d in (-1, 1) if 0 <= f + d < st["nb_frames"]}
    need |= {j[1] for j in jobs.values()} | {j[2] for j in jobs.values()}
    Y.extract(st["input"], sorted(need), p["src_yuv"], spec)

    print("proving the ruler before measuring anything")
    ruler.check(p["src_yuv"], sorted({j[1] for j in jobs.values()}), lap)

    b = shot_bounds(st)
    sidx = shot_index(st)
    near_cut = set()
    for c in b[1:-1]:
        near_cut |= {c - 2, c - 1, c, c + 1, c + 2}

    # Floors come from the film's own photographed frames. Not from "frames
    # nobody touched": in a retimed shot a real frame still moves to a new slot,
    # and a frozen repeat sits untouched while carrying no motion at all, so
    # judging by whether a frame was touched calibrates on exactly the wrong
    # population. Frozen frames are excluded, survivors are not.
    frozen = set()
    if os.path.exists(p["census"]):
        for c in json.load(open(p["census"])).values():
            frozen |= set(c["repeats"])
    real, triples = [], []
    for f in range(2, st["nb_frames"] - 2):
        if f in frozen or f in near_cut or span[f] < MOVE_FLOOR:
            continue
        p0, q0 = _flank(f, frozen, sidx, b)
        if p0 is None:
            continue
        real.append((f, lap[f] / max(0.5 * (lap[p0] + lap[q0]), 1e-9)))
        if f - 1 not in frozen and f + 1 not in frozen:
            triples.append(0.5 * (scan["diff"][f] + scan["diff"][f + 1]) / max(span[f], 1e-9))
    if len(real) < MIN_REAL:
        sys.exit(f"only {len(real)} photographed frames carry enough motion to calibrate "
                 f"on. There is nothing to judge a rebuild against here, so judge this "
                 f"one by eye rather than trusting a number.")
    film_soft = float(np.percentile([r[1] for r in real], SOFT_PCT))
    if len(triples) >= MIN_REAL:
        ratio_cut = float(np.percentile(triples, RATIO_PCT))
        print(f"floors from {len(real)} photographed frames: softness {film_soft:.3f}, "
              f"position {ratio_cut:.3f} (from {len(triples)} clean triples)")
    else:
        ratio_cut = float("inf")
        print(f"floor from {len(real)} photographed frames: softness {film_soft:.3f}")
        print(f"  the position test is OFF: only {len(triples)} runs of three consecutive "
              f"real frames\n  exist in this film, which is too few to take a cut from. "
              f"Nothing is invented to\n  replace it, so watch the result rather than "
              f"reading the pass as proof.")

    shot_soft, borrowed = {}, []
    for s in range(len(b) - 1):
        v = [r[1] for r in real if sidx[r[0]] == s]
        if len(v) >= MIN_REAL:
            shot_soft[s] = float(np.percentile(v, SOFT_PCT))
        else:
            shot_soft[s] = film_soft
            borrowed.append(s)

    still = _still_by_anchors(jobs, span, lap, ruler, p, spec)
    sharp = _resharp(st, spec, ruler, jobs, lap, still, p, args)

    rows, keep, unmeasured = [], [], 0
    for f, j in sorted(jobs.items()):
        src = p["sharp"] if f in sharp else p["built"]
        path = f"{src}/{f:06d}.yuv"
        if not os.path.exists(path):
            continue
        buf = Y.read_yuv(path, spec)
        g = ruler.small(buf)
        rlap = ruler.lapvar(g)
        prev_g = _neighbour_small(ruler, p, spec, f - 1, plan, sharp)
        next_g = _neighbour_small(ruler, p, spec, f + 1, plan, sharp)
        floor = shot_soft[sidx[f]]
        # Softness is judged against the two real frames the rebuild was built
        # from. For a hole those are its own neighbours; for a retime they are
        # the survivors either side, which is what it should resemble.
        soft = rlap / max(0.5 * (lap[j[1]] + lap[j[2]]), 1e-9)
        if prev_g is None or next_g is None:
            # No reading, so no pass. A frame that cannot be measured is reverted,
            # never waved through on a missing number.
            ratio, measurable = float("nan"), False
            unmeasured += 1
        else:
            gg = g.astype(np.float32)
            sp = float(np.abs(next_g - prev_g).mean())
            ratio = 0.5 * (float(np.abs(gg - prev_g).mean())
                           + float(np.abs(next_g - gg).mean())) / max(sp, 1e-9)
            measurable = sp > 0
        halo = sharp.get(f, {}).get("halo_ratio", 0.0)
        rings = halo > HALO_CUT
        ok = (measurable and not still[f] and not rings
              and soft >= floor and ratio <= ratio_cut)
        if ok:
            keep.append(f)
        rows.append(dict(frame=f, mode=j[4], soft=round(soft, 4), floor=round(floor, 4),
                         ratio=(round(ratio, 4) if measurable else None),
                         span=round(float(span[f]), 3),
                         still=still[f], rings=bool(rings),
                         amount=sharp.get(f, {}).get("amount", 0.0), keep=bool(ok)))
    if unmeasured:
        print(f"\n{unmeasured} rebuilds could not be measured against their neighbours "
              f"and were reverted")

    keep = _shot_verdicts(st, rows, keep, plan, sidx, shot_soft, ratio_cut, span)
    json.dump(rows, open(p["gate"], "w"), indent=0)
    json.dump(sorted(keep), open(p["keep"], "w"))
    _report_gate(rows, keep, jobs, ratio_cut, borrowed, sidx)
    return keep


def _still_by_anchors(jobs, span, lap, ruler, p, spec):
    """Is the subject really still where this frame is being rebuilt?

    `span[f]` is |next minus previous| in the ORIGINAL, and inside a hole the
    frames either side ARE the frozen copies the repair exists to replace. So
    for any stall two frames long or more, the interior frame reads as a shot
    that is not moving and is reverted, every time, by construction. Measured
    on two 11 second generated clips: every 2 frame stall lost its first frame
    that way (34, 58, 82, 106, 130), the second was kept, and the hitch
    survived at the same rhythm one frame shorter.

    Judge a hole across the two REAL frames it was built from instead, which is
    what the softness test already does a few lines below and for the same
    reason. Normalise to the 2 frame distance `span` and MOVE_FLOOR are stated
    in, so the floor keeps its meaning. If the anchors themselves barely
    differ, the subject genuinely is still and reverting is right.
    """
    out = {}
    for f, j in jobs.items():
        a, b = int(j[1]), int(j[2])
        if j[4] != "fill" or b <= a:
            out[f] = bool(span[f] < MOVE_FLOOR)      # retime: judged shot wide
            continue
        try:
            ga = ruler.small(Y.read_yuv(f"{p['src_yuv']}/{a:06d}.yuv", spec))
            gb = ruler.small(Y.read_yuv(f"{p['src_yuv']}/{b:06d}.yuv", spec))
        except OSError:
            out[f] = bool(span[f] < MOVE_FLOOR)      # unreadable: old reading
            continue
        travel = float(np.abs(gb.astype(np.float32) - ga.astype(np.float32)).mean())
        out[f] = bool(travel * 2.0 / (b - a) < MOVE_FLOOR)
    return out


def _shot_verdicts(st, rows, keep, plan, sidx, shot_soft, ratio_cut, span, fail_share=0.25):
    """A retimed shot is kept or reverted whole. A hole is kept or reverted alone.

    A cadence rebuild rewrites the shot's whole timeline. Keeping the frames that
    passed and dropping the ones that did not would leave the survivors sitting
    at their new positions with gaps where the new frames should be, which is a
    worse stutter than the one being repaired. So the shot is judged as one
    thing, and if it does not carry, every frame of it goes back to the source.
    """
    by_shot = {}
    for r in rows:
        if r["mode"] == "rebuild":
            by_shot.setdefault(sidx[r["frame"]], []).append(r)
    if not by_shot:
        return keep
    keep = set(keep)
    for s, rr in sorted(by_shot.items()):
        failing = sum(1 for r in rr if not r["keep"]) / len(rr)
        shot_span = float(np.median([span[r["frame"]] for r in rr]))
        verdict = failing <= fail_share and shot_span >= MOVE_FLOOR
        why = (f"{failing * 100:.0f}% of its frames failed" if failing > fail_share
               else "the shot is not moving" if shot_span < MOVE_FLOOR else "carries")
        print(f"  shot {s}: cadence rebuild {'KEPT' if verdict else 'REVERTED WHOLE'}, {why}")
        for r in rr:
            r["keep"] = bool(verdict)
            (keep.add if verdict else keep.discard)(r["frame"])
        # The survivors move too, so their new slots ride on the same verdict.
        for f, j in plan.items():
            if j[4] == "copy" and sidx[f] == s:
                (keep.add if verdict else keep.discard)(f)
    return sorted(keep)


def _flank(f, skip, sidx, b):
    """Nearest photographed frame either side of f, inside f's own shot."""
    s = sidx[f]
    lo, hi = b[s], b[s + 1]
    p = f - 1
    while p >= lo and p in skip:
        p -= 1
    q = f + 1
    while q < hi and q in skip:
        q += 1
    return (p, q) if p >= lo and q < hi else (None, None)


_small_cache = {}


def _small_of(ruler, path, spec):
    if path in _small_cache:
        return _small_cache[path]
    if not os.path.exists(path):
        return None
    g = ruler.small(Y.read_yuv(path, spec)).astype(np.float32)
    if len(_small_cache) > 64:
        _small_cache.clear()
    _small_cache[path] = g
    return g


def _real_small(ruler, yuv_dir, spec, f):
    """A source frame at viewing scale, down the path the ruler was proved on."""
    return _small_of(ruler, f"{yuv_dir}/{int(f):06d}.yuv", spec)


def _neighbour_small(ruler, p, spec, f, plan, sharp):
    """Whatever the delivered file will actually hold at slot f.

    Judging a rebuild against the source's own neighbour is right when the
    neighbour survives untouched, and wrong inside a shot whose cadence is being
    rebuilt, where the source neighbour is a frozen repeat that will not be in
    the delivery at all and the survivor that will be there has moved.
    """
    j = plan.get(f)
    if j is None:
        return _real_small(ruler, p["src_yuv"], spec, f)
    if j[4] == "copy":
        return _real_small(ruler, p["src_yuv"], spec, j[1])
    for d in (p["sharp"] if f in sharp else p["built"], p["built"]):
        g = _small_of(ruler, f"{d}/{int(f):06d}.yuv", spec)
        if g is not None:
            return g
    return None


def _report_gate(rows, keep, jobs, ratio_cut, borrowed, sidx):
    rev = [r for r in rows if not r["keep"]]
    soft_fail = [r for r in rev if not r["still"] and not r["rings"] and r["soft"] < r["floor"]]
    off_path = [r for r in rev if r["ratio"] is not None and not r["still"]
                and not r["rings"] and r["soft"] >= r["floor"] and r["ratio"] > ratio_cut]
    moved = len(keep) - sum(1 for r in rows if r["keep"])
    print(f"\nkept {sum(1 for r in rows if r['keep'])} of {len(rows)} synthesised frames"
          + (f", plus {moved} real frames moving to new slots" if moved else ""))
    print(f"  reverted, shot not moving  : {sum(1 for r in rev if r['still'])}")
    print(f"  reverted, ringing          : {sum(1 for r in rev if r['rings'] and not r['still'])}")
    print(f"  reverted, too soft         : {len(soft_fail)}")
    print(f"  reverted, sitting off path : {len(off_path)}")
    k = [r for r in rows if r["keep"]]
    if k:
        print(f"  kept: softness median {np.median([r['soft'] for r in k]):.3f}, "
              f"worst {min(r['soft'] for r in k):.3f}; "
              f"position median {np.median([r['ratio'] for r in k]):.3f}")
    if borrowed:
        named = sorted({s for s in borrowed if any(sidx[r["frame"]] == s for r in rows)})
        if named:
            print(f"  shots judged on the film wide floor, having too few clean frames "
                  f"of their own: {named}")


def _resharp(st, spec, ruler, jobs, lap, still, p, args):
    """Put back the detail a rebuild loses, rather than throwing the frame away.

    An unsharp mask on luma only, the amount solved so the frame's sharpness at
    viewing scale matches the mean of the two real frames either side. Never
    above them: the aim is to remove a difference, not to add sharpening the film
    does not have. Chroma is untouched, being half size and carrying no detail
    worth recovering.

    Guarded on ringing against the real frames either side of that same frame, so
    a halo cannot be traded for a number.
    """
    if args.no_resharp:
        return {}
    os.makedirs(p["sharp"], exist_ok=True)
    todo = [f for f in sorted(jobs) if not still[f]]
    print(f"\nrestoring detail on {len(todo)} rebuilds "
          f"({len(jobs) - len(todo)} skipped, their shot is not moving)")
    out = {}
    for i, f in enumerate(todo, 1):
        built = f"{p['built']}/{f:06d}.yuv"
        if not os.path.exists(built):
            continue
        buf = Y.read_yuv(built, spec)
        y = np.frombuffer(buf[:spec.ysize], np.uint8).reshape(spec.height, spec.width).astype(np.float32)
        q0, q1 = jobs[f][1], jobs[f][2]
        target = 0.5 * (lap[q0] + lap[q1])
        cur = ruler.lapvar(ruler.small(buf))
        amount = _solve_amount(ruler, buf, y, target, cur, args.max_sharpen)
        sharpened = _sharpen(y, amount) if amount > 0 else y
        got = ruler.small(buf, y_override=sharpened)
        hs = _halo(got)
        hn = np.mean([_halo(_real_small(ruler, p["src_yuv"], spec, q)) for q in (q0, q1)
                      if _real_small(ruler, p["src_yuv"], spec, q) is not None] or [1e-9])
        if amount > 0 and hs > hn * HALO_CUT:
            amount *= 0.5                     # back off rather than ship a ring
            sharpened = _sharpen(y, amount)
            got = ruler.small(buf, y_override=sharpened)
            hs = _halo(got)
        with open(f"{p['sharp']}/{f:06d}.yuv", "wb") as fh:
            fh.write(np.clip(sharpened + 0.5, 0, 255).astype(np.uint8).tobytes()
                     + bytes(buf[spec.ysize:]))
        out[f] = dict(amount=round(float(amount), 4),
                      before=round(cur / max(target, 1e-9), 4),
                      after=round(ruler.lapvar(got) / max(target, 1e-9), 4),
                      halo_ratio=round(float(hs / max(hn, 1e-9)), 4))
        if i % 20 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
    if out:
        am = np.array([r["amount"] for r in out.values()])
        bf = np.array([r["before"] for r in out.values()])
        af = np.array([r["after"] for r in out.values()])
        print(f"  sharpness against the neighbours: before median {np.median(bf):.3f} "
              f"-> after median {np.median(af):.3f}")
        print(f"  amount: median {np.median(am):.3f}, max {am.max():.3f}, "
              f"at the cap: {int((am >= args.max_sharpen).sum())}")
    return out


def _sharpen(y, amount, sigma=1.2):
    import cv2
    return np.clip(y + amount * (y - cv2.GaussianBlur(y, (0, 0), sigma)), 0, 255)


def _halo(gray):
    import cv2
    f = gray.astype(np.float32)
    ring = np.ones((5, 5), np.uint8)
    ring[2, 2] = 0
    hi = cv2.dilate(f, ring)
    lo = cv2.erode(f, ring)
    return float(((f > hi + 2) | (f < lo - 2)).mean())


def _solve_amount(ruler, buf, y, target, cur, cap):
    """The square root of the Laplacian variance is very nearly linear in the
    amount over this range, so two probes place it and one check confirms it."""
    if cur >= target or target <= 0:
        return 0.0
    probe = 0.15
    lp = ruler.lapvar(ruler.small(buf, y_override=_sharpen(y, probe)))
    slope = (np.sqrt(lp) - np.sqrt(cur)) / probe
    if slope <= 0:
        return 0.0
    a = float(np.clip((np.sqrt(target) - np.sqrt(cur)) / slope, 0.0, cap))
    got = ruler.lapvar(ruler.small(buf, y_override=_sharpen(y, a)))
    for _ in range(3):
        if abs(got / target - 1) < 0.02 or a >= cap:
            break
        a = float(np.clip(a * np.sqrt(target / max(got, 1e-9)), 0.0, cap))
        got = ruler.lapvar(ruler.small(buf, y_override=_sharpen(y, a)))
    return a


# ---------------------------------------------------------------- render


def cmd_render(args):
    p = work_paths(args.work)
    st = load_state(args.work)
    spec = spec_of(st)
    keep = set(json.load(open(p["keep"]))) if os.path.exists(p["keep"]) else set()
    plan = {int(j[0]): j for j in json.load(open(p["jobs"]))} if os.path.exists(p["jobs"]) else {}
    if not keep:
        print("no repairs kept, nothing to splice")
    have_sharp = os.path.isdir(p["sharp"])

    vf = []
    if args.vf:
        # An explicit chain, for a film where the colour repair is not one LUT
        # over the whole running time. cgvideo builds exactly this shape for the
        # grader: one lut3d per range, each switched on over its own frames
        # through the filter's own timeline `enable`. It is here so that a film
        # with several corrected ranges still gets ONE decode and ONE encode,
        # which is the rule the whole track is built on.
        vf.append(args.vf)
    elif args.lut:
        vf.append(f"lut3d=file={args.lut}:interp=tetrahedral")
    enc = [FFMPEG, "-y", "-v", "error", "-stats",
           "-f", "rawvideo", "-pix_fmt", "yuv420p",
           "-s", f"{spec.width}x{spec.height}", "-r", f"{st['fps']}",
           *spec.tags, "-i", "-"]
    if st["has_audio"] and not args.no_audio:
        enc += ["-i", st["input"], "-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
    if vf:
        enc += ["-vf", ",".join(vf)]
    enc += ["-c:v", args.codec, "-crf", str(args.crf), "-preset", args.preset,
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", spec.colorspace, "-color_range", spec.color_range,
            "-movflags", "+faststart", args.out]

    print(f"splicing {len(keep)} rebuilt frames and encoding"
          + (f", LUT {os.path.basename(args.lut)} in the same pass" if args.lut else ""))
    proc = subprocess.Popen(enc, stdin=subprocess.PIPE)
    built = moved = 0
    try:
        for i, buf in Y.decode(st["input"], spec):
            if i in keep:
                j = plan.get(i)
                if j is not None and j[4] == "copy":
                    # A survivor moving to its true slot in a retimed shot.
                    path = f"{p['src_yuv']}/{int(j[1]):06d}.yuv"
                    if os.path.exists(path):
                        buf = Y.read_yuv(path, spec)
                        moved += 1
                else:
                    for d in ((p["sharp"], p["built"]) if have_sharp else (p["built"],)):
                        path = f"{d}/{i:06d}.yuv"
                        if os.path.exists(path):
                            buf = Y.read_yuv(path, spec)
                            built += 1
                            break
            proc.stdin.write(buf)
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        sys.exit(f"the encoder failed with code {proc.returncode}")
    print(f"wrote {args.out}: {built} frames rebuilt, {moved} real frames moved")
    print("Every frame reached the encoder as raw yuv down one path, the repaired "
          "ones included, so a correct rebuild cannot shift the colour.")


# ---------------------------------------------------------------- verify


def cmd_verify(args):
    """Four questions, in the order they matter."""
    p = work_paths(args.work)
    st = load_state(args.work)
    spec = spec_of(st)
    jobs = {int(j[0]) for j in json.load(open(p["jobs"]))} if os.path.exists(p["jobs"]) else set()
    keep = set(json.load(open(p["keep"]))) if os.path.exists(p["keep"]) else set()
    b = shot_bounds(st)
    near_cut = set()
    for c in b[1:-1]:
        near_cut |= {c - 2, c - 1, c, c + 1, c + 2}

    print("1  is the colour flat across the repairs")
    cols = [("delivered", args.delivered)]
    if args.against:
        cols.append(("against", args.against))
    results = {}
    for label, path in cols:
        results[label] = _flicker(path, spec, keep or jobs, near_cut)
    print(f"   {'':34s}" + "".join(f"{lab:>14s}" for lab, _ in cols))
    for key, name in (("real_median", "real frames, median offset"),
                      ("rebuilt_median", "rebuilt frames, median offset"),
                      ("ratio", "rebuilt over real"),
                      ("over", "rebuilt above the real 99th")):
        print(f"   {name:34s}" + "".join(f"{results[lab][key]:>14}" for lab, _ in cols))
    print("   offsets are code levels of whole frame colour shift against the two "
          "neighbours")
    print("   A rebuild is an average of two frames, so it should sit at or below the")
    print(f"   real median. Anything past {FLAT_RATIO:.1f} times it carries something the "
          f"film does not.")

    d = results["delivered"]
    print(f"   {'VERDICT: the colour is flat' if d['ratio_n'] <= FLAT_RATIO else 'VERDICT: FAIL, the rebuilds carry a cast'}")
    if args.against:
        a = results["against"]
        if a["ratio_n"] <= FLAT_RATIO:
            print("   WARNING: this instrument reads clean on the reference file too. A "
                  "test that finds\n            nothing on a file known to be bad proves "
                  "nothing. Fix the test first.")
        else:
            print(f"   the instrument reads {a['ratio_n']:.2f} on the reference file, so "
                  f"it is live")
    elif d["ratio_n"] <= FLAT_RATIO:
        print("   Run it again with --against on a file known to carry the fault before\n"
              "   trusting that, or the reading is untested.")

    print("\n2  is the stutter actually gone")
    ruler = Y.Ruler(spec, st["view_width"])
    _, diff_out, _, area_out = ruler.scan(args.delivered, verbose=False)
    st_out = dict(st)
    st_out["nb_frames"] = min(len(diff_out), st["nb_frames"])
    # the delivered file is judged by BOTH readings, exactly as the source was.
    # Judging the source on two and the delivery on one would let a repair that
    # only the area reading can see come back clean whatever it did.
    frozen_out = sum(len(c["repeats"])
                     for c in census_frozen(st_out, diff_out, area_out).values())
    frozen_src = sum(len(c["repeats"]) for c in
                     json.load(open(p["census"])).values()) if os.path.exists(p["census"]) else -1
    print(f"   frozen frames: source {frozen_src}, delivered {frozen_out}")
    if frozen_src >= 0 and frozen_out >= frozen_src:
        print("   WARNING: no fewer frozen frames than the source. The repair did not land.")

    if args.approved:
        print("\n3  do the approved sections still match")
        _approved(args, st, spec, jobs)


def _flicker(path, spec, rebuilt, near_cut):
    """Whole frame colour offset of every frame against its two neighbours.

    This is the instrument that would have caught that delivery before it
    went out. A frame built in the wrong colour space carries a constant cast,
    and among untouched frames that reads as a flash at the repair rate. Real
    frames give the floor, so no threshold has to be invented.
    """
    means, order = [], []
    for i, buf in Y.decode(path, spec):
        y, u, v = Y.planes(buf, spec)
        means.append((float(y.mean()), float(u.mean()), float(v.mean())))
        order.append(i)
    m = np.array(means)
    n = len(m)
    dev = np.zeros(n)
    for i in range(1, n - 1):
        mid = 0.5 * (m[i - 1] + m[i + 1])
        dev[i] = float(np.abs(m[i] - mid).mean())
    reb = sorted(f for f in rebuilt if 0 < f < n - 1)
    real = [f for f in range(1, n - 1) if f not in rebuilt and f not in near_cut]
    if not real or not reb:
        return dict(real_median="n/a", rebuilt_median="n/a", ratio="n/a", over="n/a",
                    ratio_n=0.0, over_n=0, n=len(reb))
    rm = float(np.median(dev[real]))
    bm = float(np.median(dev[reb]))
    cut = float(np.percentile(dev[real], 99))
    over = int(sum(1 for f in reb if dev[f] > cut))
    return dict(
        real_median=f"{rm:.3f}",
        rebuilt_median=f"{bm:.3f}",
        ratio=f"{bm / max(rm, 1e-9):.2f}x",
        over=f"{over} of {len(reb)}",
        ratio_n=bm / max(rm, 1e-9), over_n=over, n=len(reb))


def _approved(args, st, spec, jobs):
    """The sections the client already signed off must come out of this render
    exactly as they came out of the one they signed off.

    This is the check a rebuild is most likely to break by accident, because a
    section that was only reused before gets re encoded now.
    """
    lo, hi = [int(x) for x in args.approved.split(",")]
    rng = np.random.default_rng(1)
    pick = [f for f in rng.choice(range(lo, hi), min(10, hi - lo), replace=False) if f not in jobs]
    rows = []
    for f in sorted(pick):
        s = _one_frame(st["input"], spec, int(f))
        d = _one_frame(args.delivered, spec, int(f))
        if s is None or d is None:
            continue
        rows.append((int(f), float(np.abs(d.astype(np.float32) - s.astype(np.float32)).mean())))
    if not rows:
        print("   no untouched frames in that range to compare")
        return
    print(f"   {'frame':>7} {'levels from the source':>24}")
    for f, d in rows:
        print(f"   {f:7d} {d:24.4f}")
    print(f"   worst {max(r[1] for r in rows):.4f} levels across {len(rows)} frames")


def _one_frame(path, spec, n):
    p = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{n})",
         "-vsync", "0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        capture_output=True)
    if len(p.stdout) < spec.frame_bytes:
        return None
    return Y.luma(p.stdout, spec)


# ---------------------------------------------------------------- holdout


def score(rebuilt, truth, previous=None):
    """Three numbers, because scoring on error alone selects for blur.

    error   mean absolute difference from the held out real frame. Lower is
            better, AND ON ITS OWN IT PREFERS A BLURRY ANSWER, because a soft
            rebuild sits closer to the truth on average than a crisp one whose
            detail is a pixel out of place. A holdout scored on error alone
            therefore picks the softest method available, every time. That is
            how a rebuild once shipped measuring 10 to 25 per cent softer than
            its neighbours, reading as the subject twitching sideways for a
            single frame, which no measurement of motion can see.
    detail  gradient energy as a fraction of the real frame's. 1.0 is right.
            Below 1 is soft. Above 1 is usually ghosting rather than extra
            sharpness: a double edge carries more high frequency than a single
            one, which is why Laplacian variance alone once reported rebuilt
            frames holding 178 per cent of the real frame's detail.
    placed  correlation of the gradient MAP with the real frame's. This is the
            one that separates a crisp frame in the right place from a crisp
            frame in the wrong place, which `detail` alone cannot do.
    """
    a = np.asarray(rebuilt, dtype=np.float32)
    b = np.asarray(truth, dtype=np.float32)
    err = float(np.abs(a - b).mean())

    def grad(x):
        if x.ndim == 3:
            x = x.mean(axis=2)
        gx = np.diff(x, axis=1)[:-1, :]
        gy = np.diff(x, axis=0)[:, :-1]
        return np.hypot(gx, gy)

    ga, gb = grad(a), grad(b)
    detail = float(ga.mean() / max(gb.mean(), 1e-6))
    va, vb = ga.ravel() - ga.mean(), gb.ravel() - gb.mean()
    placed = float((va * vb).sum() / max(np.sqrt((va * va).sum() * (vb * vb).sum()), 1e-9))
    out = {"error": err, "detail": detail, "placed": placed}
    if previous is not None:
        # what the file already does, which is repeat the previous frame. A
        # rebuild that does not beat this is not worth the softness it costs.
        out["vs_previous"] = float(np.abs(b - np.asarray(previous, np.float32)).mean())
    return out


def judder(steps):
    """How rough a shot plays: movement that ALTERNATES frame to frame.

    Movement is allowed to speed up and slow down. What the eye reads as judder
    is a step that is short, then long, then short. Measured as each step's
    distance from the average of its two neighbours, over the shot's own mean
    step, so it is comparable between shots. A shot with no stalls at all gives
    the floor this can reach on real footage, which is the only honest baseline.
    """
    d = np.asarray(steps, dtype=np.float64)
    if len(d) < 6 or d.mean() < 0.2:
        return None
    return float(np.abs(d[1:-1] - (d[:-2] + d[2:]) / 2).mean() / d.mean())


def cmd_holdout(args):
    """Hide a real frame, rebuild it, and score the rebuild against it.

    There is no ground truth for a synthesised frame, so this makes one. The test
    is deliberately harder than the job, interpolating across a whole frame gap
    where the real work crosses a fraction of one, so what it reports is a floor
    on the quality and not a ceiling.

    Run this before committing to a rebuild on unfamiliar footage. If motion
    compensation does not beat repeating the previous frame here, it will not
    beat it in the film either, and the honest answer is to leave the stutter in.
    """
    ok, why = _flow_ready()
    if not ok:
        sys.exit(why)
    import cgflow
    load_state(args.work)                 # fails loudly if census has not run
    p = work_paths(args.work)
    pngs = sorted(f for f in os.listdir(p["png"]) if f.endswith(".png")) if os.path.isdir(p["png"]) else []
    if len(pngs) < 3:
        sys.exit("no stills to test on. Run build first, or point --work at a finished job.")
    paths = [f"{p['png']}/{f}" for f in pngs][:args.limit + 2]
    from PIL import Image

    def arr(q):
        return np.asarray(Image.open(q).convert("RGB"), dtype=np.float32)

    def psnr(a, c):
        m = ((a - c) ** 2).mean()
        return 99.0 if m < 1e-9 else float(10 * np.log10(255.0 ** 2 / m))

    rows, sc = [], []
    for i in range(1, len(paths) - 1):
        A, T, B = arr(paths[i - 1]), arr(paths[i]), arr(paths[i + 1])
        mid = cgflow.between(cgflow.load_rgb(paths[i - 1]), cgflow.load_rgb(paths[i + 1]), 0.5)
        M = mid[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255.0
        rows.append((psnr(T, A), psnr(T, (A + B) / 2), psnr(T, M)))
        sc.append((score(M, T), score((A + B) / 2, T)))
    r = np.array(rows)
    print(f"\n{len(rows)} held out frames, PSNR against the real frame, higher is better")
    print(f"  repeat the previous frame, which is what the file does now : {r[:, 0].mean():6.2f} dB   worst {r[:, 0].min():6.2f}")
    print(f"  plain blend of the two neighbours, no motion               : {r[:, 1].mean():6.2f} dB   worst {r[:, 1].min():6.2f}")
    print(f"  motion compensated                                        : {r[:, 2].mean():6.2f} dB   worst {r[:, 2].min():6.2f}")
    win = int((r[:, 2] > r[:, 0]).sum())
    print(f"  motion compensation beats repeating on {win} of {len(rows)} frames")

    # PSNR is an error measure, and error alone prefers the blurriest answer, so
    # it can never be the whole verdict. See `score`.
    det = float(np.mean([a["detail"] for a, _ in sc]))
    pla = float(np.mean([a["placed"] for a, _ in sc]))
    bdet = float(np.mean([b["detail"] for _, b in sc]))
    print("\n  detail held and edge placement, which PSNR cannot see")
    print(f"  motion compensated : detail {det:5.3f} of the real frame   placed {pla:6.4f}")
    print(f"  plain blend        : detail {bdet:5.3f}")
    if det < 0.90:
        print(f"  SOFT. The rebuild holds {det:5.3f} of the real frame's detail. Below 0.90 it\n"
              "  reads as a one frame twitch even when the motion measures clean.")
    if det > 1.15:
        print(f"  GHOSTING. {det:5.3f} is not extra sharpness, it is a double edge.")

    if win < 0.6 * len(rows) or det < 0.90:
        print("  This footage is not a candidate for a rebuild. Leave the stutter in "
              "rather than\n  trading it for softness.")


# ---------------------------------------------------------------- one shot


def cmd_repair(args):
    cmd_census(args)
    cmd_plan(args)
    cmd_build(args)
    cmd_gate(args)
    cmd_render(args)
    args.delivered = args.out
    cmd_verify(args)


# ---------------------------------------------------------------- cli


def main():
    ap = argparse.ArgumentParser(
        description="Find frozen frames, rebuild them in the film's own colour space, "
                    "prove the rebuild, splice it in.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(q, need_input=True):
        if need_input:
            q.add_argument("input")
        q.add_argument("--work", required=True, help="working directory, reused across steps")
        return q

    c = common(sub.add_parser("census", help="find the frozen frames, shot by shot"))
    c.add_argument("--cuts", help="explicit cut frames, comma separated")
    c.add_argument("--panel", help="x0,x1 of the panel to repair, for split screens")
    c.add_argument("--color-range", choices=["tv", "pc"], help="override the range tag")
    c.add_argument("--view-width", type=int, default=1920,
                   help="scale everything is measured at, default 1920")
    c.set_defaults(func=cmd_census)

    q = common(sub.add_parser("plan", help="decide, per shot, holes or cadence"), False)
    q.add_argument("--force-mode", default=None,
                   help="override the decision per shot, e.g. 0:rebuild,2:fill")
    q.set_defaults(func=cmd_plan)

    q = common(sub.add_parser("build", help="synthesise the frames, never leaving yuv"), False)
    q.set_defaults(func=cmd_build)

    q = common(sub.add_parser("gate", help="judge every rebuild on the film's own rulers"), False)
    q.add_argument("--no-resharp", action="store_true", help="discard soft rebuilds instead of restoring them")
    q.add_argument("--max-sharpen", type=float, default=0.35)
    q.set_defaults(func=cmd_gate)

    q = common(sub.add_parser("render", help="splice the kept rebuilds and encode"), False)
    q.add_argument("--out", required=True)
    q.add_argument("--lut", help="a .cube applied in the same pass, so there is one encode")
    q.add_argument("--vf", help="an explicit filter chain instead of --lut, for a film "
                                "whose colour repair is several LUTs over different "
                                "frame ranges. Still one decode and one encode.")
    q.add_argument("--crf", type=int, default=16)
    q.add_argument("--preset", default="medium")
    q.add_argument("--codec", default="libx264")
    q.add_argument("--no-audio", action="store_true")
    q.set_defaults(func=cmd_render)

    q = common(sub.add_parser("verify", help="prove the colour is flat and the stutter is gone"), False)
    q.add_argument("--delivered", required=True)
    q.add_argument("--against", help="a file known to carry the fault, to prove the test is live")
    q.add_argument("--approved", help="lo,hi frame range the client already signed off")
    q.set_defaults(func=cmd_verify)

    q = common(sub.add_parser("holdout", help="prove motion compensation beats repeating, here"), False)
    q.add_argument("--limit", type=int, default=40)
    q.set_defaults(func=cmd_holdout)

    r = common(sub.add_parser("repair", help="census, plan, build, gate, render, verify"))
    r.add_argument("--out", required=True)
    r.add_argument("--cuts")
    r.add_argument("--panel")
    r.add_argument("--color-range", choices=["tv", "pc"])
    r.add_argument("--view-width", type=int, default=1920)
    r.add_argument("--lut")
    r.add_argument("--vf")
    r.add_argument("--crf", type=int, default=16)
    r.add_argument("--preset", default="medium")
    r.add_argument("--codec", default="libx264")
    r.add_argument("--no-audio", action="store_true")
    r.add_argument("--no-resharp", action="store_true")
    r.add_argument("--max-sharpen", type=float, default=0.35)
    r.set_defaults(func=cmd_repair, force_mode=None, against=None, approved=None)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
