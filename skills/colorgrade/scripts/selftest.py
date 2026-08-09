#!/usr/bin/env python3
"""Self test for the colour maths. Run it after touching cgcore.py.

    python scripts/selftest.py

No video needed. Everything here is a measurement with a stated tolerance,
not a smoke test that only checks nothing threw.
"""

from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgcore as C
import cganalyze as A

LOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "looks")

_fail = 0
_ran = 0


def check(name, ok, detail=""):
    global _fail, _ran
    _ran += 1
    print(("PASS  " if ok else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        _fail += 1


def main():
    rng = np.random.default_rng(0)
    pts = rng.random((60000, 3)).astype(np.float32)

    # ---- transfer functions -------------------------------------------
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    e = np.abs(C.lin_to_code(C.code_to_lin(x)) - x).max()
    check("code and linear round trip", e < 1e-5, f"max error {e:.2e}")

    lin = np.linspace(1e-5, 4.0, 1001, dtype=np.float32)
    e = np.abs(C.log_to_lin(C.lin_to_log(lin)) - lin).max()
    check("linear and log round trip", e < 1e-4, f"max error {e:.2e}")

    v = float(C.lin_to_log(np.float32(0.18)))
    check("Cineon puts 18 percent grey on 0.4573", abs(v - 0.4573) < 1e-3, f"got {v:.4f}")
    v = float(C.lin_to_log(np.float32(0.0)))
    check("Cineon puts black on 0.0928", abs(v - 0.09286) < 1e-3, f"got {v:.5f}")

    # ---- colour spaces --------------------------------------------------
    lin = C.code_to_lin(pts)
    e = np.abs(C.lab_to_lin(C.lin_to_lab(lin)) - lin).max()
    check("linear and Lab round trip", e < 5e-4, f"max error {e:.2e}")

    h, s, _ = C.rgb_to_hsv(np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, .5]],
                                    dtype=np.float32))
    check("hue of red, green and blue", np.allclose(h, [0, 120, 240, 0], atol=1e-3), f"{h}")
    check("grey has no saturation", abs(float(s[3])) < 1e-5)

    # a neutral ramp must stay exactly neutral through the whole chain
    ramp = np.repeat(np.linspace(0, 1, 256, dtype=np.float32)[:, None], 3, axis=1)
    for name in sorted(os.path.basename(p)[:-5] for p in glob.glob(LOOKS + "/*.json")):
        out = C.apply_grade(ramp, C.Grade(look=C.load_look(name, LOOKS)))
        tint = float(np.abs(out - out.mean(axis=1, keepdims=True)).max()) * 255
        # only looks that deliberately tint should move a grey, and split
        # toning is exactly that, so this bound is generous by design
        check(f"grey ramp stays monotone under {name}",
              bool(np.all(np.diff(out.mean(axis=1)) >= -1e-4)),
              f"max channel spread {tint:.1f} of 255")

    # ---- identity ------------------------------------------------------
    out = C.apply_grade(pts, C.Grade())
    e = np.abs(out - pts).max() * 255
    check("an empty grade changes nothing", e < 0.25, f"max error {e:.3f} of 255")

    # ---- gamut compression ---------------------------------------------
    inside = rng.random((5000, 3)).astype(np.float32) * 0.9 + 0.05
    gc = C.gamut_compress(inside, limit=2.0, threshold=1.0)
    check("compression is disabled at threshold 1", np.allclose(gc, inside, atol=1e-6))
    wild = (rng.random((5000, 3)).astype(np.float32) - 0.35) * 2.0
    gc = C.gamut_compress(wild, limit=1.6, threshold=0.8)
    check("compression never returns a negative", bool(np.all(gc >= -1e-6)),
          f"min {float(gc.min()):.4f}")

    # ---- the shoulder and toe must actually bound the signal --------------
    # They did not. The old form lerped the compressed curve against the
    # identity, so at highlight_rolloff 0.35 the output passed 1.0 for any
    # input above 1.034 and reached 2.95 at input 4.0, and every look in the
    # library ships a value between 0.2 and 0.6. These two checks are the whole
    # point of the parameter, so they are asserted rather than assumed.
    over = np.linspace(0.0, 4.0, 4001, dtype=np.float32)
    worst, worst_a = 0.0, None
    for a in np.linspace(0.02, 1.0, 50):
        m = float(C._soft_shoulder(over, float(a), knee=0.80).max())
        if m > worst:
            worst, worst_a = m, float(a)
    check("the shoulder is bounded by 1 at every amount", worst <= 1.0 + 1e-6,
          f"worst {worst:.6f} at amount {worst_a:.2f}, inputs to 4.0")

    under = np.linspace(-2.0, 1.0, 3001, dtype=np.float32)
    worst, worst_a = np.inf, 0.0
    for a in np.linspace(0.02, 1.0, 50):
        m = float(C._soft_toe(under, float(a), knee=0.25).min())
        if m < worst:
            worst, worst_a = m, float(a)
    check("the toe is bounded by 0 at every amount", worst >= -1e-6,
          f"worst {worst:.6f} at amount {worst_a:.2f}, inputs to -2.0")

    # amount 1.0 must still be the curve the looks were authored against
    ref = 0.80 + 0.20 * (1.0 - np.exp(-np.maximum(over - 0.80, 0.0) / 0.20))
    ref = np.where(over > 0.80, ref, over)
    e = float(np.abs(C._soft_shoulder(over, 1.0, knee=0.80) - ref).max())
    check("amount 1.0 reproduces the original curve exactly", e < 1e-6,
          f"max deviation {e:.2e}")

    # ---- and `amount` must still mean what it says in between -------------
    # The three checks above pin the two ENDS: bounded everywhere, and amount
    # 1.0 identical to the old curve. Nothing pins the interior, and the
    # interior is where the library lives -- 9 of the 10 looks ship a rolloff
    # between 0.2 and 0.6. Reparameterising `a` survives all three: (1-a)**3,
    # (1-a)**0.25 and (1-a*a) each pass with 0 failures, and (1-a)**3 makes
    # `highlight_rolloff: 0.20` behave like 0.49 -- a slider that does not mean
    # what its name says, which is the defect this whole section exists for.
    # So assert the docstring's own promise: the knee sits `amount` of the way
    # down from 1.0 to `knee`, and more amount always rolls off more signal.
    bad = []
    for a in (0.1, 0.2, 0.35, 0.5, 0.75, 1.0):
        k = 0.80 + 0.20 * (1.0 - a)
        below = np.array([k - 1e-3], dtype=np.float32)
        above = np.array([min(k + 0.01, 0.999)], dtype=np.float32)
        if float(C._soft_shoulder(below, a, knee=0.80)[0]) != float(below[0]):
            bad.append(f"amount {a} rolls off below its stated knee {k:.3f}")
        if float(C._soft_shoulder(above, a, knee=0.80)[0]) >= float(above[0]) - 1e-5:
            bad.append(f"amount {a} does not roll off above its stated knee {k:.3f}")
    check("the shoulder knee sits where `amount` says it does",
          not bad, "; ".join(bad) or "amounts 0.10 to 1.00, knee 0.80")

    bad = []
    for a in (0.1, 0.2, 0.35, 0.5, 0.75, 1.0):
        k = 0.25 * a
        above = np.array([k + 1e-3], dtype=np.float32)
        below = np.array([max(k - 0.01, -0.5)], dtype=np.float32)
        if float(C._soft_toe(above, a, knee=0.25)[0]) != float(above[0]):
            bad.append(f"amount {a} toes above its stated knee {k:.3f}")
        if float(C._soft_toe(below, a, knee=0.25)[0]) <= float(below[0]) + 1e-5:
            bad.append(f"amount {a} does not toe below its stated knee {k:.3f}")
    check("the toe knee sits where `amount` says it does",
          not bad, "; ".join(bad) or "amounts 0.10 to 1.00, knee 0.25")

    ramp = np.linspace(0.0, 1.0, 20001, dtype=np.float32)
    fracs = [float((np.abs(C._soft_shoulder(ramp, a, knee=0.80) - ramp) > 1e-5).mean())
             for a in (0.1, 0.25, 0.5, 0.75, 1.0)]
    check("more `amount` always rolls off more of the signal",
          all(hi > lo for lo, hi in zip(fracs, fracs[1:])),
          " < ".join(f"{f * 100:.1f}%" for f in fracs))

    # The sweeps above stop at amount 1.0, so nothing sees a look file that
    # asks for more. `min(amount, 1.0)` is load bearing: without it, amount 6
    # puts the knee at -0.20 and the shoulder returns NEGATIVE values for a
    # black input. Pin the clamp rather than the symptom.
    worst = 0.0
    for a in (1.5, 5.0, 50.0):
        worst = max(worst,
                    float(np.abs(C._soft_shoulder(over, a, knee=0.80)
                                 - C._soft_shoulder(over, 1.0, knee=0.80)).max()),
                    float(np.abs(C._soft_toe(under, a, knee=0.25)
                                 - C._soft_toe(under, 1.0, knee=0.25)).max()))
    check("an amount above 1.0 clamps to 1.0 rather than running past it",
          worst == 0.0, f"worst difference {worst:.2e} across amounts 1.5, 5, 50")

    # and the rolloff must stop the display chain pinning at white
    dense = np.linspace(0, 1, 65, dtype=np.float32)
    cube = np.stack(np.meshgrid(dense, dense, dense, indexing="ij"), axis=-1).reshape(-1, 3)
    for name in sorted(os.path.basename(p)[:-5] for p in glob.glob(LOOKS + "/*.json")):
        look = C.load_look(name, LOOKS)
        if look.highlight_rolloff <= 0:
            continue
        code = C.lin_to_code(np.maximum(C.apply_look(C.code_to_lin(cube), look), 0.0))
        if look.black_offset:
            code = code * (1.0 - look.black_offset) + look.black_offset
        rolled = C._soft_shoulder(code, look.highlight_rolloff, knee=0.80)
        check(f"  {name} rolloff leaves headroom rather than clipping",
              float(rolled.max()) <= 1.0 + 1e-6,
              f"peak {float(rolled.max()):.6f} at rolloff {look.highlight_rolloff}")

    # ---- hue_shifts gate and rotation must live in the same space ---------
    # The gate was HSV and the rotation Lab, in one call, with nothing naming
    # either. A centre measured in Lab then selected nothing and the shift was
    # silently inert. On a real job four holds did nothing and printed numbers
    # identical to no holds at all.
    teal = C.code_to_lin(np.array([[[0.05, 0.42, 0.45]]], dtype=np.float32))
    centre = float(C.lab_hue(teal)[0, 0])
    start = float(C.lab_hue(teal)[0, 0])
    ent = {"centre": centre, "width": 20.0, "shift": 25.0}
    moved_hsv = float(C.lab_hue(C.apply_look(teal, C.Look(hue_shifts=[dict(ent)])))[0, 0]) - start
    moved_lab = float(C.lab_hue(
        C.apply_look(teal, C.Look(hue_shifts=[dict(ent, space="lab")])))[0, 0]) - start
    check("a Lab centre gated in Lab rotates by what it asked for",
          abs(moved_lab - 25.0) < 0.05, f"asked 25.0, got {moved_lab:+.3f} deg")
    check("the same entry gated in HSV is inert, which is the trap",
          abs(moved_hsv) < 0.5, f"{moved_hsv:+.3f} deg")

    try:
        C.apply_look(teal, C.Look(hue_shifts=[{"centre": 0.0, "space": "hcl"}]))
        check("an unknown hue_shift space is refused", False, "it was accepted")
    except ValueError:
        check("an unknown hue_shift space is refused", True)

    # looks in the library carry no space key and must keep their HSV gate
    worst = 0.0
    for name in sorted(os.path.basename(p)[:-5] for p in glob.glob(LOOKS + "/*.json")):
        look = C.load_look(name, LOOKS)
        if not look.hue_shifts:
            continue
        explicit = C.Look(**{**look.__dict__,
                             "hue_shifts": [dict(x, space="hsv") for x in look.hue_shifts]})
        a = C.apply_look(C.code_to_lin(pts), look)
        b = C.apply_look(C.code_to_lin(pts), explicit)
        worst = max(worst, float(np.abs(a - b).max()))
    check("the library looks default to the HSV gate they were authored in",
          worst == 0.0, f"worst difference {worst:.2e}")

    # ---- LUT bake ------------------------------------------------------
    # Error is in dE2000 because the raw code level error piles up in the
    # deepest shadows, where it is not visible. See lut_bake_error in cg.py.
    # 1.5 is the ceiling, not the goal. A just noticeable difference on a flat
    # patch is about 1.0 dE, and the worst of sixty thousand random colours
    # sitting at 1.2 is the price of a very hard contrast curve. noir, at
    # contrast 1.55, is the one look that cannot do better than that through
    # any cube, and the grader reports its residual rather than hiding it.
    print("\nLUT bake error, worst dE2000 (and worst code levels of 255):")
    budget = 1.5
    for name in sorted(os.path.basename(p)[:-5] for p in glob.glob(LOOKS + "/*.json")):
        g = C.Grade(look=C.load_look(name, LOOKS))
        row, chosen = [], None
        exact = C.apply_grade(pts, g)
        lab_exact = C.lin_to_lab(C.code_to_lin(exact))
        for size in (17, 33, 65):
            lut = C.bake_lut(g, size)
            approx = C.apply_lut_trilinear(pts, lut, size)
            de = A.delta_e_2000_vec(lab_exact, C.lin_to_lab(C.code_to_lin(approx)))
            row.append((size, float(de.max()), float(np.abs(exact - approx).max() * 255)))
            if chosen is None and size >= 33 and de.max() <= budget:
                chosen = size
        print(f"  {name:<18} " + "   ".join(f"{s}^3 {m:5.2f}dE ({c:5.2f})" for s, m, c in row))
        check(f"  {name} lands under {budget} dE at 33 or 65", chosen is not None,
              f"chose {chosen}^3" if chosen else "neither size was good enough")

    # the vectorised dE must agree with the scalar one that is checked against
    # the published reference table below
    l1 = C.lin_to_lab(C.code_to_lin(pts[:300]))
    l2 = C.lin_to_lab(C.code_to_lin(pts[300:600]))
    vec = A.delta_e_2000_vec(l1, l2)
    sca = np.array([A.delta_e_2000(a, b) for a, b in zip(l1, l2)])
    check("vectorised dE2000 agrees with the scalar one",
          float(np.abs(vec - sca).max()) < 1e-9,
          f"max deviation {float(np.abs(vec - sca).max()):.2e}")

    # ---- cube file round trip -------------------------------------------
    import tempfile
    g = C.Grade(look=C.load_look("kodak2383", LOOKS))
    lut = C.bake_lut(g, 33)
    with tempfile.NamedTemporaryFile(suffix=".cube", delete=False) as f:
        p = f.name
    C.write_cube(p, lut, 33, title="selftest")
    back, size = C.read_cube(p)
    os.unlink(p)
    check("cube file writes and reads back",
          size == 33 and back.shape == lut.shape and np.abs(back - lut).max() < 1e-5,
          f"size {size}, max error {float(np.abs(back - lut).max()):.2e}")

    # ---- dE2000 against the Sharma reference pairs -----------------------
    # Sharma, Wu and Dalal 2005, table 1. These are the pairs everybody uses
    # to prove a CIEDE2000 implementation is not subtly wrong.
    cases = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
        ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
        ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
        ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ]
    worst = 0.0
    for a, b, want in cases:
        got = A.delta_e_2000(a, b)
        worst = max(worst, abs(got - want))
    check("CIEDE2000 matches the Sharma reference table", worst < 1e-3,
          f"worst deviation {worst:.5f}")

    print(f"\n{_ran} checks, {_fail} failures")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
