#!/usr/bin/env python3
"""Self test for the picture tools: panels, frame repair, localized colour fix.

    python scripts/selftest_tools.py

Builds a small synthetic clip whose faults are known exactly, then asks the
tools to find them. Nothing here is a smoke test: every check has a stated
tolerance and a stated reason, and the clip is built to contain the specific
traps that cost time on real footage.

    frames 0..59, 320x160, a divider at x=160 with a flat gutter either side
    LEFT  panel  moves smoothly the whole way and cuts at frame 30
    RIGHT panel  moves smoothly, has FROZEN frames at 12, 13, 25, 40, and a
                 genuine six frame hold at 50..55 which must NOT be repaired
    frame 30     the left panel's colour steps with no cut in the right panel
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgfix as X
import cgframes as R
import cgpanel as P

W, H, N = 320, 160, 60
SPLIT = 160
FROZEN = {12, 13, 25, 40}
HOLD = set(range(50, 56))
CUT_L = 30

_fail = 0
_ran = 0


def check(name, ok, detail=""):
    global _fail, _ran
    _ran += 1
    print(("PASS  " if ok else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        _fail += 1


def _texture(w, h, seed, smooth=0):
    rng = np.random.default_rng(seed)
    t = rng.random((h, w)).astype(np.float32)
    for _ in range(smooth):
        t = (t + np.roll(t, 1, 1) + np.roll(t, -1, 1)
             + np.roll(t, 1, 0) + np.roll(t, -1, 0)) / 5.0
    t -= t.min()
    return t / max(t.max(), 1e-6) * 0.7 + 0.15


def build_clip(path):
    # The LEFT panel is a GREY texture, equal in all three channels, and it is
    # smooth so that a one pixel shift still leaves consecutive frames highly
    # correlated. Both properties are needed for the test to mean anything: a
    # tint on a grey texture is a pure colour change that leaves the luma
    # structure proportional, which is what a grade does, whereas re-weighting
    # three decorrelated channels changes the picture and would read as a cut.
    left = _texture((SPLIT - 8) * 3, H, 1, smooth=6)
    right = _texture((W - SPLIT - 8) * 3, H, 2, smooth=1)

    frames = []
    rphase = 0
    for n in range(N):
        f = np.zeros((H, W, 3), np.float32)
        lo = (n * 1) % (SPLIT - 8)
        g = left[:, lo:lo + SPLIT - 8]
        tint = (1.0, 1.0, 1.0) if n < CUT_L else (0.86, 1.0, 1.12)
        f[:, :SPLIT - 8] = g[..., None] * np.asarray(tint, np.float32)
        # right: advances except on the frozen frames and through the hold
        if n not in FROZEN and n not in HOLD:
            rphase += 3
        ro = rphase % (W - SPLIT - 8)
        f[:, SPLIT + 8:] = right[:, ro:ro + W - SPLIT - 8][..., None] * (0.9, 1.0, 0.95)
        # the divider: a flat mid grey bar, held still all the way through
        f[:, SPLIT - 8:SPLIT + 8] = 0.5
        frames.append(np.round(np.clip(f, 0, 1) * 255).astype(np.uint8))

    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", "24", "-i", "-",
         "-c:v", "libx264", "-crf", "8", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("could not build the test clip")


def main():
    tmp = tempfile.mkdtemp(prefix="cgtools_")
    clip = os.path.join(tmp, "split.mp4")
    build_clip(clip)

    info = P.probe(clip)
    check("the test clip is what it says it is",
          (info["width"], info["height"], info["frames"]) == (W, H, N),
          f"{info['width']}x{info['height']}, {info['frames']} frames")

    # ---- decode discipline -------------------------------------------------
    _, s1 = P.stream(clip, 0, 4, 0, 160)
    _, s2 = P.stream(clip, 10, 14, 0, 160)
    _, s3 = P.stream(clip, 0, 4, 0, 160, scale=80)
    check("the same band at the same scale is comparable across ranges", s1 == s2)
    try:
        P.require_same_path(s1, s3)
        check("two scales are refused as incomparable", False, "it allowed them")
    except ValueError:
        check("two scales are refused as incomparable", True)

    x0, x1 = P.snap_band(3, 84, W)
    check("an odd crop band is snapped even before it reaches ffmpeg",
          x0 % 2 == 0 and (x1 - x0) % 2 == 0, f"got {x0},{x1}")

    # ---- panels ------------------------------------------------------------
    found = P.find_panels(clip, width=W)
    got = found[0]["split_x"] if found else None
    check("the divider is found within 12 columns of where it was drawn",
          got is not None and abs(got - SPLIT) <= 12,
          f"found {got}, drawn at {SPLIT}")

    # ---- stalls ------------------------------------------------------------
    st = {d["frame"] for d in R.stalls(clip, SPLIT + 8, W, cuts=[], scale=W)}
    check("every frozen frame in the right panel is found",
          FROZEN <= st, f"found {sorted(st)}, planted {sorted(FROZEN)}")
    check("the six frame hold is NOT called a fault",
          not (HOLD & st),
          f"{sorted(HOLD & st)} would have been rebuilt, inventing movement")
    stl = {d["frame"] for d in R.stalls(clip, 0, SPLIT - 8, cuts=[], scale=W)}
    check("the left panel, which never stalls, is left alone",
          not (FROZEN & stl), f"claimed {sorted(FROZEN & stl)}")

    # ---- scoring -----------------------------------------------------------
    rng = np.random.default_rng(3)
    truth = rng.random((64, 64)).astype(np.float32) * 255
    soft = np.stack([truth, np.roll(truth, 1, 0)]).mean(0)
    s_true = R.score(truth, truth, truth)
    s_soft = R.score(soft, truth, truth)
    check("a perfect rebuild scores zero error and full detail",
          abs(s_true["error"]) < 1e-4 and abs(s_true["detail"] - 1) < 1e-4,
          f"error {s_true['error']:.2e}, detail {s_true['detail']:.4f}")
    check("blur is caught by detail even though it is the point of the blur",
          s_soft["detail"] < 0.95, f"detail {s_soft['detail'] * 100:.1f}%")

    # ---- cut versus colour change ------------------------------------------
    rows = dict(X.edge_corr(clip, CUT_L, 0, SPLIT - 8, span=4, scale=W))
    here = rows[CUT_L]
    others = [v for k, v in rows.items() if k != CUT_L]
    check("a colour step with no cut keeps the picture correlated",
          here > 0.5 * float(np.median(others)),
          f"{here:+.4f} against a neighbourhood median of {np.median(others):+.4f}")

    # a real cut, made by comparing across two unrelated textures
    a = _texture(64, 64, 11, smooth=2)
    b = _texture(64, 64, 12, smooth=2)
    ga, gb = np.diff(a, axis=1), np.diff(b, axis=1)
    ga, gb = ga.ravel() - ga.mean(), gb.ravel() - gb.mean()
    cut_corr = float((ga * gb).sum() / np.sqrt((ga * ga).sum() * (gb * gb).sum()))
    check("a real cut reads near zero on the same measure",
          abs(cut_corr) < 0.2, f"{cut_corr:+.4f}")

    # ---- the delta fit -----------------------------------------------------
    # a hue selective change a 3x3 matrix cannot express, plus the black test
    rng = np.random.default_rng(5)
    B = rng.random((600, 3)).astype(np.float64)
    sel = np.exp(-((B[:, 2] - 0.7) ** 2) / 0.02)[:, None]
    A = np.clip(B + sel * np.array([-0.04, 0.02, 0.05]), 0, 1)
    m = X.fit_delta(A * 255, B * 255, ncentres=48, eps=0.16)
    sc = m["scores"]
    names = [n for n in sc if n.startswith("rbf")]
    check("the fit beats doing nothing", sc[names[0]]["held_out"] < sc["do nothing"]["held_out"],
          f"{sc[names[0]]['held_out']:.2f} against {sc['do nothing']['held_out']:.2f} levels")
    check("a 3x3 matrix cannot express a hue selective change, and says so",
          sc["3x3 matrix"]["held_out"] > sc[names[0]]["held_out"],
          f"matrix {sc['3x3 matrix']['held_out']:.2f}, rbf "
          f"{sc[names[0]]['held_out']:.2f} levels")

    lut = X.correction_lut(m, 17)
    black = float(np.abs(lut[0]).max()) * 255
    check("fitting the DELTA leaves pure black alone",
          black < 1.0, f"black moved {black:.2f} of 255 (fitting the map moved 2.3)")

    # ---- the retime smoother must not park the camera at the ends --------
    # Both halves are required. The ramp extension has to be right AND
    # mode="edge" has to be shown wrong on the same curve, or this is not
    # evidence that the padding mattered.
    import cgmotion as M
    for label, C, truth in (
            ("a clean ramp", np.arange(60, dtype=float) * 10.0, 10.0),
            ("a held frame timeline", np.repeat(np.arange(20, dtype=float) * 30.0, 3), 10.0)):
        v = np.diff(M.smooth_cumulative(C, 25))
        k = 12
        old_pad = np.pad(C, k, mode="edge")
        v_old = np.diff(np.array([old_pad[i:i + 25].mean() for i in range(len(C))]))
        check(f"the smoother keeps the shot's speed at both ends, {label}",
              abs(v[0] - truth) < 0.5 and abs(v[-1] - truth) < 0.5,
              f"head {v[0]:.2f}, tail {v[-1]:.2f}, truth {truth:.2f}")
        check(f"and mode=edge really does lose it, {label}",
              v_old[0] < truth * 0.7 and v_old[-1] < truth * 0.7,
              f"head {v_old[0]:.2f}, tail {v_old[-1]:.2f}")
    flat = M.smooth_cumulative(np.zeros(40), 25)
    check("a shot that never moves is not given movement",
          float(np.abs(np.diff(flat)).max()) == 0.0)

    print(f"\n{_ran} checks, {_fail} failures")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
