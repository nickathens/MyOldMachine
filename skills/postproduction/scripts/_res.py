#!/usr/bin/env python3
"""Resolution: what a picture actually carries, and what an enlargement did to it.

Two measurements nothing else in this skill can make, and one of them does not
exist in the stills upscaler at all.

  EFFECTIVE RESOLUTION. A raster is a claim. The radially averaged power
  spectrum of a natural picture follows a power law over its mid band, and an
  enlargement snaps that law off at the SOURCE's Nyquist, so the frequency where
  the spectrum leaves its own law is the resolution the file really carries. A
  review set named 4K that was blown up from 720 says so here; the frame size
  never does. The measurement cannot tell an enlargement from a soft lens or
  from heavy compression, and it says so: all three suppress the same band. What
  it can always say is the useful half, which is that there is no detail above
  this frequency, so enlarging further adds nothing a resampler could find.

  TEMPORAL STABILITY. A stills upscaler has no memory, so it re-invents its
  detail on every frame independently and the picture boils. The measurement is
  the warping error: push a frame forward along the optical flow and see how far
  it misses the next one. The trap is that this number is LARGE on a correct
  film, because flow is wrong at every occlusion and every specular, so it is
  read against the SOURCE's own warping error and never against zero. That is
  the generation floor argument, in time instead of in code levels.

Everything here needs numpy and OpenCV, so it lives in this skill's own
environment (~/.venvs/post), never the bot's.
"""
from __future__ import annotations

import numpy as np

# The mid band the power law is fitted over, in units of Nyquist. The top of it
# is the number that took the calibration: at 0.30 a real lens has already begun
# to roll off inside the fit band, so the law being extrapolated is the softness
# itself and the tool measures the blur through the blur -- the same trap as
# scoring a de-plastic filter with the filter that made it. Dropped to 0.16 the
# fit sits under every optical roll off measured here (curvature +1.2 to -3.1
# dB per decade squared across a native frame, three resamplers and lens PSFs to
# sigma 1.2) while still sitting under the knee of a 4x enlargement at 0.25.
FIT_LO, FIT_HI = 0.03, 0.16

# Bins across the radius. 128 keeps sixteen usable bins inside the narrowed fit
# band, which is enough to fit a line and to cross check its curvature.
BINS = 128

# How far below the extrapolated law counts as the spectrum having left it.
# 6 dB is a factor of four in power, well outside the scatter of a real fit
# (measured at 0.3 to 1.5 dB RMS on the synthetic and real frames in selftest).
DROP_DB = 6.0

# Below this the mid band is not a power law and extrapolating it is a guess,
# so the answer is UNDETERMINED rather than a number. Measured 0.20 to 0.25 dB
# on every honest case in selftest and 9.27 dB on a frame blurred past
# measurability.
MAX_FIT_RMS_DB = 3.0

# A straight line can fit a curve over a short span with small residuals, so
# scatter alone is not enough: the quadratic term is checked too. Honest cases
# measured within 3.1; a frame with nothing left above 0.02 Nyquist measured
# 254. The limit sits between them with two orders of margin on the fault side.
MAX_CURV_DB = 12.0

# A resampler leaves a bias: it does not cut at the source Nyquist, it rolls off
# through a transition band, so the knee lands ABOVE the true source Nyquist.
# Measured across Lanczos, bicubic and bilinear at 2x, 3x, 4x and 1.5x: 1.05 to
# 1.14. The knee is therefore an UPPER BOUND on the source's own Nyquist, and a
# source raster is only ever offered as consistent with the reading.
KNEE_BIAS_MAX = 1.25

# A frame with no texture has no spectrum to read. Measured as the fall in power
# from the bottom of the fit band to the top: a real picture drops tens of dB, a
# flat card or a heavy defocus drops almost nothing and cannot be measured.
MIN_BAND_FALL_DB = 6.0


# ---------------------------------------------------------------- basics


def gray(a):
    """Rec.709 luma of an HxWx3 array, or an array that is already 2D."""
    a = np.asarray(a, dtype=np.float32)
    if a.ndim == 2:
        return a
    return (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]).astype(np.float32)


def psnr(a, b, peak=1.0):
    """PSNR in dB. Returns inf when the arrays are identical."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f"cannot compare {a.shape} with {b.shape}")
    mse = float(np.mean((a - b) ** 2))
    if mse <= 0:
        return float("inf")
    return float(10.0 * np.log10((peak ** 2) / mse))


def resize(arr, size, interp=None):
    """Resample to (w, h). Lanczos going up, area going down, unless told."""
    import cv2
    a = np.asarray(arr, dtype=np.float32)
    if interp is None:
        interp = cv2.INTER_AREA if size[0] * size[1] < a.shape[1] * a.shape[0] \
            else cv2.INTER_LANCZOS4
    return cv2.resize(a, (int(size[0]), int(size[1])), interpolation=interp)


# ---------------------------------------------------------------- spectrum


def radial_power(g, bins=BINS):
    """Radially averaged power spectrum, in units where 1.0 is Nyquist.

    The frame is windowed before the transform. Without a window the frame's own
    edges are a step discontinuity, which puts a cross of energy at every
    frequency, and that cross is far brighter than the knee we are looking for.
    """
    g = np.asarray(gray(g), dtype=np.float64)
    h, w = g.shape
    win = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    f = np.fft.fftshift(np.fft.fft2((g - g.mean()) * win))
    p = (f.real ** 2 + f.imag ** 2)

    # fftfreq is in cycles per sample, so 0.5 is Nyquist; doubling puts Nyquist
    # at 1.0 on each axis. Only the disc r <= 1 is sampled in every direction --
    # the corners of the transform reach sqrt(2) and are not comparable.
    fy = np.fft.fftshift(np.fft.fftfreq(h))[:, None] * 2.0
    fx = np.fft.fftshift(np.fft.fftfreq(w))[None, :] * 2.0
    r = np.sqrt(fy ** 2 + fx ** 2)

    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.digitize(r.ravel(), edges) - 1
    flat = p.ravel()
    keep = (idx >= 0) & (idx < bins)
    total = np.bincount(idx[keep], weights=flat[keep], minlength=bins)
    count = np.bincount(idx[keep], minlength=bins).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    centres = 0.5 * (edges[:-1] + edges[1:])
    return centres.astype(np.float64), mean


def block_structure(g, max_step=6):
    """Find a pixel replication lattice: the one enlargement the spectrum misses.

    A nearest neighbour or "pixel doubled" blow up does not band limit anything.
    It replicates the spectrum instead of suppressing it, so the power runs all
    the way to Nyquist and `effective_resolution` reports CARRIES on a picture
    that plainly carries nothing -- measured on a 2x nearest enlargement, knee
    0.99 against the Lanczos version's 0.57.

    What it does leave is an exact lattice: for a step of N, N-1 of every N
    neighbouring column differences are IDENTICALLY zero. That is a decisive
    test with no threshold worth arguing about. Measured ratio of the quiet
    parity to the loud one: 0.000 on a nearest 2x, 0.860 on a Lanczos 2x, 0.998
    on a native frame.
    """
    a = gray(g)
    out = {"step": None, "ratio": None, "note": ""}
    dc = np.abs(np.diff(a, axis=1)).mean(axis=0)
    dr = np.abs(np.diff(a, axis=0)).mean(axis=1)
    best = None
    for step in range(2, int(max_step) + 1):
        for d in (dc, dr):
            if len(d) < step * 8:
                continue
            groups = [d[k::step].mean() for k in range(step)]
            quiet, loud = min(groups), max(groups)
            ratio = float(quiet / loud) if loud > 0 else 1.0
            if best is None or ratio < best[1]:
                best = (step, ratio)
    if best is None:
        return out
    out["step"], out["ratio"] = best[0], round(best[1], 5)
    # 0.02 is far above the exact zero a replication lattice gives and far below
    # anything a real resampler or a real picture produces.
    if best[1] < 0.02:
        out["note"] = (f"A {best[0]}x pixel replication lattice is present: "
                       f"{best[0] - 1} of every {best[0]} neighbouring differences "
                       "are zero. This frame was blown up by pixel doubling, and "
                       "it carries none of the detail its raster claims.")
    return out


def effective_resolution(g, raster=None, drop_db=DROP_DB, bins=BINS):
    """Where the spectrum leaves its own power law, and what that raster is.

    Returns the knee as a fraction of Nyquist, the raster that fraction implies,
    a verdict, and the two guards that decide whether the number means anything.
    """
    r, p = radial_power(g, bins=bins)
    ok = np.isfinite(p) & (p > 0)
    lp = np.full_like(r, np.nan)
    lp[ok] = 10.0 * np.log10(p[ok])

    band = ok & (r >= FIT_LO) & (r <= FIT_HI)
    out = {"knee": None, "verdict": "UNDETERMINED", "fit_rms_db": None,
           "curvature_db": None, "band_fall_db": None,
           "slope_db_per_decade": None, "declared_raster": None,
           "effective_raster": None, "consistent_with": None,
           "lattice": None, "note": ""}
    if raster:
        out["declared_raster"] = f"{int(raster[0])}x{int(raster[1])}"
    if band.sum() < 6:
        out["note"] = ("The fit band holds fewer than six usable bins, which "
                       "happens on a very small frame. Nothing can be fitted.")
        return out

    x, y = np.log10(r[band]), lp[band]
    coeff = np.polyfit(x, y, 1)
    pred = np.polyval(coeff, np.log10(np.maximum(r, 1e-6)))
    fit_rms = float(np.sqrt(np.mean((y - np.polyval(coeff, x)) ** 2)))
    curv = float(np.polyfit(x, y, 2)[0])
    out["fit_rms_db"] = round(fit_rms, 2)
    out["curvature_db"] = round(curv, 2)
    out["slope_db_per_decade"] = round(float(coeff[0]), 2)

    top = ok & (r > FIT_HI)
    fall = float(np.nanmax(lp[band]) - np.nanmin(lp[top])) if top.any() else 0.0
    out["band_fall_db"] = round(fall, 2)

    if fit_rms > MAX_FIT_RMS_DB:
        out["note"] = (f"The mid band is not a power law (fit RMS {fit_rms:.2f} dB, "
                       f"limit {MAX_FIT_RMS_DB}), so extrapolating it would be a "
                       "guess. Measure on a frame with ordinary texture.")
        return out
    if abs(curv) > MAX_CURV_DB:
        out["note"] = (f"The mid band is curved, not straight ({curv:.1f} dB per "
                       f"decade squared, limit {MAX_CURV_DB}), so the fit is "
                       "sitting inside a roll off rather than on the law above "
                       "it. Extrapolating it would be measuring the softness "
                       "through the softness.")
        return out
    if fall < MIN_BAND_FALL_DB:
        out["note"] = (f"The frame has almost no spectrum to read (it falls only "
                       f"{fall:.2f} dB across the band). A flat card, a heavy "
                       "defocus or a fog frame cannot be measured this way.")
        return out

    # The knee is the lowest frequency above the fit band where the spectrum has
    # gone `drop_db` below its own law AND STAYS there. Requiring it to stay is
    # what keeps a single notch -- a moire null, a compression artefact -- from
    # being read as the end of the picture's detail.
    deficit = pred - lp
    knee = 1.0
    cand = np.where(ok & (r > FIT_HI) & (deficit >= drop_db))[0]
    for i in cand:
        rest = ok & (np.arange(len(r)) >= i)
        if np.all(deficit[rest] >= drop_db * 0.5):
            knee = float(r[i])
            break
    out["knee"] = round(knee, 4)

    if raster:
        w, h = int(raster[0]), int(raster[1])
        out["effective_raster"] = f"{int(round(w * knee))}x{int(round(h * knee))}"

    lattice = block_structure(g)
    out["lattice"] = lattice
    if lattice.get("note"):
        # The lattice is an exact fact and the spectrum is an inference, so when
        # they disagree the exact fact wins.
        out["verdict"] = "SHORT"
        step = lattice["step"]
        out["knee"] = round(1.0 / step, 4)
        if raster:
            out["effective_raster"] = (f"{int(round(raster[0] / step))}x"
                                       f"{int(round(raster[1] / step))}")
            out["consistent_with"] = f"{out['effective_raster']} pixel doubled {step}x"
        out["note"] = lattice["note"]
        return out

    if knee >= 0.85:
        out["verdict"] = "CARRIES"
        out["note"] = ("Detail runs to the frame's own Nyquist. The raster is "
                       "real and an enlargement would be adding, not recovering.")
        return out

    out["verdict"] = "SHORT"
    # A resampler stops at a simple ratio of the raster, and it stops through a
    # transition band, so the knee sits between the ratio and 1.25 times it.
    # Largest ratio first, so the reading makes the smallest claim it can.
    for name, ratio in (("1.33x", 0.75), ("1.5x", 1 / 1.5), ("2x", 0.5),
                        ("3x", 1 / 3.0), ("4x", 0.25)):
        if ratio <= knee <= ratio * KNEE_BIAS_MAX:
            if raster:
                sw, sh = int(round(raster[0] * ratio)), int(round(raster[1] * ratio))
                out["consistent_with"] = (f"a {sw}x{sh} source enlarged {name}, "
                                          "or a lens or codec that stops there")
            else:
                out["consistent_with"] = f"an enlargement of about {name}"
            break
    out["note"] = (
        f"Detail stops at {knee:.2f} of Nyquist. This measurement cannot tell an "
        "enlargement from a soft lens or from heavy compression: all three "
        "suppress the same band, and the knee is an UPPER bound on the source's "
        "own Nyquist because a resampler rolls off rather than cutting. What it "
        "does settle is that there is nothing above that frequency for a "
        "resampler to find, so any further enlargement is invention, not "
        "recovery.")
    return out


# ---------------------------------------------------------------- detail bands


def band_detail(a, sigma=1.5, bands=4):
    """Fine band deviation per luminance band.

    One global detail figure hides the fault that matters, because a detail
    model's SIGN flips with the material: on one job it invented texture on a
    cloud sky at 1.86x and deleted it from a star field at 0.61x, in the same
    picture. Splitting by luminance is the cheapest split that separates sky
    from ground without anybody drawing a horizon.
    """
    import cv2
    g = gray(a).astype(np.float32)
    lo = cv2.GaussianBlur(g, (0, 0), float(sigma))
    fine = g - lo
    qs = np.quantile(g, np.linspace(0.0, 1.0, bands + 1))
    qs[0] -= 1e-6
    out = []
    for i in range(bands):
        m = (g > qs[i]) & (g <= qs[i + 1])
        n = int(m.sum())
        out.append({"band": i + 1,
                    "luma_range": [round(float(qs[i]), 4), round(float(qs[i + 1]), 4)],
                    "pixels": n,
                    "fine_std": round(float(fine[m].std()), 6) if n > 32 else None})
    return out


# ---------------------------------------------------------------- time


def flow_dis(src, dst):
    """The field that PULLS `src` onto `dst`'s grid, DIS medium.

    The sign convention is the whole of this function and it is worth stating
    once, because getting it backwards costs nothing visible and everything
    numerically: OpenCV's `calc(f, g)` returns F with f(y,x) ~ g(y+Fy, x+Fx),
    so the field that resamples `src` into `dst`'s frame is `calc(dst, src)`,
    not `calc(src, dst)`. Measured on an exact seven by three pixel translation
    with known ground truth: the right way round leaves 0.002 mean absolute
    error, the wrong way round leaves 0.082, and 0.082 is indistinguishable from
    two unrelated frames -- so a sign slip here does not look like a bug, it
    looks like a film that boils.

    DIS rather than Farneback because it is both faster and better on the large
    displacements an advertising cut actually contains, and rather than a
    learned flow because this must run with no model download and no GPU.
    """
    import cv2
    a = np.clip(gray(dst) * 255.0, 0, 255).astype(np.uint8)
    b = np.clip(gray(src) * 255.0, 0, 255).astype(np.uint8)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    return dis.calc(a, b, None)


def warp_by_flow(img, flow):
    """Pull img along flow. Out of frame samples come back as NaN, not as edge."""
    import cv2
    a = np.asarray(img, dtype=np.float32)
    h, w = flow.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    mx = gx + flow[..., 0]
    my = gy + flow[..., 1]
    out = cv2.remap(a, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                    borderValue=0.0)
    inside = (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
    return out, inside


def flow_validity(fwd, bwd, border=8, tol=1.0):
    """Where the flow can be believed: forward and backward must agree.

    An occlusion has no correspondence at all, so the flow there is whatever the
    solver's smoothness term invented, and the warping error there measures the
    solver rather than the picture. Dropping those pixels is what makes the
    number comparable between two candidates instead of merely large on both.
    """
    import cv2
    h, w = fwd.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    bx = cv2.remap(bwd[..., 0], gx + fwd[..., 0], gy + fwd[..., 1],
                   cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    by = cv2.remap(bwd[..., 1], gx + fwd[..., 0], gy + fwd[..., 1],
                   cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    err = np.sqrt((fwd[..., 0] + bx) ** 2 + (fwd[..., 1] + by) ** 2)
    mag = np.sqrt(fwd[..., 0] ** 2 + fwd[..., 1] ** 2)
    ok = err <= (tol + 0.05 * mag)
    if border > 0:
        ok[:border, :] = ok[-border:, :] = False
        ok[:, :border] = ok[:, -border:] = False
    return ok


def warping_error(pairs, flows=None, border=8):
    """Mean absolute error after warping each frame onto the next.

    `pairs` is a sequence of (frame_t, frame_t1) as HxWx3 float arrays. `flows`
    lets a CONTROL's flow field be reused on a candidate, which is the whole
    point: measure both against ONE motion model, so the only thing that can
    differ is the candidate's own stability. Solving the flow separately on each
    would let a boiling candidate move the yardstick it is measured with.
    """
    errs, valid = [], []
    made = []
    for i, (a, b) in enumerate(pairs):
        if flows is not None:
            fwd, ok = flows[i]
        else:
            fwd = flow_dis(a, b)          # pulls a onto b's grid
            bwd = flow_dis(b, a)          # pulls b onto a's grid
            ok = flow_validity(fwd, bwd, border=border)
            made.append((fwd, ok))
        warped, inside = warp_by_flow(np.asarray(a, dtype=np.float32), fwd)
        m = ok & inside
        n = int(m.sum())
        if n < 1024:
            continue
        d = np.abs(warped - np.asarray(b, dtype=np.float32))
        errs.append(float(d[m].mean()))
        valid.append(n / float(m.size))
    if not errs:
        return {"mae": None, "pairs": 0, "valid_fraction": None,
                "flows": made or None}
    return {"mae": float(np.mean(errs)), "pairs": len(errs),
            "valid_fraction": round(float(np.mean(valid)), 4),
            "per_pair": [round(e, 6) for e in errs],
            "flows": made or None}


# The value the incoherence statistic takes when two detail fields are entirely
# unrelated. For two independent zero mean fields of equal spread, the mean
# absolute difference is E|a-b| = 2s/sqrt(pi) and the normaliser is
# E|a| + E|b| = 2s*sqrt(2/pi), so the ratio is 1/sqrt(2) whatever the amplitude.
# That fixed ceiling is what makes the number readable with no control to
# compare against: 0 is detail that moves with the picture, 0.707 is detail
# re-invented from nothing on every frame.
INCOHERENCE_INDEPENDENT = 0.7071

# A control that already reads this incoherent has no grip on the shot: the flow
# failed and the floor is the ceiling. Half of fully independent.
INCOHERENCE_UNGRIPPED = 0.35

# How much MORE incoherent than the neutral enlargement a candidate may be.
# Measured on real encodes: a Lanczos 2x reads +0.0005 and a downscale +0.0002,
# while a per frame model reads +0.60; and on synthetic ground truth a barely
# visible 1.2 per cent per frame wobble reads +0.029. So the stable limit sits
# an order of magnitude above the honest cases and an order below the mild
# fault, at three per cent of the independent ceiling, and boiling starts at
# twelve per cent of it.
STABLE_EXCESS = round(0.03 * INCOHERENCE_INDEPENDENT, 4)
BOILING_EXCESS = round(0.12 * INCOHERENCE_INDEPENDENT, 4)


def detail_incoherence(seq, flows, sigma=1.0):
    """How much of the fine detail fails to travel with the picture.

    Scale free by construction, so it can be read on a delivered file with no
    source to compare against: 0 means the detail is part of the scene, 0.707
    means it was invented independently on every frame, which is the signature
    of a stills upscaler run down a clip.

    Measured on synthetic ground truth with an exact translation: a control
    reads 0.028, and a per frame model adding detail at 1.2 per cent amplitude
    reads 0.057 while a temporally coherent model adding the SAME amplitude
    stays at 0.028.
    """
    import cv2
    vals = []
    for i in range(len(seq) - 1):
        fwd, ok = flows[i]
        a = np.asarray(seq[i], dtype=np.float32)
        b = np.asarray(seq[i + 1], dtype=np.float32)
        da = a - cv2.GaussianBlur(a, (0, 0), float(sigma))
        db = b - cv2.GaussianBlur(b, (0, 0), float(sigma))
        wa, inside = warp_by_flow(da, fwd)
        m = ok & inside
        if int(m.sum()) < 1024:
            continue
        den = np.abs(da)[m].mean() + np.abs(db)[m].mean()
        vals.append(float(np.abs(wa - db)[m].mean() / max(den, 1e-9)))
    if not vals:
        return None
    return float(np.mean(vals))


def temporal_stability(control, candidate, border=8, sigma=1.0):
    """Both time measurements, on one shared motion model.

    The shared flow is the point. Solving the flow separately on each sequence
    would let a boiling candidate move the yardstick it is being measured with,
    so the flow is solved ONCE on the control and applied to both. What differs
    between the two numbers is then only the candidate's own stability.

    The control is the neutral enlargement of the source at the output raster,
    and its warping error is NOT zero and never will be, because flow is wrong
    at every occlusion. Read the ratio, never the level: that is the generation
    floor argument moved from code levels into time.
    """
    if len(control) != len(candidate):
        raise ValueError(f"{len(control)} control frames against "
                         f"{len(candidate)} candidate frames")
    if len(control) < 2:
        raise ValueError("temporal stability needs at least two frames")
    base = warping_error(list(zip(control[:-1], control[1:])), border=border)
    flows = base.pop("flows")
    if not flows or base["mae"] is None:
        return {"verdict": "UNDETERMINED",
                "note": "The flow found too few pixels it could believe."}
    cand = warping_error(list(zip(candidate[:-1], candidate[1:])), flows=flows)
    cand.pop("flows", None)
    ratio = cand["mae"] / base["mae"] if base["mae"] > 0 else None
    inc_c = detail_incoherence(control, flows, sigma=sigma)
    inc_k = detail_incoherence(candidate, flows, sigma=sigma)
    out = {"control_mae": round(base["mae"], 6),
           "candidate_mae": round(cand["mae"], 6),
           "ratio": round(ratio, 4) if ratio is not None else None,
           "pairs": base["pairs"],
           "valid_fraction": base["valid_fraction"],
           "control_incoherence": round(inc_c, 4) if inc_c is not None else None,
           "candidate_incoherence": round(inc_k, 4) if inc_k is not None else None,
           "independent_at": INCOHERENCE_INDEPENDENT}

    excess = None if (inc_c is None or inc_k is None) else inc_k - inc_c
    out["excess_incoherence"] = round(excess, 4) if excess is not None else None

    # The instrument has to be able to see before its reading is worth having.
    if base["valid_fraction"] is None or base["valid_fraction"] < 0.5:
        out["verdict"] = "UNDETERMINED"
        out["note"] = (f"The flow could only be believed on "
                       f"{(base['valid_fraction'] or 0) * 100:.0f} per cent of the "
                       "frame, so there is not enough ground to measure on. "
                       "Try a quieter span of the clip.")
        return out
    if inc_c is None or excess is None:
        out["verdict"] = "UNDETERMINED"
        out["note"] = "There was not enough believable ground to read the detail on."
        return out
    if inc_c > INCOHERENCE_UNGRIPPED:
        out["verdict"] = "UNDETERMINED"
        out["note"] = (f"The control's own detail already reads {inc_c:.2f} "
                       f"incoherent against a fully independent {INCOHERENCE_INDEPENDENT}, "
                       "which means the flow never locked onto this motion at "
                       "all. Anything measured against that floor would be noise. "
                       "Measure on a span with trackable motion.")
        return out

    # The verdict is driven by the EXCESS incoherence, not by the warping error
    # ratio, and that choice was forced by measurement. The ratio has no fixed
    # scale: its denominator is how trackable the shot is, so on a clean
    # synthetic pan the control's error is 7e-05 and a candidate reads 669 times
    # it, while the same fault on a hand held plate reads 3 times it. A
    # threshold on an unbounded ratio is a threshold on the shot. The excess is
    # bounded by construction -- 0 is detail that travels with the picture,
    # 0.707 is detail invented afresh every frame -- so a number on it means the
    # same thing on every shot. Measured: a neutral resample +0.0005, a
    # downscale +0.0002, a per frame model +0.60. The ratio is still reported,
    # because when the floor IS meaningful it is the more familiar number.
    if excess <= STABLE_EXCESS:
        out["verdict"] = "STABLE"
        out["note"] = (f"The candidate's detail is {excess:+.4f} more incoherent "
                       "than the neutral enlargement's, against a scale where "
                       f"{INCOHERENCE_INDEPENDENT} would be detail invented from "
                       "nothing every frame. It added no flicker of its own.")
    elif excess <= BOILING_EXCESS:
        out["verdict"] = "MARGINAL"
        out["note"] = (f"The candidate's detail is {excess:+.4f} more incoherent "
                       "than the neutral enlargement's. Something is moving that "
                       "should not be. Look at a flat surface at 1:1 before "
                       "delivering it.")
    else:
        out["verdict"] = "BOILS"
        out["note"] = (f"The candidate's detail is {excess:+.4f} more incoherent "
                       f"than the neutral enlargement's, on a scale where "
                       f"{INCOHERENCE_INDEPENDENT} is invented from nothing. This "
                       "is what a stills upscaler run frame by frame down a clip "
                       "looks like: the detail is re-invented every picture, so "
                       "it crawls. A per frame model cannot be fixed by "
                       "settings; the fix is a model that sees neighbours.")
    return out
