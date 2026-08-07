"""Measurement, balance derivation, shot matching, and the consistency judge.

This is the part that replaces a colourist's eyes on a scope. Every number it
produces is a measurement of the actual pixels, and every correction it derives
is capped, so a wrong reading can never wreck a shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

from cgcore import (
    Balance, Grade, code_to_lin, lin_to_code, lin_to_lab, luma, rgb_to_hsv, apply_grade,
)

# Percentiles we care about. p0.5 and p99.5 are the practical black and white
# points: using the true min and max just tracks a single hot pixel.
PCTS = [0.1, 0.5, 1, 5, 10, 25, 50, 75, 90, 95, 99, 99.5, 99.9]

# Above this share of perfectly flat pixels a shot is treated as rendered
# graphics rather than camera original. Calibrated by measurement, see the
# docstring on flatness() below.
GRAPHICS_FLATNESS = 0.22

# Below this confidence there is nothing in the shot that can be trusted as
# neutral, so white balance is left alone rather than guessed.
MIN_ILLUM_CONF = 0.35


@dataclass
class ShotStats:
    index: int
    n_frames: int
    duration: float
    lum_pct: dict = field(default_factory=dict)      # code space
    chan_pct: dict = field(default_factory=dict)     # {'r':{...},'g':...}
    illuminant: list = field(default_factory=list)   # linear RGB, from neutral pixels
    illum_conf: float = 0.0     # 0 means nothing in shot could be trusted as neutral
    flatness: float = 0.0       # 0 camera original, toward 1 rendered graphics
    is_graphics: bool = False
    lab_mean: list = field(default_factory=list)
    lab_std: list = field(default_factory=list)
    sat_mean: float = 0.0
    skin_frac: float = 0.0
    skin_lab: list = field(default_factory=list)
    skin_angle: float = 0.0
    clip_hi: float = 0.0        # fraction of pixels at or above 0.99 code
    clip_lo: float = 0.0        # fraction at or below 0.01 code
    contrast: float = 0.0       # code p95 minus code p5

    def as_dict(self):
        return asdict(self)


def _pct(x, pcts=PCTS):
    v = np.percentile(x, pcts)
    return {str(p): float(val) for p, val in zip(pcts, v)}


def measure(frames, index=0, duration=0.0) -> ShotStats:
    """frames: list of HxWx3 float32 arrays in Rec.709 code space, 0..1."""
    if not len(frames):
        raise ValueError("no frames to measure")
    px = np.concatenate([f.reshape(-1, 3) for f in frames], axis=0).astype(np.float32)
    # subsample for speed once we are past a couple of hundred thousand pixels
    if px.shape[0] > 400_000:
        step = px.shape[0] // 400_000 + 1
        px = px[::step]

    lin = code_to_lin(px)
    y_code = lin_to_code(luma(lin))

    st = ShotStats(index=index, n_frames=len(frames), duration=duration)
    st.lum_pct = _pct(y_code)
    st.chan_pct = {c: _pct(px[:, i]) for i, c in enumerate("rgb")}
    st.contrast = float(st.lum_pct["95"] - st.lum_pct["5"])
    st.clip_hi = float(np.mean(np.max(px, axis=1) >= 0.99))
    st.clip_lo = float(np.mean(np.max(px, axis=1) <= 0.01))

    ill, conf = neutral_illuminant(px)
    st.illuminant = [float(v) for v in ill]
    st.illum_conf = conf
    st.flatness = flatness(frames)
    st.is_graphics = st.flatness >= GRAPHICS_FLATNESS

    lab = lin_to_lab(lin)
    st.lab_mean = [float(v) for v in lab.mean(axis=0)]
    st.lab_std = [float(v) for v in lab.std(axis=0)]

    h, s, v = rgb_to_hsv(np.maximum(lin, 0.0))
    st.sat_mean = float(np.mean(s))

    skin = skin_mask(h, s, v)
    st.skin_frac = float(np.mean(skin))
    if st.skin_frac > 0.002:
        sl = lab[skin]
        st.skin_lab = [float(x) for x in sl.mean(axis=0)]
        st.skin_angle = float(np.degrees(np.arctan2(st.skin_lab[2], st.skin_lab[1])))
    return st


def shades_of_grey(lin, p=6.0):
    """Illuminant estimate, Minkowski norm of order p over each channel.

    p=1 is grey world, p=inf is white patch. Finlayson and Trezzi 2004 report
    p around 6 as the best general compromise.

    KNOWN FAILURE, and the reason this is no longer the default: every grey
    world style estimator assumes the average of the scene should be neutral.
    On a shot that is genuinely dominated by one colour, that assumption is
    false and the estimate is the subject, not the light. Measured on the
    a corporate brand film: shots built on the brand yellow were read as
    yellow lit, and correcting them turned the brand colour green. Use
    neutral_illuminant instead, which only looks at pixels that had a chance
    of being neutral in the first place.
    """
    x = np.maximum(lin.reshape(-1, 3), 0.0)
    e = np.power(np.mean(np.power(x, p, dtype=np.float64), axis=0), 1.0 / p)
    e = np.maximum(e, 1e-9)
    return (e / e.mean()).astype(np.float32)


SAT_NEUTRAL = 0.03    # median saturation of a genuine grey reference
SAT_HOPELESS = 0.25   # above this the flattest quarter is itself a colour


def neutral_illuminant(code, quantile=0.25, min_c=0.06, max_c=0.96):
    """Illuminant estimated only from pixels that could plausibly be neutral.

    This is what a colourist does: balance on something you know is grey, a
    wall, a shirt, a specular hit, never on the average of the frame.

    Takes the least saturated `quantile` of usable pixels and averages them
    in linear light. Returns (illuminant normalised to mean 1, confidence).

    Saturation is measured in code space on purpose. Measured in linear it
    reads far higher for the same visible tint, because linear values span a
    much wider range, and every shot then looks too coloured to trust.

    Confidence falls away as that least saturated population is itself
    saturated. If even the flattest quarter of the frame is strongly coloured
    there is nothing in shot that can be trusted as neutral, and the caller
    should leave white balance alone rather than guess.
    """
    c = np.asarray(code, dtype=np.float32).reshape(-1, 3)
    mx, mn = c.max(axis=1), c.min(axis=1)
    usable = (mx > min_c) & (mx < max_c)
    if usable.sum() < 64:
        return np.ones(3, dtype=np.float32), 0.0
    c, mx, mn = c[usable], mx[usable], mn[usable]
    sat = (mx - mn) / np.maximum(mx, 1e-9)

    cut = float(np.quantile(sat, quantile))
    sel = sat <= max(cut, 1e-6)
    if sel.sum() < 32:
        return np.ones(3, dtype=np.float32), 0.0

    e = code_to_lin(c[sel]).mean(axis=0, dtype=np.float64)
    e = np.maximum(e, 1e-9)
    e = (e / e.mean()).astype(np.float32)

    med = float(np.median(sat[sel]))
    conf = float(np.clip((SAT_HOPELESS - med) / (SAT_HOPELESS - SAT_NEUTRAL), 0.0, 1.0))
    return e, conf


def flatness(frames, tol=0.5 / 255.0):
    """How synthetic a shot looks, 0 for camera original, 1 for a flat fill.

    Fraction of pixels whose immediate neighbours match to within half a code
    level. Camera footage carries sensor noise everywhere, so almost nothing
    is truly flat. Rendered graphics and titles are flat over most of the
    frame. Used to decide whether the colour in a shot is the light or is the
    design, because a brand fill must never be white balanced.

    Calibrated on real files rather than picked: brand motion graphics on a
    corporate film read 0.26 to 0.72, the live action interior inside the same
    film reads 0.09, and AI generated footage reads 0.10 to 0.17. The tolerance matters,
    at 1.5 code levels compression smoothing pushes camera footage up to 0.35
    and the separation disappears.
    """
    vals = []
    for f in frames[:4]:
        y = f.mean(axis=2)
        dx = np.abs(np.diff(y, axis=1))[:-1, :]
        dy = np.abs(np.diff(y, axis=0))[:, :-1]
        vals.append(float(np.mean((dx < tol) & (dy < tol))))
    return float(np.median(vals)) if vals else 0.0


def skin_mask(h, s, v):
    """Crude but stable skin gate in HSV over linear RGB.

    Deliberately loose. It is used to measure whether skin drifted between
    shots and to protect skin from hue moves, not to isolate anybody.
    """
    return (h >= 5.0) & (h <= 45.0) & (s >= 0.15) & (s <= 0.70) & (v >= 0.03) & (v <= 0.95)


# ---------------------------------------------------------------- targets


@dataclass
class Targets:
    black: float = 0.0        # code value the p0.5 should land on
    white: float = 1.0        # code value the p99.5 should land on
    mid: float = 0.45         # code value the median luma should land on
    illum: tuple = (1.0, 1.0, 1.0)


def balanceable(stats: list[ShotStats]) -> list[ShotStats]:
    """The shots it is meaningful to balance: camera original, not graphics.

    A rendered graphic has no white balance to correct. Its colour is a
    decision somebody already made, usually a brand one. Including such shots
    in the target, or correcting them toward it, is how a brand yellow ends up
    green.
    """
    return [s for s in stats if not s.is_graphics]


def aggregate_targets(stats: list[ShotStats], weight_by_duration=True) -> Targets:
    """The video's own centre of gravity. Matching to this removes drift
    between shots without inventing a look the footage never had.

    Graphics shots are excluded from the target, and shots whose neutral
    reading could not be trusted contribute to the illuminant only in
    proportion to that confidence.
    """
    pool = balanceable(stats) or stats
    w = np.array([max(s.duration, 1e-3) for s in pool]) if weight_by_duration \
        else np.ones(len(pool))
    w = w / w.sum()

    def wmed(vals):
        # weighted median, robust to one very different shot
        order = np.argsort(vals)
        v, ww = np.asarray(vals)[order], w[order]
        c = np.cumsum(ww)
        return float(v[min(np.searchsorted(c, 0.5), len(v) - 1)])

    black = wmed([s.lum_pct["0.5"] for s in pool])
    white = wmed([s.lum_pct["99.5"] for s in pool])
    mid = wmed([s.lum_pct["50"] for s in pool])

    cw = w * np.array([s.illum_conf for s in pool])
    if cw.sum() < 1e-6:
        ill = np.array([1.0, 1.0, 1.0])
    else:
        ill = np.average(np.array([s.illuminant for s in pool], dtype=np.float64),
                         axis=0, weights=cw)
        ill = ill / ill.mean()
    return Targets(black=black, white=white, mid=mid, illum=tuple(float(x) for x in ill))


NEUTRAL_TARGETS = Targets(black=0.0, white=1.0, mid=0.45, illum=(1.0, 1.0, 1.0))


@dataclass
class Caps:
    exposure_stops: float = 1.5
    wb_gain: float = 0.18       # max fractional deviation per channel
    black_code: float = 0.10    # max black point move in code value
    white_code: float = 0.20
    lab_shift: float = 6.0      # max a*/b* nudge from the matcher
    lightness: float = 0.25     # max fractional L* scale from the matcher


def derive_balance(st: ShotStats, tgt: Targets, caps: Caps = Caps(),
                   strength=1.0, do_wb=True, do_exposure=True, do_levels=True):
    """Turn one shot's measurements into a capped Balance. Returns
    (Balance, notes) where notes records every cap that actually bound.
    """
    b = Balance()
    notes = []

    # A rendered graphic is left exactly as its designer made it. Nothing in
    # this function can improve it and everything in it can wreck a brand.
    if st.is_graphics:
        return b, [f"graphics shot (flatness {st.flatness:.2f}), left untouched"]

    # ---- white balance -------------------------------------------------
    if do_wb:
        if st.illum_conf < MIN_ILLUM_CONF:
            notes.append(f"no trustworthy neutral in shot "
                         f"(confidence {st.illum_conf:.2f}), white balance skipped")
        else:
            ill = np.asarray(st.illuminant, dtype=np.float64)
            tg = np.asarray(tgt.illum, dtype=np.float64)
            gains = np.where(ill > 1e-6, tg / ill, 1.0)
            gains = gains / gains.mean()
            # a marginal reading buys a partial correction, never a full one
            gains = 1.0 + (gains - 1.0) * strength * st.illum_conf
            clipped = np.clip(gains, 1.0 - caps.wb_gain, 1.0 + caps.wb_gain)
            if not np.allclose(clipped, gains, atol=1e-4):
                notes.append(f"white balance capped at {caps.wb_gain:.0%}")
            clipped = clipped / clipped.mean()
            b.gain_r, b.gain_g, b.gain_b = (float(x) for x in clipped)

    # ---- exposure ------------------------------------------------------
    if do_exposure:
        cur = max(st.lum_pct["50"], 1e-4)
        want = max(tgt.mid, 1e-4)
        stops = np.log2(code_to_lin(want) / max(code_to_lin(cur), 1e-6))
        stops = float(stops) * strength
        if abs(stops) > caps.exposure_stops:
            notes.append(f"exposure capped at {caps.exposure_stops} stops "
                         f"(wanted {stops:+.2f})")
            stops = float(np.clip(stops, -caps.exposure_stops, caps.exposure_stops))
        b.exposure = stops

    # ---- black and white point ----------------------------------------
    if do_levels:
        # measured after the exposure and wb moves above, approximately: both
        # are monotone in luma so applying them to the percentile is exact
        # enough for a capped correction.
        gmean = (b.gain_r + b.gain_g + b.gain_b) / 3.0
        k = (2.0 ** b.exposure) * gmean
        blk_lin = code_to_lin(st.lum_pct["0.5"]) * k
        wht_lin = code_to_lin(st.lum_pct["99.5"]) * k
        tb_lin = code_to_lin(tgt.black)
        tw_lin = code_to_lin(tgt.white)

        lift = (blk_lin - tb_lin) * strength
        max_lift = code_to_lin(caps.black_code)
        if abs(lift) > max_lift:
            notes.append(f"black point capped at {caps.black_code:.2f} code")
            lift = float(np.clip(lift, -max_lift, max_lift))
        b.lift = float(lift)

        span = max(wht_lin - lift, 1e-4)
        white = (tw_lin - tb_lin) / span
        white = 1.0 + (white - 1.0) * strength
        lo, hi = 1.0 - caps.white_code, 1.0 + caps.white_code * 2.0
        if not (lo <= white <= hi):
            notes.append(f"white point gain capped (wanted {white:.3f})")
            white = float(np.clip(white, lo, hi))
        b.white = float(white)

    return b, notes


def match_to_reference(st: ShotStats, ref: ShotStats, balance: Balance,
                       caps: Caps = Caps(), strength=0.5):
    """Residual colour match in Lab, on top of the mechanical balance.

    Only the a* and b* means and the L* scale are touched. Standard deviation
    matching (full Reinhard transfer) is deliberately not done: it fights the
    look and it exaggerates whichever shot happens to have the widest spread.
    """
    if (st.index == ref.index or st.is_graphics or ref.is_graphics
            or not st.lab_mean or not ref.lab_mean):
        return balance, []
    notes = []
    # When there was no trustworthy neutral in the shot, white balance did
    # nothing, and this match is the only mechanism left that can pull a cast
    # out. So lean on it harder exactly then. It is safe to do so: unlike
    # white balance this makes no claim about what neutral is, it only makes
    # two shots agree with each other.
    if st.illum_conf < MIN_ILLUM_CONF:
        strength = min(1.0, strength * 1.8)
        caps = Caps(**{**asdict(caps), "lab_shift": caps.lab_shift * 2.0})

    da = (ref.lab_mean[1] - st.lab_mean[1]) * strength
    db = (ref.lab_mean[2] - st.lab_mean[2]) * strength
    if abs(da) > caps.lab_shift or abs(db) > caps.lab_shift:
        notes.append(f"lab match capped at {caps.lab_shift}")
    balance.lab_shift_a = float(np.clip(da, -caps.lab_shift, caps.lab_shift))
    balance.lab_shift_b = float(np.clip(db, -caps.lab_shift, caps.lab_shift))

    # Lightness has to be matched here too, not only through the exposure and
    # levels above. Those work on luma percentiles, and matching the median of
    # the luma is not the same as matching the mean lightness: on the ground
    # truth harness the medians agreed to 0.03 code while mean L still spanned
    # 9 points, which reads as one shot being flatly brighter than the next.
    if st.lab_mean[0] > 1e-3:
        scale = ref.lab_mean[0] / st.lab_mean[0]
        scale = 1.0 + (scale - 1.0) * strength
        lo, hi = 1.0 - caps.lightness, 1.0 + caps.lightness
        if not (lo <= scale <= hi):
            notes.append(f"lightness match capped (wanted {scale:.3f})")
            scale = float(np.clip(scale, lo, hi))
        balance.lab_scale_L = float(scale)
    return balance, notes


def pick_reference(stats: list[ShotStats]) -> int:
    """The shot closest to the video's own centre, weighted toward longer
    shots. Longer shots carry the piece, so they should not be the ones that
    move.
    """
    pool = balanceable(stats) or stats
    tgt = aggregate_targets(stats)
    best, best_d = pool[0].index, 1e18
    for s in pool:
        d = (abs(s.lum_pct["50"] - tgt.mid) * 3.0
             + abs(s.lum_pct["0.5"] - tgt.black)
             + abs(s.lum_pct["99.5"] - tgt.white)
             + float(np.sum(np.abs(np.asarray(s.illuminant) - np.asarray(tgt.illum)))))
        d = d / (1.0 + 0.15 * min(s.duration, 8.0))
        if d < best_d:
            best, best_d = s.index, d
    return best


# ---------------------------------------------------------------- judge


def delta_e_2000(lab1, lab2):
    """CIEDE2000. Implemented from the Sharma, Wu and Dalal 2005 formulation."""
    L1, a1, b1 = [float(x) for x in lab1]
    L2, a2, b2 = [float(x) for x in lab2]
    kL = kC = kH = 1.0
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2.0
    G = 0.5 * (1.0 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25.0 ** 7))) if Cb > 0 else 0.5
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0 if (a1p or b1) else 0.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0 if (a2p or b2) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)
    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360.0) / 2.0
    else:
        hbp = (h1p + h2p - 360.0) / 2.0
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30))
         + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6))
         - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dTheta = 30.0 * np.exp(-(((hbp - 275.0) / 25.0) ** 2))
    Rc = 2.0 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7)) if Cbp > 0 else 0.0
    Sl = 1.0 + (0.015 * (Lbp - 50) ** 2) / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1.0 + 0.045 * Cbp
    Sh = 1.0 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dTheta)) * Rc
    return float(np.sqrt((dLp / (kL * Sl)) ** 2 + (dCp / (kC * Sc)) ** 2
                         + (dHp / (kH * Sh)) ** 2
                         + Rt * (dCp / (kC * Sc)) * (dHp / (kH * Sh))))


def delta_e_2000_vec(lab1, lab2):
    """Vectorised CIEDE2000 over arrays shaped (..., 3).

    Same formulation as delta_e_2000 above, which is checked against the
    Sharma, Wu and Dalal 2005 reference table in selftest.py.
    """
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2.0
    Cb7 = Cb ** 7
    G = 0.5 * (1.0 - np.sqrt(Cb7 / (Cb7 + 25.0 ** 7 + 1e-300)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    zero = (C1p * C2p) == 0

    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dhp = np.where(np.abs(dh) <= 180, dh, np.where(dh > 180, dh - 360.0, dh + 360.0))
    dhp = np.where(zero, 0.0, dhp)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)

    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) <= 180, hsum / 2.0,
                   np.where(hsum < 360, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0))
    hbp = np.where(zero, hsum, hbp)

    T = (1 - 0.17 * np.cos(np.radians(hbp - 30))
         + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6))
         - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dTheta = 30.0 * np.exp(-(((hbp - 275.0) / 25.0) ** 2))
    Cbp7 = Cbp ** 7
    Rc = 2.0 * np.sqrt(Cbp7 / (Cbp7 + 25.0 ** 7 + 1e-300))
    Sl = 1.0 + (0.015 * (Lbp - 50) ** 2) / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1.0 + 0.045 * Cbp
    Sh = 1.0 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dTheta)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


@dataclass
class Consistency:
    max_adjacent_de: float
    mean_adjacent_de: float
    worst_pair: tuple
    black_spread: float
    white_spread: float
    mid_spread: float
    illum_spread: float
    skin_angle_spread: float
    n_judged: int = 0
    n_graphics: int = 0
    per_shot: list = field(default_factory=list)
    verdict: str = ""

    def as_dict(self):
        return asdict(self)


# Adjacent shots inside one scene should be indistinguishable. A just
# noticeable difference is around 1.0 dE2000 for a flat patch; for a moving
# picture the practical cut point where an audience reads a jump is higher.
DE_PASS = 3.0
DE_WARN = 6.0


def judge(stats: list[ShotStats]) -> Consistency:
    """Consistency, measured only over the shots that ought to match.

    Graphics are excluded on purpose. A pale brand fill and a dark interior
    genuinely have different black points, and calling that an inconsistency
    is a category error that would push the grader into ruining one of them.
    What must match across every shot is the neutral axis and skin, and those
    are reported separately.
    """
    pool = balanceable(stats)
    n_graphics = len(stats) - len(pool)
    if len(pool) < 2:
        pool = stats
        n_graphics = 0

    des = []
    worst = (0.0, None)
    for a, b in zip(pool, pool[1:]):
        if not a.lab_mean or not b.lab_mean:
            continue
        d = delta_e_2000(a.lab_mean, b.lab_mean)
        des.append(d)
        if d > worst[0]:
            worst = (d, (a.index, b.index))
    blacks = [s.lum_pct["0.5"] for s in pool]
    whites = [s.lum_pct["99.5"] for s in pool]
    mids = [s.lum_pct["50"] for s in pool]
    ills = np.array([s.illuminant for s in pool if s.illum_conf >= MIN_ILLUM_CONF]) \
        if any(s.illum_conf >= MIN_ILLUM_CONF for s in pool) else np.zeros((1, 3))
    skin = [s.skin_angle for s in stats if s.skin_frac > 0.01]

    c = Consistency(
        max_adjacent_de=float(max(des)) if des else 0.0,
        mean_adjacent_de=float(np.mean(des)) if des else 0.0,
        worst_pair=worst[1] or (0, 0),
        black_spread=float(np.ptp(blacks)),
        white_spread=float(np.ptp(whites)),
        mid_spread=float(np.ptp(mids)),
        illum_spread=float(np.ptp(ills, axis=0).max()) if len(ills) else 0.0,
        skin_angle_spread=float(np.ptp(skin)) if len(skin) > 1 else 0.0,
        n_judged=len(pool),
        n_graphics=n_graphics,
        per_shot=[{"index": s.index, "mid": s.lum_pct["50"],
                   "black": s.lum_pct["0.5"], "white": s.lum_pct["99.5"],
                   "lab": s.lab_mean, "skin_frac": s.skin_frac,
                   "skin_angle": s.skin_angle, "clip_hi": s.clip_hi,
                   "graphics": s.is_graphics, "flatness": s.flatness,
                   "illum_conf": s.illum_conf}
                  for s in stats],
    )
    if c.max_adjacent_de <= DE_PASS:
        c.verdict = "consistent"
    elif c.max_adjacent_de <= DE_WARN:
        c.verdict = "close, one or two shots drift"
    else:
        c.verdict = "not consistent, shots visibly jump"
    return c


def restat(frames, g: Grade, st: ShotStats) -> ShotStats:
    """Re measure a shot after grading, by grading the sampled pixels.

    Preferred over predict_stats. Pushing only the mean colour through a
    nonlinear grade is not the same as the mean of the graded pixels, and the
    gap is large enough to matter: on the ground truth harness the analytic
    prediction claimed 2.7 dE of residual spread where the rendered file
    actually held 7.3. The iteration loop was therefore optimising against a
    number that was not true. Grading eight small frames per shot costs
    milliseconds and removes the guess entirely.
    """
    graded = [apply_grade(f, g) for f in frames]
    out = measure(graded, index=st.index, duration=st.duration)
    # these describe the source material, not the result
    out.flatness, out.is_graphics = st.flatness, st.is_graphics
    return out


def predict_stats(st: ShotStats, g: Grade) -> ShotStats:
    """What the shot's statistics become after a grade, without rendering.

    Pushes the measured percentiles and the mean colour through the same
    apply_grade() the LUT is baked from, so the judge can run before any
    pixels are encoded.
    """
    out = ShotStats(index=st.index, n_frames=st.n_frames, duration=st.duration)
    keys = list(st.lum_pct.keys())
    # Percentiles are monotone under the tone chain, so mapping the neutral
    # triple at each percentile is a faithful prediction of luma.
    grey = np.array([[v, v, v] for v in st.lum_pct.values()], dtype=np.float32)
    gy = apply_grade(grey, g)
    out.lum_pct = {k: float(lin_to_code(luma(code_to_lin(row))))
                   for k, row in zip(keys, gy)}
    out.chan_pct = st.chan_pct
    out.contrast = float(out.lum_pct["95"] - out.lum_pct["5"])
    out.clip_hi, out.clip_lo = st.clip_hi, st.clip_lo
    out.flatness, out.is_graphics = st.flatness, st.is_graphics
    out.illum_conf = st.illum_conf

    # Mean colour: reconstruct an RGB from the measured Lab mean, grade it,
    # measure it again.
    from cgcore import lab_to_lin
    mean_lin = np.maximum(lab_to_lin(np.array(st.lab_mean, dtype=np.float32)), 0.0)
    graded = apply_grade(lin_to_code(mean_lin)[None, :], g)[0]
    out.lab_mean = [float(x) for x in lin_to_lab(code_to_lin(graded))]
    out.lab_std = st.lab_std
    out.illuminant = st.illuminant
    out.sat_mean = st.sat_mean
    out.skin_frac = st.skin_frac
    if st.skin_lab:
        sl = np.maximum(lab_to_lin(np.array(st.skin_lab, dtype=np.float32)), 0.0)
        sg = apply_grade(lin_to_code(sl)[None, :], g)[0]
        out.skin_lab = [float(x) for x in lin_to_lab(code_to_lin(sg))]
        out.skin_angle = float(np.degrees(np.arctan2(out.skin_lab[2], out.skin_lab[1])))
    return out
