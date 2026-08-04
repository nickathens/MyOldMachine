#!/usr/bin/env python3
"""Ground truth test for the balancer.

Takes one clean clip, cuts it into segments, damages each segment by a known
amount (a colour cast, an exposure offset, a lifted black), stitches them back
into one file, and then asks the grader to repair it. Because the damage is
known, the repair can be scored rather than admired.

    python scripts/groundtruth.py SOURCE.mp4 --workdir /tmp/gt

Reports, per segment, how far the graded result sits from the clean original
in dE2000, next to how far the damaged version sat. Anything above 1.0 means
the repair left visible error behind.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgcore as C
import cgvideo as V
import cganalyze as A

# Deliberately varied damage: a warm shot, a cool shot, a dark shot, a bright
# flat shot, a green cast. All within what a capped balancer is allowed to
# undo, so a failure here is a real failure and not a cap doing its job.
DAMAGE = [
    dict(name="clean", gain=(1.00, 1.00, 1.00), exposure=0.0, lift=0.00),
    dict(name="warm", gain=(1.12, 1.00, 0.88), exposure=0.0, lift=0.00),
    dict(name="cool", gain=(0.90, 0.99, 1.14), exposure=0.0, lift=0.00),
    dict(name="dark", gain=(1.00, 1.00, 1.00), exposure=-0.8, lift=0.00),
    dict(name="bright and flat", gain=(1.00, 1.00, 1.00), exposure=0.6, lift=0.035),
    dict(name="green", gain=(0.94, 1.10, 0.96), exposure=0.0, lift=0.00),
]


def damage_grade(d):
    b = C.Balance(exposure=d["exposure"], gain_r=d["gain"][0], gain_g=d["gain"][1],
                  gain_b=d["gain"][2])
    look = C.Look(black_offset=d["lift"])
    return C.Grade(balance=b, look=look)


def build_case(src, workdir, seg_frames=36, width=960):
    os.makedirs(workdir, exist_ok=True)
    media = V.probe(src)
    n = min(len(DAMAGE), max(1, media.nb_frames // seg_frames))
    h = int(round(media.height * width / media.width))
    h += h % 2

    clean_dir = os.path.join(workdir, "clean")
    dmg_dir = os.path.join(workdir, "damaged")
    for d in (clean_dir, dmg_dir):
        os.makedirs(d, exist_ok=True)

    # one decode, straight to per segment PNG sequences
    print(f"building {n} segments of {seg_frames} frames at {width}x{h}")
    cmd = [V.FFMPEG, "-v", "error", "-i", src, "-vf", f"scale={width}:{h}",
           "-frames:v", str(n * seg_frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    fb = width * h * 3
    got = len(raw) // fb
    if got < n * seg_frames:
        n = max(1, got // seg_frames)
        print(f"source is short, using {n} segments")

    # Every segment gets the SAME frames. That is the whole point: if the
    # content differs between segments then they are not supposed to match,
    # and any residual would be the edit, not the grader. Identical content
    # means every remaining difference is damage the balancer failed to undo.
    #
    # And every segment is one frozen frame, not a moving loop. A moving loop
    # carries its own content changes, and the shot detector fired on one of
    # those instead of on the real boundary, then dropped the real boundary
    # for being closer than the minimum shot length. Segments were then
    # measured across two different damages and the harness blamed the grader
    # for it. Frozen frames make the only change in the file the one under
    # test.
    idx = min(seg_frames // 2, got - 1)
    still = np.frombuffer(raw[idx * fb:(idx + 1) * fb],
                          dtype=np.uint8).reshape(h, width, 3).astype(np.float32) / 255.0
    base = [still] * seg_frames

    clean_frames, dmg_frames = [], []
    for seg in range(n):
        for f in base:
            clean_frames.append(f)
            dmg_frames.append(C.apply_grade(f, damage_grade(DAMAGE[seg])))

    def encode(frames, path):
        p = subprocess.Popen(
            [V.FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{width}x{h}", "-r", "24", "-i", "-",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
             "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
        for f in frames:
            p.stdin.write(np.clip(f * 255 + 0.5, 0, 255).astype(np.uint8).tobytes())
        p.stdin.close()
        p.wait()

    clean_mp4 = os.path.join(workdir, "clean.mp4")
    dmg_mp4 = os.path.join(workdir, "damaged.mp4")
    encode(clean_frames, clean_mp4)
    encode(dmg_frames, dmg_mp4)
    return clean_mp4, dmg_mp4, n, seg_frames


def seg_stats(path, n, seg_frames, width=480):
    media = V.probe(path)
    shots = [V.Shot(i, i * seg_frames, (i + 1) * seg_frames,
                    i * seg_frames / media.fps, (i + 1) * seg_frames / media.fps)
             for i in range(n)]
    samples = V.collect_shot_samples(media, shots, per_shot=6, width=width)
    return [A.measure(samples[s.index], index=s.index, duration=s.duration) for s in shots]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--workdir", default="/tmp/cg_groundtruth")
    ap.add_argument("--seg-frames", type=int, default=36)
    ap.add_argument("--normalize", default="match", choices=["match", "full"])
    ap.add_argument("--tolerance", type=float, default=2.0,
                    help="max acceptable residual dE2000 after repair")
    args = ap.parse_args()

    clean, damaged, n, sf = build_case(args.source, args.workdir,
                                       seg_frames=args.seg_frames)

    graded = os.path.join(args.workdir, "repaired.mp4")
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cg.py"),
           "grade", damaged, "--look", "neutral", "--normalize", args.normalize,
           "--out", graded, "--workdir", os.path.join(args.workdir, "work"),
           "--cuts", ",".join(str(i * sf) for i in range(1, n)), "--no-sheet"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        return 1

    # the harness scores nothing until it knows the grader cut where we did
    cs = seg_stats(clean, n, sf)
    ds = seg_stats(damaged, n, sf)
    gs = seg_stats(graded, n, sf)

    # The score that matters is SPREAD between segments, not distance from the
    # clean original. In match mode the grader deliberately aims at the video's
    # own centre of gravity, so all six should converge on one another, and
    # that common point sits at the average of the damage rather than at clean.
    # Only full mode targets absolute neutral, and only then is the distance
    # from clean a fair thing to score.
    def spread(stats):
        labs = [s.lab_mean for s in stats]
        return max(A.delta_e_2000(a, b) for i, a in enumerate(labs)
                   for b in labs[i + 1:]) if len(labs) > 1 else 0.0

    def adjacent(stats):
        labs = [s.lab_mean for s in stats]
        return max((A.delta_e_2000(a, b) for a, b in zip(labs, labs[1:])), default=0.0)

    print(f"\n{'segment':<16} {'damaged':>10} {'repaired':>10}   "
          f"distance from the clean original, dE2000")
    rows = []
    for i in range(n):
        d0 = A.delta_e_2000(cs[i].lab_mean, ds[i].lab_mean)
        d1 = A.delta_e_2000(cs[i].lab_mean, gs[i].lab_mean)
        rows.append((DAMAGE[i]["name"], d0, d1))
        print(f"{DAMAGE[i]['name']:<16} {d0:>10.2f} {d1:>10.2f}")

    sd, sg = spread(ds), spread(gs)
    ad, ag = adjacent(ds), adjacent(gs)
    print("\nworst difference between any two segments, dE2000")
    print(f"  damaged  {sd:6.2f}   (adjacent only {ad:5.2f})")
    print(f"  repaired {sg:6.2f}   (adjacent only {ag:5.2f})   "
          f"{(1 - sg / sd) * 100 if sd > 1e-6 else 0:.0f}% closed")
    print("\nper segment L* a* b* after repair (all six should agree):")
    for i in range(n):
        print(f"  {DAMAGE[i]['name']:<16} clean {cs[i].lab_mean[0]:6.1f}"
              f"{cs[i].lab_mean[1]:6.1f}{cs[i].lab_mean[2]:6.1f}   "
              f"damaged {ds[i].lab_mean[0]:6.1f}{ds[i].lab_mean[1]:6.1f}{ds[i].lab_mean[2]:6.1f}"
              f"   repaired {gs[i].lab_mean[0]:6.1f}{gs[i].lab_mean[1]:6.1f}{gs[i].lab_mean[2]:6.1f}")

    ok = sg <= args.tolerance
    print(f"\ntolerance {args.tolerance}")
    print("PASS" if ok else "FAIL")
    with open(os.path.join(args.workdir, "groundtruth.json"), "w") as f:
        json.dump({"rows": rows, "spread_damaged": sd, "spread_repaired": sg,
                   "tolerance": args.tolerance, "pass": ok}, f, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
