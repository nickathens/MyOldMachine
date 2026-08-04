#!/usr/bin/env python3
"""colorgrade: unattended colour grading for a whole video.

    cg.py grade IN.mp4 --look kodak2383 --out OUT.mp4

Cuts the file into shots, measures each one, balances them to each other,
lays one look over the top, checks the result is consistent, renders, and
writes a report plus a before and after contact sheet.

Every shot's grade is also written out as a .cube LUT, so the same result can
be handed to a colourist and dropped straight onto the clip in Resolve.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgcore as C
import cgvideo as V
import cganalyze as A

HERE = os.path.dirname(os.path.abspath(__file__))
LOOKS_DIR = os.path.join(os.path.dirname(HERE), "looks")


# ---------------------------------------------------------------- helpers


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def contact_sheet(media, shots, lut_paths, out_path, cols=4, tile_w=420):
    """One tile per shot, ungraded on top of graded. What Nick actually looks at."""
    from PIL import Image, ImageDraw

    tiles = []
    for s in shots:
        t = (s.start_t + s.end_t) / 2.0
        before = V.grab_frame(media, t, width=tile_w)
        g = lut_paths.get(s.index)
        if g is not None and isinstance(g, tuple):
            grade = g[1]
            after = C.apply_grade(before, grade)
        else:
            after = before
        tiles.append((s, before, after))

    if not tiles:
        return None
    th = tiles[0][1].shape[0]
    pad, label_h = 6, 18
    cell_h = th * 2 + pad + label_h
    cols = min(cols, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    W = cols * (tile_w + pad) + pad
    H = rows * (cell_h + pad) + pad
    canvas = np.zeros((H, W, 3), dtype=np.float32)

    for i, (s, b, a) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (tile_w + pad)
        y = pad + r * (cell_h + pad) + label_h
        canvas[y:y + th, x:x + tile_w] = b
        canvas[y + th + pad:y + th + pad + th, x:x + tile_w] = a

    img = Image.fromarray(np.clip(canvas * 255 + 0.5, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for i, (s, b, a) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (tile_w + pad)
        y = pad + r * (cell_h + pad)
        d.text((x + 2, y + 3),
               f"shot {s.index + 1}   {s.start_t:.2f}s  {s.duration:.2f}s   "
               f"top ungraded / bottom graded", fill=(200, 200, 200))
    img.save(out_path)
    return out_path


def fmt_stats_table(stats, title):
    lines = [f"\n{title}",
             f"{'shot':>5} {'dur':>6} {'black':>7} {'mid':>7} {'white':>7} "
             f"{'L*':>6} {'a*':>6} {'b*':>6} {'skin%':>6} {'clip%':>6} "
             f"{'flat':>6} {'wbconf':>6}"]
    for s in stats:
        lines.append(
            f"{s.index + 1:>5} {s.duration:>6.2f} {s.lum_pct['0.5']:>7.3f} "
            f"{s.lum_pct['50']:>7.3f} {s.lum_pct['99.5']:>7.3f} "
            f"{s.lab_mean[0]:>6.1f} {s.lab_mean[1]:>6.1f} {s.lab_mean[2]:>6.1f} "
            f"{s.skin_frac * 100:>6.1f} {s.clip_hi * 100:>6.2f} "
            f"{s.flatness:>6.2f} {s.illum_conf:>6.2f}  {'gfx' if s.is_graphics else ''}")
    return "\n".join(lines)


def fmt_judge(c: A.Consistency, label):
    return (f"\n{label}\n"
            f"  worst adjacent difference   dE2000 {c.max_adjacent_de:5.2f}  "
            f"(shots {c.worst_pair[0] + 1} and {c.worst_pair[1] + 1})\n"
            f"  average adjacent difference dE2000 {c.mean_adjacent_de:5.2f}\n"
            f"  black point spread   {c.black_spread:.3f} code\n"
            f"  mid point spread     {c.mid_spread:.3f} code\n"
            f"  white point spread   {c.white_spread:.3f} code\n"
            f"  white balance spread {c.illum_spread:.3f}\n"
            f"  skin hue spread      {c.skin_angle_spread:.1f} degrees\n"
            f"  judged over {c.n_judged} camera shots"
            + (f", {c.n_graphics} graphics shots excluded" if c.n_graphics else "")
            + f"\n  verdict: {c.verdict}")


# ---------------------------------------------------------------- commands


def cmd_analyze(args):
    media = V.probe(args.input)
    log(f"{media.width}x{media.height} {media.fps:.3f} fps  {media.duration:.2f}s  "
        f"{media.nb_frames} frames  {media.codec}/{media.pix_fmt}  "
        f"trc={media.color_transfer or 'unset'}")
    t0 = time.time()
    shots = V.detect_shots(media, threshold=args.threshold, min_len_frames=args.min_shot)
    log(f"shot detection: {len(shots)} shots in {time.time() - t0:.1f}s")
    t0 = time.time()
    samples = V.collect_shot_samples(media, shots, per_shot=args.samples, width=args.width)
    log(f"sampling: {time.time() - t0:.1f}s")
    stats = [A.measure(samples[s.index], index=s.index, duration=s.duration) for s in shots]
    print(fmt_stats_table(stats, "measured, ungraded"))
    print(fmt_judge(A.judge(stats), "consistency, ungraded"))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"media": media.__dict__,
                       "shots": [s.__dict__ for s in shots],
                       "stats": [s.as_dict() for s in stats]}, f, indent=2)
        log(f"wrote {args.json}")
    return 0


def cmd_grade(args):
    media = V.probe(args.input)
    outdir = args.workdir or os.path.join(
        os.path.dirname(os.path.abspath(args.out or args.input)),
        os.path.splitext(os.path.basename(args.input))[0] + "_grade")
    os.makedirs(outdir, exist_ok=True)
    lutdir = os.path.join(outdir, "luts")
    os.makedirs(lutdir, exist_ok=True)

    log(f"{media.width}x{media.height} {media.fps:.3f} fps  {media.duration:.2f}s  "
        f"{media.nb_frames} frames")
    if media.is_log_flagged:
        log(f"NOTE: transfer is flagged {media.color_transfer}, not bt709. "
            f"This grader assumes Rec.709 display footage.")

    # 1. shots -----------------------------------------------------------
    t0 = time.time()
    if args.single_shot:
        shots = [V.Shot(0, 0, media.nb_frames, 0.0, media.duration)]
    elif args.cuts:
        shots = V.shots_from_cuts(media, [int(x) for x in args.cuts.split(",") if x.strip()])
        log(f"shots: {len(shots)} from the cut list given")
    else:
        shots = V.detect_shots(media, threshold=args.threshold, min_len_frames=args.min_shot)
        log(f"shots: {len(shots)} detected in {time.time() - t0:.1f}s")

    # 2. measure ---------------------------------------------------------
    t0 = time.time()
    samples = V.collect_shot_samples(media, shots, per_shot=args.samples, width=args.width)
    stats = [A.measure(samples[s.index], index=s.index, duration=s.duration) for s in shots]
    log(f"measured {len(stats)} shots in {time.time() - t0:.1f}s")

    before_judge = A.judge(stats)
    if args.verbose:
        print(fmt_stats_table(stats, "measured, ungraded"))
    print(fmt_judge(before_judge, "consistency BEFORE grading"))

    # 3. targets and reference -------------------------------------------
    if args.normalize == "off":
        targets = None
    elif args.normalize == "full":
        targets = A.NEUTRAL_TARGETS
    else:
        targets = A.aggregate_targets(stats)
    ref_idx = args.reference - 1 if args.reference else A.pick_reference(stats)
    ref_idx = max(0, min(ref_idx, len(stats) - 1))
    if targets:
        log(f"target  black {targets.black:.3f}  mid {targets.mid:.3f}  "
            f"white {targets.white:.3f}  illum "
            f"{targets.illum[0]:.3f}/{targets.illum[1]:.3f}/{targets.illum[2]:.3f}")
    log(f"reference shot: {ref_idx + 1}")

    # 4. look -------------------------------------------------------------
    look = C.Look() if args.look in ("none", "neutral") else C.load_look(args.look, LOOKS_DIR)
    if args.contrast is not None:
        look.contrast = args.contrast
    if args.saturation is not None:
        look.saturation = args.saturation
    if args.exposure:
        pass  # applied per shot below
    log(f"look: {look.name}")

    caps = A.Caps(exposure_stops=args.cap_exposure, wb_gain=args.cap_wb)

    # 5. derive, judge, and iterate the match strength ---------------------
    # Two passes, because that is the only order that is not self defeating.
    # The mechanical balance (exposure, white balance, levels) goes first. The
    # residual match must then be measured on the ALREADY BALANCED picture,
    # not on the original: computing both from the same untouched numbers and
    # stacking them corrects the same error twice. Measured, on the ground
    # truth harness that mistake pushed the spread from 8.9 up to 14.5.
    match_strength = args.match_strength
    grades, notes_all, predicted = None, None, None
    for attempt in range(args.max_iterations):
        grades, notes_all = {}, {}

        # pass 1: mechanical balance
        for st in stats:
            if targets is None:
                bal, notes = C.Balance(), []
            else:
                bal, notes = A.derive_balance(
                    st, targets, caps, strength=args.balance_strength,
                    do_wb=not args.no_wb, do_exposure=not args.no_exposure,
                    do_levels=not args.no_levels)
            grades[st.index] = C.Grade(balance=bal, look=look)
            notes_all[st.index] = notes

        # what did that actually leave behind
        balanced = {st.index: A.restat(samples[st.index], grades[st.index], st)
                    for st in stats}

        # pass 2: residual match, on the balanced picture
        if targets is not None and not args.no_match:
            ref_bal = balanced[stats[ref_idx].index]
            for st in stats:
                bal = grades[st.index].balance
                bal, n2 = A.match_to_reference(balanced[st.index], ref_bal, bal,
                                               caps, strength=match_strength)
                notes_all[st.index] += n2

        for st in stats:
            grades[st.index].balance.exposure += args.exposure

        predicted = [A.restat(samples[st.index], grades[st.index], st) for st in stats]
        after_judge = A.judge(predicted)
        if after_judge.max_adjacent_de <= A.DE_PASS or match_strength >= 0.95:
            break
        match_strength = min(0.95, match_strength + 0.25)
        log(f"consistency {after_judge.max_adjacent_de:.2f} dE, "
            f"raising the shot match to {match_strength:.2f} and re deriving")

    after_judge = A.judge(predicted)
    if args.verbose:
        print(fmt_stats_table(predicted, "predicted, after grade"))
    print(fmt_judge(after_judge, "consistency AFTER grading (predicted)"))

    capped = {k: v for k, v in notes_all.items() if v}
    if capped:
        print("\ncorrections that hit a safety cap:")
        for k in sorted(capped):
            print(f"  shot {k + 1}: " + "; ".join(capped[k]))

    # 6. bake the LUTs -----------------------------------------------------
    # A 3D LUT is an approximation of the grade, not the grade. Strong looks
    # bend the transfer hard enough that a 33 cube visibly misses. So measure
    # rather than guess: bake, compare against the maths, and step up a size
    # if the miss is bigger than a couple of code levels.
    probe_grade = grades[stats[ref_idx].index]
    if args.lut_size == 0:
        size = 33
        err = lut_bake_error(probe_grade, size)
        if err[0] > args.lut_error_budget:
            err65 = lut_bake_error(probe_grade, 65)
            log(f"33^3 misses the maths by {err[0]:.2f} dE, over the "
                f"{args.lut_error_budget} budget, so baking 65^3 "
                f"({err65[0]:.2f} dE)")
            size, err = 65, err65
            if err[0] > args.lut_error_budget:
                log(f"WARNING: even 65^3 leaves {err[0]:.2f} dE on the worst "
                    f"colour. This look bends the transfer harder than a cube "
                    f"can follow. It is one colour in tens of thousands, but "
                    f"it is not zero.")
    else:
        size = args.lut_size
        err = lut_bake_error(probe_grade, size)

    t0 = time.time()
    lut_paths, lut_pairs = {}, {}
    for st in stats:
        g = grades[st.index]
        lut = C.bake_lut(g, size=size)
        p = os.path.join(lutdir, f"shot_{st.index + 1:03d}.cube")
        C.write_cube(p, lut, size, title=f"{look.name} shot {st.index + 1}")
        lut_paths[st.index] = p
        lut_pairs[st.index] = (p, g)
        with open(os.path.join(lutdir, f"shot_{st.index + 1:03d}.json"), "w") as f:
            f.write(g.to_json())
    log(f"baked {len(lut_paths)} LUTs at {size}^3 in {time.time() - t0:.1f}s")
    log(f"LUT bake error: max {err[0]:.2f} / mean {err[1]:.3f} dE2000  "
        f"(max {err[2]:.2f} code levels of 255)")

    # 7. contact sheet -----------------------------------------------------
    sheet = None
    if not args.no_sheet:
        t0 = time.time()
        sheet = contact_sheet(media, shots, lut_pairs,
                              os.path.join(outdir, "contact_sheet.png"),
                              cols=args.sheet_cols)
        log(f"contact sheet in {time.time() - t0:.1f}s")

    # 8. render ------------------------------------------------------------
    out_video = None
    if not args.no_render:
        out_video = args.out or os.path.join(
            outdir, os.path.splitext(os.path.basename(args.input))[0] + "_graded.mp4")
        t0 = time.time()
        V.render(media, shots, lut_paths, out_video, crf=args.crf, preset=args.preset)
        dt = time.time() - t0
        log(f"rendered in {dt:.1f}s ({media.duration / max(dt, 1e-6):.2f}x realtime)")

    # 9. report ------------------------------------------------------------
    report = {
        "input": os.path.abspath(args.input),
        "output": os.path.abspath(out_video) if out_video else None,
        "look": look.name,
        "normalize": args.normalize,
        "reference_shot": ref_idx + 1,
        "match_strength_used": match_strength,
        "shots": [{"index": s.index + 1, "start_frame": s.start_frame,
                   "end_frame": s.end_frame, "start_t": round(s.start_t, 3),
                   "duration": round(s.duration, 3),
                   "lut": os.path.basename(lut_paths[s.index]),
                   "notes": notes_all.get(s.index, [])} for s in shots],
        "consistency_before": before_judge.as_dict(),
        "consistency_after_predicted": after_judge.as_dict(),
        "lut_size": size,
        "lut_bake_error": {"max_de2000": err[0], "mean_de2000": err[1],
                           "max_code_levels_255": err[2], "mean_code_levels_255": err[3]},
    }
    rp = os.path.join(outdir, "report.json")
    with open(rp, "w") as f:
        json.dump(report, f, indent=2)

    print("\nwrote:")
    print(f"  video   {out_video or '(skipped)'}")
    print(f"  sheet   {sheet or '(skipped)'}")
    print(f"  luts    {lutdir}  ({len(lut_paths)} files)")
    print(f"  report  {rp}")
    return 0


def lut_bake_error(g: C.Grade, size, n=20000, seed=0):
    """Difference between the LUT and the maths it was baked from.

    Reported in dE2000, not in code levels. Measured, not assumed: the raw
    code level error concentrates in the deepest shadows, where log space
    contrast makes the transfer steep and a uniform lattice is coarse. Four
    code levels at black is invisible; four at mid grey is not. dE2000 is the
    metric that knows the difference, and 1.0 is roughly the point where an
    eye can tell two flat patches apart at all.

    Returns (max_de, mean_de, max_code_levels, mean_code_levels).
    """
    rng = np.random.default_rng(seed)
    pts = rng.random((n, 3)).astype(np.float32)
    exact = C.apply_grade(pts, g)
    lut = C.bake_lut(g, size)
    approx = C.apply_lut_trilinear(pts, lut, size)
    d = np.abs(exact - approx)
    de = A.delta_e_2000_vec(C.lin_to_lab(C.code_to_lin(exact)),
                            C.lin_to_lab(C.code_to_lin(approx)))
    return float(de.max()), float(de.mean()), float(d.max()) * 255, float(d.mean()) * 255


def cmd_lut(args):
    look = C.Look() if args.look in ("none", "neutral") else C.load_look(args.look, LOOKS_DIR)
    g = C.Grade(look=look)
    if args.exposure:
        g.balance.exposure = args.exposure
    lut = C.bake_lut(g, size=args.size)
    C.write_cube(args.out, lut, args.size, title=look.name)
    e = lut_bake_error(g, args.size)
    print(f"wrote {args.out}  ({args.size}^3, {args.size ** 3} entries)")
    print(f"bake error: max {e[0]:.2f} / mean {e[1]:.3f} dE2000 "
          f"(max {e[2]:.2f} code levels of 255)")
    return 0


def cmd_looks(args):
    import glob
    for p in sorted(glob.glob(os.path.join(LOOKS_DIR, "*.json"))):
        with open(p) as f:
            d = json.load(f)
        print(f"{d.get('name', os.path.basename(p)):<18} {d.get('description', '')}")
    return 0


def cmd_still(args):
    from PIL import Image
    img = np.asarray(Image.open(args.input).convert("RGB"), dtype=np.float32) / 255.0
    look = C.Look() if args.look in ("none", "neutral") else C.load_look(args.look, LOOKS_DIR)
    g = C.Grade(look=look)
    if args.normalize != "off":
        st = A.measure([img])
        tgt = A.NEUTRAL_TARGETS
        g.balance, notes = A.derive_balance(st, tgt, A.Caps(), strength=args.balance_strength)
        for n in notes:
            log(f"  {n}")
    g.balance.exposure += args.exposure
    out = C.apply_grade(img, g)
    V.write_png(args.out, out)
    print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------- cli


def main(argv=None):
    p = argparse.ArgumentParser(prog="cg", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common_shot_args(q):
        q.add_argument("--threshold", type=float, default=27.0,
                       help="shot detection sensitivity, lower finds more cuts")
        q.add_argument("--min-shot", type=int, default=12,
                       help="minimum shot length in frames")
        q.add_argument("--samples", type=int, default=8,
                       help="frames measured per shot")
        q.add_argument("--width", type=int, default=320,
                       help="analysis width in pixels")

    a = sub.add_parser("analyze", help="measure a video without changing it")
    a.add_argument("input")
    a.add_argument("--json", help="write the full measurement to this file")
    common_shot_args(a)
    a.set_defaults(func=cmd_analyze)

    g = sub.add_parser("grade", help="grade a whole video, unattended")
    g.add_argument("input")
    g.add_argument("--out", help="output video path")
    g.add_argument("--workdir", help="where LUTs, sheet and report go")
    g.add_argument("--look", default="neutral", help="look name or path to a look json")
    g.add_argument("--normalize", choices=["match", "full", "off"], default="match",
                   help="match: pull shots to the video's own centre (default). "
                        "full: pull every shot to absolute neutral targets. "
                        "off: look only, no balancing")
    g.add_argument("--reference", type=int, default=0,
                   help="1 based shot number to match everything to, 0 picks automatically")
    g.add_argument("--balance-strength", type=float, default=1.0)
    g.add_argument("--match-strength", type=float, default=0.65,
                   help="how much of the residual gap to close per shot. "
                        "Measured on the ground truth harness: 0.45 leaves "
                        "1.41 dE, 0.65 leaves 0.97, and it saturates there.")
    g.add_argument("--max-iterations", type=int, default=3)
    g.add_argument("--exposure", type=float, default=0.0, help="extra stops on everything")
    g.add_argument("--contrast", type=float, default=None, help="override the look contrast")
    g.add_argument("--saturation", type=float, default=None)
    g.add_argument("--cap-exposure", type=float, default=1.5)
    g.add_argument("--cap-wb", type=float, default=0.18)
    g.add_argument("--no-wb", action="store_true")
    g.add_argument("--no-exposure", action="store_true")
    g.add_argument("--no-levels", action="store_true")
    g.add_argument("--no-match", action="store_true",
                   help="balance each shot but do not match them to each other")
    g.add_argument("--single-shot", action="store_true", help="skip shot detection")
    g.add_argument("--cuts", default="",
                   help="comma separated cut frames, used instead of detection. "
                        "Needed when the change between shots is colour only, "
                        "which content based detection cannot see.")
    g.add_argument("--lut-size", type=int, default=0, choices=[0, 17, 33, 65],
                   help="0 picks the size by measuring the bake error (default)")
    g.add_argument("--lut-error-budget", type=float, default=1.0,
                   help="max acceptable LUT error, in dE2000")
    g.add_argument("--crf", type=int, default=16)
    g.add_argument("--preset", default="medium")
    g.add_argument("--no-render", action="store_true")
    g.add_argument("--no-sheet", action="store_true")
    g.add_argument("--sheet-cols", type=int, default=4)
    g.add_argument("-v", "--verbose", action="store_true")
    common_shot_args(g)
    g.set_defaults(func=cmd_grade)

    lut = sub.add_parser("lut", help="bake one look to a .cube")
    lut.add_argument("--look", required=True)
    lut.add_argument("--out", required=True)
    lut.add_argument("--size", type=int, default=33, choices=[17, 33, 65])
    lut.add_argument("--exposure", type=float, default=0.0)
    lut.set_defaults(func=cmd_lut)

    k = sub.add_parser("looks", help="list the look library")
    k.set_defaults(func=cmd_looks)

    s = sub.add_parser("still", help="grade a single image")
    s.add_argument("input")
    s.add_argument("--out", required=True)
    s.add_argument("--look", default="neutral")
    s.add_argument("--normalize", choices=["full", "off"], default="off")
    s.add_argument("--balance-strength", type=float, default=1.0)
    s.add_argument("--exposure", type=float, default=0.0)
    s.set_defaults(func=cmd_still)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
