#!/usr/bin/env python3
"""Tracking: measure the motion, do not invent it.

Four rules, each of which cost a version of a real film.

**Detect the plate's cadence FIRST.** A generated plate is very often a higher
rate conformed down by dropping pictures, so the scene lurches on a fixed beat.
That lurch is real motion the composite must reproduce. Smooth it away and the
content slides against the real bezel on exactly that beat. Everything in this
module that smooths, smooths against TRUE TIME, never against frame index.

**Never smooth with a model that cannot represent the motion.** A cubic fitted
per corner across a clip has four degrees of freedom for the whole clip, and
real plate motion is usually a SETTLE, which a cubic cannot represent: it
deletes the motion and substitutes a monotonic slide. Measured cost on one job,
6.91 px worst error, visible by eye. `smooth` refuses a global polynomial and
says why.

**Never score a track against a smoothed copy of itself.** Every residual in
here is measured against the RAW per frame measurements.

**Two independent measurements agreeing is what certifies a track.** One
measurement plus a threshold certifies nothing. So the solve is done twice by
methods that share no assumptions, dense photometric (ECC) and sparse geometric
(features plus a robust estimator), and the certificate is the agreement
between them in pixels at the region's own corners.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _geom as G  # noqa: E402
import _pix as P  # noqa: E402

MODELS = ("translation", "euclidean", "affine", "homography")

# ECC at half resolution costs about 0.13 px against the full resolution solve
# and runs roughly twenty times faster, which is what makes an iterated solve
# affordable. The cost is measured per job by `track --scale-cost`.
DEFAULT_SCALE = 0.5


# ---------------------------------------------------------------- cadence


def motion_series(clip, scale=0.25, limit=None, bits=8, region=None):
    """How far the picture MOVED between frames, in pixels, by phase correlation.

    Not the mean absolute frame difference. A pixel difference saturates: double
    the displacement and a textured scene gives well short of double the
    difference, so the beat that a cadence test is looking for gets squashed
    into the noise. Phase correlation returns a displacement, which is linear in
    the time step, which is the whole point.
    """
    import cv2

    info = P.clip_info(clip)
    w = max(64, int(info["width"] * scale))
    h = max(64, int(info["height"] * scale))
    if region is not None:
        x, y, rw, rh = [int(round(v * scale)) for v in region]
        x, y = max(0, x), max(0, y)
        rw, rh = max(32, min(rw, w - x)), max(32, min(rh, h - y))
        box = (x, y, rw, rh)
        win = cv2.createHanningWindow((rw, rh), cv2.CV_32F)
    else:
        box = None
        win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    prev = None
    mags = []
    for idx, img in P.read_frames(clip, scale=(w, h), bits=bits):
        g = np.ascontiguousarray((img.rgb @ P.LUMA_709).astype(np.float32))
        if box is not None:
            x, y, rw, rh = box
            g = np.ascontiguousarray(g[y:y + rh, x:x + rw])
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g, win)
            # Report in SCALED pixels; the caller converts. Phase correlation is
            # good to roughly a fifth of a pixel in whatever raster it was given,
            # so the raster it was given is what decides whether a measurement is
            # possible at all.
            mags.append(float(np.hypot(dx, dy)))
        prev = g
        if limit is not None and idx + 1 >= limit:
            break
    return np.array(mags, dtype=np.float64), info


MIN_MOTION_PX = 1.5          # below this, phase correlation is reading noise
WANT_MOTION_PX = 2.5         # the motion a scaled copy needs to still show a beat


def cadence(clip, scale=1.0, limit=None, region=None):
    """Is this plate a higher rate conformed down by dropping pictures?

    A 30 fps plate conformed to 24 by dropping one picture in five leaves four
    single steps and one double step per cycle, so the picture lurches about 2x
    every fourth output frame. Frame differencing alone cannot see it because
    every frame IS different; what gives it away is the periodic size of the
    difference.

    Returns the best period, how much bigger the lurch step is, and a TRUE TIME
    vector, which is what anything that smooths must smooth against.
    """
    mags, info = motion_series(clip, scale=scale, limit=limit,
                               region=region)
    rescaled = False
    # A cadence lives in the SIZE of each step, so the step has to be bigger
    # than the measurement's own error. Phase correlation resolves about a fifth
    # of a pixel of whatever raster it was handed, so a quarter size copy of a
    # plate that drifts 1.6 px per frame is measuring nothing. Notice, and redo
    # it at full resolution rather than reporting the noise.
    if scale < 1.0 and len(mags) and np.median(mags) < WANT_MOTION_PX:
        mags, info = motion_series(clip, scale=1.0, limit=limit, region=region)
        scale, rescaled = 1.0, True
    mags = mags / scale
    n = len(mags)
    out = {"clip": info["path"], "frames": int(info["frames"]),
           "declared_rate": info["rate_str"], "steps_measured": int(n),
           "measured_on": ("region " + ",".join(str(int(v)) for v in region)
                           if region else "the whole frame"),
           "measured_at_scale": float(scale),
           "rescaled_because_motion_was_small": rescaled}
    if n < 12:
        out.update({"verdict": "UNPROVEN",
                    "reason": f"only {n} inter frame steps, need at least 12"})
        return out
    if np.median(mags) < MIN_MOTION_PX:
        out.update({"verdict": "UNMEASURABLE",
                    "motion_px_median": float(np.median(mags)),
                    "reason": (f"the picture moves {np.median(mags):.2f} px per "
                               f"frame where this was measured, under the "
                               f"{MIN_MOTION_PX} px this measurement needs. "
                               "Either the shot is locked off, or only PART of "
                               "the frame moves and this is reading the still "
                               "part: pass --region over the thing that moves.")})
        return out

    # Take the slow variation out first. A real shot speeds up and slows down,
    # so a small step during a fast passage can be larger in absolute terms than
    # a lurch during a slow one, and the beat disappears into that trend. Divide
    # each step by a running median of its neighbours and what is left is the
    # beat, if there is one.
    from scipy.ndimage import median_filter
    trend = median_filter(mags, size=min(9, n | 1), mode="nearest")
    r = mags / np.maximum(trend, 1e-9)

    best = None
    for period in range(2, min(13, n // 3 + 1)):
        for phase in range(period):
            sel = np.zeros(n, dtype=bool)
            sel[phase::period] = True
            if sel.sum() < 3 or (~sel).sum() < 3:
                continue
            big, small = r[sel], r[~sel]
            if small.mean() <= 1e-9:
                continue
            ratio = float(big.mean() / small.mean())
            # A t like statistic on the detrended series: how many standard
            # errors apart the two populations are. This is what tells a real
            # beat from a coincidence, and it does not demand that the two
            # populations never overlap, which no real plate manages.
            se = np.sqrt(big.var(ddof=1) / len(big) + small.var(ddof=1) / len(small))
            sep = float((big.mean() - small.mean()) / max(se, 1e-9))
            # Prefer the SHORTEST period that explains the beat: a period twice
            # as long catches every second lurch and leaves the others in the
            # "small" population, which shows up as a worse ratio, but ties do
            # happen and the shorter one is the real cadence.
            score = float(ratio * sep / np.sqrt(period))
            if best is None or score > best["score"]:
                best = {"period": period, "phase": phase, "ratio": ratio,
                        "separation": sep, "score": score,
                        "detrended_big_mean": float(big.mean()),
                        "detrended_small_mean": float(small.mean()),
                        "raw_big_mean_px": float(mags[sel].mean()),
                        "raw_small_mean_px": float(mags[~sel].mean())}

    out.update({k: v for k, v in (best or {}).items()})
    out["motion_px_median"] = float(np.median(mags))
    if best and best["ratio"] > 1.30 and best["separation"] > 3.0:
        p, ph = best["period"], best["phase"]
        # True time: the lurch frame covers `ratio` times as much source time.
        step = np.ones(int(info["frames"]) or n + 1)
        step[ph + 1::p] = best["ratio"]
        t = np.concatenate([[0.0], np.cumsum(step[:-1])])
        t = t / t[-1] if t[-1] > 0 else t
        out.update({"verdict": "CONFORMED",
                    "true_time_normalised": t.tolist(),
                    "implied_source_rate": _implied_rate(info["rate"], p, best["ratio"]),
                    "reason": (f"every {p} frames the picture moves "
                               f"{best['ratio']:.2f}x as far, cleanly separated. "
                               "This is real motion. Reproduce it, and smooth "
                               "against true time, never against frame index.")})
    else:
        out.update({"verdict": "NATIVE",
                    "true_time_normalised": None,
                    "reason": ("no periodic lurch above the plate's own noise "
                               "(best candidate: "
                               f"{(best or {}).get('ratio', 0):.2f}x at "
                               f"{(best or {}).get('separation', 0):.1f} standard "
                               "errors, and it takes 1.30x at 3.0 to call it); "
                               "frame index and true time are the same thing here")})
    return out


def _implied_rate(rate, period, ratio):
    """What source rate a period and a lurch size imply, as a plain string."""
    kept = period - 1 + ratio
    try:
        return f"about {float(rate) * kept / period:.3f} fps conformed to {float(rate):.3f}"
    except Exception:
        return None


# ---------------------------------------------------------------- features


def _gray(img):
    return np.clip(img.rgb @ P.LUMA_709.astype(np.float32), 0, 1).astype(np.float32)


def feature_pairs(ref_img, img, mask=None, max_features=8000, ratio=0.80,
                  contrast=0.015):
    """Sparse correspondences between two frames, inside the region only.

    SIFT plus Lowe's ratio test. Sparse and geometric, so it shares no
    assumption with the dense photometric solve; that is the whole point of
    having it.
    """
    import cv2

    a = (np.clip(_gray(ref_img), 0, 1) * 255).astype(np.uint8)
    b = (np.clip(_gray(img), 0, 1) * 255).astype(np.uint8)
    m = None
    if mask is not None:
        m = (np.asarray(mask) > 0).astype(np.uint8) * 255
    # A lower contrast threshold than the library default. A film plate is not
    # a photograph of a building: bezels, edges and set dressing are often low
    # contrast, and at the default a whole surround can return a dozen points,
    # which is not enough for the second route to be a measurement at all.
    sift = cv2.SIFT_create(nfeatures=max_features, contrastThreshold=contrast)
    ka, da = sift.detectAndCompute(a, m)
    kb, db = sift.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return np.zeros((0, 2)), np.zeros((0, 2))
    bf = cv2.BFMatcher()
    raw = bf.knnMatch(da, db, k=2)
    src, dst = [], []
    for pair in raw:
        if len(pair) < 2:
            continue
        best, second = pair
        if best.distance < ratio * second.distance:
            src.append(ka[best.queryIdx].pt)
            dst.append(kb[best.trainIdx].pt)
    return np.array(src, dtype=np.float64), np.array(dst, dtype=np.float64)


DOF = {"translation": 2, "euclidean": 4, "affine": 6, "homography": 8}


def min_support(model):
    """How many inliers a model needs before its answer is a measurement.

    Four points determine a homography exactly, which is precisely why four
    points do not MEASURE one: with no redundancy there is no residual, so
    nothing says whether the answer is right. Three times the degrees of freedom
    is the rule used here, and it is why a thin bezel with a dozen features on
    it cannot certify anything, however good the picture looks.
    """
    return max(12, 3 * DOF[model])


def fit_model(src, dst, model, method=None):
    """Fit one motion model robustly and return a 3x3 plus the inlier mask.

    MAGSAC++ where OpenCV has it: it is markedly less sensitive to the inlier
    threshold than plain RANSAC, which matters here because the right threshold
    depends on the plate's grain and nobody knows it in advance.
    """
    import cv2

    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}")
    s = np.asarray(src, dtype=np.float64).reshape(-1, 1, 2)
    d = np.asarray(dst, dtype=np.float64).reshape(-1, 1, 2)
    need = {"translation": 1, "euclidean": 2, "affine": 3, "homography": 4}[model]
    if len(s) < max(need, 4):
        return None, None

    if model == "homography":
        meth = method if method is not None else getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
        H, inl = cv2.findHomography(s, d, meth, 3.0, maxIters=5000, confidence=0.999)
        if H is None:
            return None, None
        return H.astype(np.float64), (inl.ravel() > 0 if inl is not None else None)

    if model == "affine":
        M, inl = cv2.estimateAffine2D(s, d, method=cv2.RANSAC,
                                      ransacReprojThreshold=3.0, maxIters=5000)
    elif model == "euclidean":
        M, inl = cv2.estimateAffinePartial2D(s, d, method=cv2.RANSAC,
                                             ransacReprojThreshold=3.0,
                                             maxIters=5000)
    else:  # translation
        diff = d.reshape(-1, 2) - s.reshape(-1, 2)
        # Median is the robust estimator for a pure shift.
        t = np.median(diff, axis=0)
        resid = np.linalg.norm(diff - t, axis=1)
        M = np.array([[1.0, 0.0, t[0]], [0.0, 1.0, t[1]]])
        inl = (resid < 3.0).astype(np.uint8).reshape(-1, 1)
    if M is None:
        return None, None
    H = np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]])
    return H, (inl.ravel() > 0 if inl is not None else None)


def model_select(src, dst, folds=5, seed=11):
    """Which model does this plate's motion actually need? Cross validated.

    Fit on four fifths of the correspondences, measure on the fifth that was
    left out, rotate. A model that is too rich fits the noise and its HELD OUT
    error goes up, which is the only way to tell over modelling from real
    motion. Then take the SIMPLEST model whose held out error is within one
    standard error of the best: an object that travels may only need affine, and
    an object LIFTED toward the lens grows while its centre barely moves, which
    affine cannot represent at all.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = len(src)
    if n < 20:
        return {"verdict": "UNPROVEN", "n_pairs": int(n),
                "reason": "fewer than 20 correspondences; not enough to cross "
                          "validate a model choice"}
    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    cuts = np.array_split(order, folds)

    results = {}
    for model in MODELS:
        errs = []
        for k in range(folds):
            test = cuts[k]
            train = np.concatenate([cuts[j] for j in range(folds) if j != k])
            H, _ = fit_model(src[train], dst[train], model)
            if H is None:
                errs = None
                break
            pred = G.apply_h(H, src[test])
            e = np.linalg.norm(pred - dst[test], axis=1)
            # Robust: the correspondence set still holds outliers the robust
            # fit rejected, and a single wild match would decide the choice.
            errs.append(float(np.median(e)))
        if errs is None:
            results[model] = None
            continue
        errs = np.array(errs)
        results[model] = {"heldout_median_px": float(errs.mean()),
                          "fold_spread_px": float(errs.std()),
                          "standard_error_px": float(errs.std() / np.sqrt(folds))}

    # Does ONE rigid surface explain this region at all? Fit the richest model
    # on everything and look at how much it had to throw away. A single planar
    # surface keeps most of its correspondences; a region straddling two things
    # that move independently, a screen and the room behind it, does not, and
    # then the model choice is measuring whichever surface brought more
    # features. That is failure 19 wearing a different hat.
    H_all, inl_all = fit_model(src, dst, "homography")
    inlier_fraction = (float(inl_all.mean()) if inl_all is not None else None)

    usable = {m: r for m, r in results.items() if r}
    if not usable:
        return {"verdict": "UNPROVEN", "models": results,
                "reason": "no model could be fitted on these correspondences"}
    best = min(usable, key=lambda m: usable[m]["heldout_median_px"])
    thresh = (usable[best]["heldout_median_px"] +
              usable[best]["standard_error_px"])
    for m in MODELS:
        if m in usable and usable[m]["heldout_median_px"] <= thresh:
            chosen = m
            break
    else:
        chosen = best
    out = {"verdict": "MEASURED", "chosen": chosen, "best": best,
           "n_pairs": int(n), "folds": folds, "models": results,
           "inlier_fraction": inlier_fraction,
           "reason": (f"{chosen} is the simplest model within one standard "
                      f"error of the best ({best}) on held out "
                      f"correspondences")}
    if inlier_fraction is not None and inlier_fraction < 0.6:
        out["one_surface"] = "NO"
        out["warning"] = (
            f"only {100 * inlier_fraction:.0f} per cent of the correspondences "
            "fit any single planar motion. This region is not one rigid thing: "
            "it straddles two surfaces that move differently, and the model "
            "chosen above describes whichever of them brought more features, "
            "which may not be the one you meant to track. Narrow the region to "
            "the surface itself, or to a surround that is RIGID with it.")
    else:
        out["one_surface"] = "YES"
    return out


# ---------------------------------------------------------------- ecc


def ecc_solve(ref_img, img, init=None, model="homography", mask=None,
              iters=200, eps=1e-6, gauss=5, scale=1.0):
    """Dense photometric alignment, reference to frame, by ECC maximisation.

    ECC maximises the correlation coefficient between zero mean, normalised
    intensity vectors, so it is invariant to a change of gain and a change of
    offset in the picture. That matters on a real plate: the lamp flickers, the
    exposure ramps, and an l2 residual would chase the brightness instead of the
    motion.

    The warp maps REFERENCE coordinates to FRAME coordinates, which is the
    direction a composite needs.
    """
    import cv2

    a = _gray(ref_img)
    b = _gray(img)
    if scale != 1.0:
        a = P.resize(a, (int(a.shape[1] * scale), int(a.shape[0] * scale)))
        b = P.resize(b, (int(b.shape[1] * scale), int(b.shape[0] * scale)))
    S = np.diag([scale, scale, 1.0])
    Si = np.diag([1.0 / scale, 1.0 / scale, 1.0])

    motion = {"translation": cv2.MOTION_TRANSLATION,
              "euclidean": cv2.MOTION_EUCLIDEAN,
              "affine": cv2.MOTION_AFFINE,
              "homography": cv2.MOTION_HOMOGRAPHY}[model]

    W0 = np.eye(3) if init is None else np.asarray(init, dtype=np.float64)
    Ws = S @ W0 @ Si
    if model == "homography":
        warp = Ws.astype(np.float32)
    else:
        warp = Ws[:2].astype(np.float32)

    m = None
    if mask is not None:
        mm = (np.asarray(mask) > 0).astype(np.uint8)
        if scale != 1.0:
            mm = (P.resize(mm.astype(np.float32),
                           (b.shape[1], b.shape[0])) > 0.5).astype(np.uint8)
        # The mask marks the region in the FRAME, so carry it there with the
        # initial warp before handing it over.
        if model == "homography":
            mm = cv2.warpPerspective(mm, Ws, (b.shape[1], b.shape[0]),
                                     flags=cv2.INTER_NEAREST)
        else:
            mm = cv2.warpAffine(mm, Ws[:2], (b.shape[1], b.shape[0]),
                                flags=cv2.INTER_NEAREST)
        m = (mm > 0).astype(np.uint8) * 255

    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, int(iters), float(eps))
    try:
        cc, warp = cv2.findTransformECC(a, b, warp, motion, crit, m, int(gauss))
    except cv2.error as exc:
        return {"ok": False, "reason": str(exc).strip().splitlines()[-1][:200],
                "warp": None, "cc": None}
    W = np.vstack([warp, [0, 0, 1]]) if warp.shape[0] == 2 else warp
    return {"ok": True, "warp": (Si @ np.asarray(W, dtype=np.float64) @ S),
            "cc": float(cc), "model": model, "scale": scale}


def split_region_certificate(ref_img, img, mask, corners, model, scale):
    """A weaker certificate for a region with no features to match.

    A flat backing has nothing for a feature detector to find, so the sparse
    geometric route cannot run at all. Solving the LEFT half and the RIGHT half
    separately and comparing at the corners is still two measurements, and a
    track that is drifting will not survive it. It is weaker and it is labelled
    weaker, because both halves share the photometric assumption: if the plate's
    brightness ramps, both are wrong the same way.
    """
    m = (np.asarray(mask) > 0).astype(np.uint8)
    ys, xs = np.nonzero(m)
    mid = int((xs.min() + xs.max()) / 2)
    left = m.copy()
    left[:, mid:] = 0
    right = m.copy()
    right[:, :mid] = 0
    if left.sum() < 400 or right.sum() < 400:
        return {"verdict": "UNPROVEN", "route": "split region",
                "reason": "the region is too small to split"}
    # Half a region carries half the constraint. Asking a half for a homography,
    # eight parameters, out of a thin sliver with little texture in it, produces
    # a number that looks like a catastrophic disagreement and is really just an
    # underdetermined solve. Downgrade the model for this route and say so.
    half_model = model if model in ("translation", "euclidean") else "euclidean"
    a = ecc_solve(ref_img, img, model=half_model, mask=left, scale=scale)
    b = ecc_solve(ref_img, img, model=half_model, mask=right, scale=scale)
    if not (a["ok"] and b["ok"]):
        return {"verdict": "UNPROVEN", "route": "split region",
                "reason": "one half of the region would not converge"}
    worst_cc = min(a["cc"], b["cc"])
    if worst_cc < 0.80:
        return {"verdict": "UNPROVEN", "route": "split region",
                "worst_cc": float(worst_cc),
                "reason": (f"a half region only correlated to {worst_cc:.2f}; "
                           "there is not enough signal in half of this shape to "
                           "certify anything with")}
    out = agreement(a["warp"], b["warp"], corners)
    out["route"] = (f"split region ECC at {half_model} (WEAKER: both halves "
                    "share the photometric assumption, and the model is "
                    "downgraded to fit half the constraint)")
    out["worst_cc"] = float(worst_cc)
    return out


def warp_plausible(W, corners, raster, area_ratio=8.0, margin=3.0):
    """Has this solve collapsed? Answered from the warp alone, per frame.

    Not a temporal smoothness test, which would be an assumption about the
    motion. These are things a projective view of a rigid quadrilateral simply
    cannot do: turn itself inside out, cross its own edges, change area by an
    order of magnitude, or land most of a picture away from the picture. A solve
    that does any of them found a maximum of the correlation somewhere that is
    not the object.
    """
    if W is None or not np.all(np.isfinite(W)):
        return {"ok": False, "reason": "the warp is not finite"}
    q = G.apply_h(W, corners)
    if not np.all(np.isfinite(q)):
        return {"ok": False, "reason": "the warped corners are not finite"}

    def _cross(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))

    signs = [np.sign(_cross(q[i - 1], q[i], q[(i + 1) % 4])) for i in range(4)]
    if len(set(s for s in signs if s != 0)) > 1:
        return {"ok": False, "reason": "the warped quad crosses its own edges "
                                       "or turns inside out"}
    def _signed_area(p):
        return 0.5 * sum(p[i][0] * p[(i + 1) % 4][1] -
                         p[(i + 1) % 4][0] * p[i][1] for i in range(4))

    s0, s1 = _signed_area(np.asarray(corners, float)), _signed_area(q)
    if s0 * s1 < 0:
        # The winding flipped, so this solve is looking at the BACK of the
        # panel. A homography search can land there and the correlation can
        # even be respectable on a symmetrical shape.
        return {"ok": False, "reason": "the warped quad is mirrored: this solve "
                                       "has turned the panel around"}
    a0, a1 = abs(s0), abs(s1)
    if a0 <= 1 or not (1.0 / area_ratio <= a1 / a0 <= area_ratio):
        return {"ok": False,
                "reason": f"the region's area changed by {a1 / max(a0, 1):.1f}x, "
                          f"outside the {area_ratio}x a rigid object can do "
                          "without leaving the shot"}
    w, h = raster
    if (q[:, 0].min() < -margin * w or q[:, 0].max() > (1 + margin) * w or
            q[:, 1].min() < -margin * h or q[:, 1].max() > (1 + margin) * h):
        return {"ok": False, "reason": "the warped quad landed off the picture"}
    return {"ok": True}


def agreement(H_a, H_b, corners):
    """The certificate: two independent solves compared at the region's corners.

    Reported in pixels, at the corners, because that is where the error is
    visible and where the client looks. A correlation coefficient and a
    reprojection residual are not comparable; corner positions are.
    """
    if H_a is None or H_b is None:
        return {"verdict": "UNPROVEN", "reason": "one of the two solves failed"}
    pa = G.apply_h(H_a, corners)
    pb = G.apply_h(H_b, corners)
    d = np.linalg.norm(pa - pb, axis=1)
    span = float(np.linalg.norm(np.asarray(corners).max(axis=0) -
                                np.asarray(corners).min(axis=0)))
    if not np.all(np.isfinite(d)) or d.max() > span:
        return {"verdict": "DEGENERATE", "worst_px": float(d.max()),
                "region_span_px": span,
                "reason": ("the two solves disagree by more than the region "
                           "is wide, which is not a disagreement, it is a "
                           "collapse: at least one of them found no motion it "
                           "could measure. A region with no texture in it cannot "
                           "carry a homography.")}
    return {"verdict": "MEASURED", "worst_px": float(d.max()),
            "mean_px": float(d.mean()), "region_span_px": span,
            "per_corner_px": d.tolist()}


# ---------------------------------------------------------------- the track


def texture_coverage(img, mask, above_noise=2.5):
    """How much of the region has anything in it for a feature to lock onto.

    The blind spot of every feature based check: a surface with no texture is
    invisible to it, so "the correspondences all fit one motion" can be true
    while the surface you actually meant to track goes somewhere else entirely.
    That is failure 19. A green panel inside a tracked box is exactly this case,
    and the number below is the warning.
    """
    import cv2

    g = _gray(img)

    def _energy(x):
        gx = cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3)
        return cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), 2.0)

    energy = _energy(g)
    # Against the plate's OWN noise floor, never against an absolute. Every real
    # plate has grain, and grain has a gradient: measured against a fixed number,
    # a flat green panel covered in grain reads as full of texture, which is the
    # opposite of the truth. So estimate what the grain alone contributes and
    # ask what beats it.
    resid = g - cv2.GaussianBlur(g, (0, 0), 1.6)
    noise_floor = float(np.median(_energy(resid)))
    low = max(noise_floor * above_noise, 1e-5)

    m = np.asarray(mask) > 0
    if m.sum() < 16:
        return {"verdict": "UNPROVEN", "reason": "the region is empty"}
    flat = (energy < low) & m
    frac = float(flat.sum() / m.sum())
    out = {"region_px": int(m.sum()), "flat_fraction": frac,
           "noise_floor": noise_floor, "threshold": low,
           "gradient_median": float(np.median(energy[m]))}
    if frac > 0.2:
        out["verdict"] = "BLIND SPOT"
        out["reason"] = (
            f"{100 * frac:.0f} per cent of this region has no texture in it. A "
            "feature based check is blind there, so it can agree perfectly while "
            "the untextured surface goes somewhere else entirely. If that "
            "untextured part is the thing you are tracking, the surround must be "
            "RIGID with it; a room behind a screen is not.")
    else:
        out["verdict"] = "OK"
        out["reason"] = ""
    return out


MIN_CC = 0.80        # below this, ECC found a maximum but not the right one

# The solve settings are a MEASUREMENT, not a default. The often quoted figure
# that half resolution costs about 0.13 px was measured on one job on one
# region; on a thin shape it costs more than a pixel, and on some shapes half
# resolution does not solve at all. `tune` measures it here, on this plate.
LADDER = ((1.0, 3), (1.0, 5), (1.0, 1), (0.5, 3), (0.5, 5), (0.25, 3))


def tune(ref_img, frames, mask, model, samples=5, tolerance=0.25,
         min_cc=MIN_CC):
    """Measure what each solve setting costs on THIS plate, then choose.

    The reference setting is full resolution with a small blur. Every cheaper
    setting is scored two ways: whether it correlates at all, and how far its
    corners land from the reference setting's. The cheapest setting that stays
    inside `tolerance` of the reference is the one to use, and the table comes
    back so the choice can be argued with.
    """
    keys = sorted(frames)
    if len(keys) > samples:
        keys = [keys[int(round(i * (len(keys) - 1) / (samples - 1)))]
                for i in range(samples)]
    ys, xs = np.nonzero(np.asarray(mask) > 0)
    corners = np.array([[xs.min(), ys.min()], [xs.max(), ys.min()],
                        [xs.max(), ys.max()], [xs.min(), ys.max()]], float)

    table = {}
    for scale, gauss in LADDER:
        init = np.eye(3)
        ccs, quads = [], {}
        for i in keys:
            r = ecc_solve(ref_img, frames[i], init=init, model=model,
                          mask=mask, scale=scale, gauss=gauss)
            if not r["ok"]:
                continue
            init = r["warp"]
            ccs.append(r["cc"])
            quads[i] = G.apply_h(r["warp"], corners)
        table[(scale, gauss)] = {
            "scale": scale, "gauss": gauss, "solved": len(ccs),
            "of": len(keys),
            "cc_median": float(np.median(ccs)) if ccs else None,
            "quads": quads}

    ref_key = LADDER[0]
    ref_quads = table[ref_key]["quads"]
    for key, row in table.items():
        common = [i for i in row["quads"] if i in ref_quads]
        if key == ref_key or not common:
            row["cost_px_vs_full"] = 0.0 if key == ref_key else None
            continue
        d = [float(np.linalg.norm(row["quads"][i] - ref_quads[i], axis=1).max())
             for i in common]
        row["cost_px_vs_full"] = float(np.median(d))

    usable = [k for k, r in table.items()
              if r["cc_median"] is not None and r["cc_median"] >= min_cc
              and r["solved"] == r["of"]
              and (r["cost_px_vs_full"] is not None)
              and r["cost_px_vs_full"] <= tolerance]
    # Cheapest is smallest scale, then largest blur (cheapest to compute).
    chosen = min(usable, key=lambda k: (k[0], -k[1])) if usable else ref_key
    out = {"chosen_scale": chosen[0], "chosen_gauss": chosen[1],
           "tolerance_px": tolerance, "sample_frames": keys,
           "table": [{k2: v2 for k2, v2 in r.items() if k2 != "quads"}
                     for r in table.values()]}
    ref_row = table[ref_key]
    if ref_row["cc_median"] is None or ref_row["cc_median"] < min_cc:
        out["verdict"] = "UNSOLVABLE"
        out["reason"] = (
            f"no setting reached the correlation floor: the best was "
            f"{max((r['cc_median'] or 0) for r in table.values()):.3f} against a "
            f"floor of {min_cc}. This region does not carry a {model} solve. "
            "Give it more to hold on to, or drop to a simpler model.")
    elif not usable:
        out["verdict"] = "FULL RESOLUTION"
        out["reason"] = ("no cheaper setting stayed inside the tolerance, so "
                         "the full resolution solve is what this plate needs")
    else:
        out["verdict"] = "TUNED"
    if out.get("verdict") == "TUNED":
        row = table[chosen]
        out["reason"] = (f"scale {chosen[0]} with a {chosen[1]} pixel blur lands "
                         f"{row['cost_px_vs_full']:.3f} px from the full "
                         f"resolution solve on this plate, inside the "
                         f"{tolerance} px tolerance")
    return out


def track(clip, region=None, mask=None, ref=0, model="auto", scale=None,
          gauss=None, start=0, count=None, certify_every=8, progress=None,
          min_cc=MIN_CC, tune_tolerance=0.25):
    """Solve the whole clip against ONE reference frame.

    Reference to frame, never frame to frame: a chain of inter frame solves
    accumulates its own error and there is nothing in the result that says how
    much. The previous frame's answer is used only to INITIALISE, which is what
    keeps ECC out of a local maximum on a fast move.

    Every `certify_every` frames the solve is repeated by the independent
    feature route and the two are compared at the region's corners. That
    comparison, not a threshold on the correlation coefficient, is what says
    whether the track is good.
    """
    info = P.clip_info(clip)
    frames = list(P.read_frames(clip, start=start, count=count))
    if not frames:
        raise RuntimeError(f"no frames decoded from {clip}")
    index = {i: im for i, im in frames}
    if ref not in index:
        raise ValueError(f"reference frame {ref} is not in the decoded range")
    ref_img = index[ref]

    if mask is not None:
        m = (np.asarray(mask) > 0).astype(np.uint8)
    elif region is not None:
        x, y, w, h = [int(v) for v in region]
        m = np.zeros((ref_img.height, ref_img.width), np.uint8)
        m[y:y + h, x:x + w] = 1
    else:
        m = np.ones((ref_img.height, ref_img.width), np.uint8)

    ys, xs = np.nonzero(m)
    if len(xs) < 64:
        raise ValueError("the region is too small to track")
    corners = np.array([[xs.min(), ys.min()], [xs.max(), ys.min()],
                        [xs.max(), ys.max()], [xs.min(), ys.max()]],
                       dtype=np.float64)

    texture = texture_coverage(ref_img, m)

    chosen = model
    select = None
    if model == "auto":
        # Choose on the widest baseline available: the reference against the
        # frame furthest from it, where the models differ most.
        far = max(index, key=lambda i: abs(i - ref))
        s, d = feature_pairs(ref_img, index[far], mask=m)
        select = model_select(s, d)
        select["texture"] = texture
        chosen = select.get("chosen")
        if not chosen:
            # A motion model is a fact about THIS plate, not a constant that may
            # default. Guessing one here is how a track ends up describing the
            # room behind the screen instead of the screen. Say what is missing
            # and stop.
            raise ValueError(
                "the motion model could not be measured on this region: "
                f"{select.get('reason')}\n"
                f"Texture: {texture.get('reason') or 'the region has texture'}\n"
                "A model is a fact about this plate, not a default. Either give "
                "one with --model (translation, euclidean, affine or "
                "homography), or track a region with more to lock onto. Note "
                "that affine cannot represent a panel being LIFTED toward the "
                "lens: the region grows while its centre barely moves, and only "
                "a homography carries that.")

    tuning = None
    if scale is None or gauss is None:
        tuning = tune(ref_img, index, m, chosen, tolerance=tune_tolerance,
                      min_cc=min_cc)
        scale = tuning["chosen_scale"] if scale is None else scale
        gauss = tuning["chosen_gauss"] if gauss is None else gauss

    order = sorted(index)
    warps, records = {}, []
    init = np.eye(3)
    for i in order:
        r = ecc_solve(ref_img, index[i], init=init, model=chosen, mask=m,
                      scale=scale, gauss=gauss)
        plaus = (warp_plausible(r["warp"], corners,
                                (ref_img.width, ref_img.height))
                 if r["ok"] else {"ok": False, "reason": r["reason"]})
        if not r["ok"] or r["cc"] < min_cc or not plaus["ok"]:
            # Fall back to the feature route rather than dropping the frame, but
            # only if the feature solve is itself a measurement: a homography
            # from six points has no residual and therefore no evidence.
            sp, dp = feature_pairs(ref_img, index[i], mask=m)
            H, inl = fit_model(sp, dp, chosen)
            n_inl = int(inl.sum()) if inl is not None else (0 if H is None
                                                            else len(sp))
            need = min_support(chosen)
            reason = (r["reason"] if not r["ok"] else
                      (f"ECC correlated only {r['cc']:.3f}, under {min_cc}"
                       if r["cc"] < min_cc else plaus["reason"]))
            if H is not None and not warp_plausible(
                    H, corners, (ref_img.width, ref_img.height))["ok"]:
                H = None
            if H is None or n_inl < need:
                # UNSOLVED, and deliberately NOT used to initialise the next
                # frame. A bad solve accepted here becomes the starting point
                # for every frame after it, and the whole tail of the track
                # follows it away from the plate.
                warps[i] = None
                records.append({"frame": i, "ok": False, "cc": r.get("cc"),
                                "route": "unsolved", "reason": reason,
                                "feature_inliers": n_inl,
                                "feature_inliers_needed": need})
                continue
            warps[i] = H
            init = H
            records.append({"frame": i, "ok": True, "cc": r.get("cc"),
                            "route": "features", "reason": reason,
                            "feature_inliers": n_inl})
            continue
        warps[i] = r["warp"]
        init = r["warp"]
        rec = {"frame": i, "ok": True, "cc": r["cc"], "route": "ecc"}
        # The reference frame is never certified: comparing the reference with
        # itself is two ways of computing the identity, and it always agrees.
        if certify_every and i != ref and (i - order[0]) % certify_every == 0:
            sp, dp = feature_pairs(ref_img, index[i], mask=m)
            H, inl = fit_model(sp, dp, chosen)
            if H is None:
                rec["certify"] = split_region_certificate(
                    ref_img, index[i], m, corners, chosen, scale)
                rec["certify"]["n_pairs"] = int(len(sp))
                rec["certify"]["features_failed"] = (
                    f"only {len(sp)} correspondences inside the region; a flat "
                    "backing has nothing for a feature detector to find")
            else:
                n_inl = int(inl.sum()) if inl is not None else int(len(sp))
                need = min_support(chosen)
                if n_inl < need:
                    # Not enough redundancy for the feature solve to BE a
                    # measurement. Certifying against it would fail a correct
                    # track, and a check that fails a correct film is a broken
                    # check, not a broken film.
                    rec["certify"] = split_region_certificate(
                        ref_img, index[i], m, corners, chosen, scale)
                    rec["certify"]["n_pairs"] = int(len(sp))
                    rec["certify"]["features_failed"] = (
                        f"{n_inl} inliers for a {chosen} solve, which needs "
                        f"{need} before its own answer means anything")
                else:
                    rec["certify"] = agreement(r["warp"], H, corners)
                    rec["certify"]["route"] = ("dense photometric vs sparse "
                                               "geometric")
                    rec["certify"]["n_pairs"] = int(len(sp))
                    rec["certify"]["n_inliers"] = n_inl
        records.append(rec)
        if progress:
            progress(i, len(order))

    certified = [r["certify"] for r in records
                 if r.get("certify", {}).get("verdict") == "MEASURED"]
    strong = [c for c in certified if "WEAKER" not in c.get("route", "")]
    weak = [c for c in certified if "WEAKER" in c.get("route", "")]
    failed = [r["certify"].get("reason") for r in records
              if r.get("certify", {}).get("verdict") in ("UNPROVEN", "DEGENERATE")]

    def _summary(group, label, weakness):
        if not group:
            return None
        w = [c["worst_px"] for c in group]
        return {"verdict": "MEASURED", "route": label, "weak": weakness,
                "worst_px": float(max(w)), "median_px": float(np.median(w)),
                "checked_frames": len(w)}

    strong_s = _summary(strong, "dense photometric vs sparse geometric", False)
    weak_s = _summary(weak, "split region ECC", True)
    # Never average a strong route with a weak one. If the strong route ran on
    # any frame, that is the certificate; the weak one is reported beside it.
    cert = strong_s or weak_s
    if cert is None:
        cert = {"verdict": "UNPROVEN", "checked_frames": 0,
                "reason": ("no frame could be certified by a second route. "
                           + (failed[0] if failed else
                              "Nothing was attempted: raise --certify-every or "
                              "widen the region."))}
    else:
        cert["reason"] = ("two independent solves compared at the region "
                          "corners, on frames other than the reference")
        if strong_s and weak_s:
            cert["also_weak_route"] = weak_s
        if failed:
            cert["frames_that_could_not_be_certified"] = len(failed)
            cert["first_reason"] = failed[0]
    quads = {i: (G.apply_h(W, corners) if W is not None else None)
             for i, W in warps.items()}

    return {"clip": info["path"], "decode_path": ref_img.decode_path,
            "reference_frame": ref, "model": chosen,
            "model_selection": select, "scale": scale, "gauss": gauss,
            "tuning": tuning,
            "frames": order, "warps": warps, "corners": corners,
            "texture": texture, "min_cc": min_cc,
            "unsolved_frames": [x["frame"] for x in records
                                if x.get("route") == "unsolved"],
            "quads": quads, "records": records,
            "certificate": cert}


# ---------------------------------------------------------------- smoothing


def smooth(values, true_time=None, method="savgol", window=11, order=2):
    """Smooth a per frame quantity, or refuse to.

    A GLOBAL polynomial over the whole clip is refused outright. It has a fixed,
    tiny number of degrees of freedom for an arbitrary length of film, it cannot
    represent a settle, and what it does instead is delete the settle and
    substitute a monotonic slide. That is invention, not filtering.

    A local window is allowed, and only against TRUE TIME. Where the plate is a
    conformed one, frame index is not time, and a filter run on frame index
    smooths across a lurch that is really there.

    The residual comes back measured against the RAW input, never against the
    smoothed copy.
    """
    v = np.asarray(values, dtype=np.float64)
    if v.ndim == 1:
        v = v[:, None]
    n = len(v)

    if method in ("poly", "polynomial", "global"):
        raise ValueError(
            "a global polynomial is refused here. Fitted across a whole clip it "
            "has a handful of degrees of freedom for the entire film, cannot "
            "represent a settle, and replaces it with a monotonic slide: 6.91 px "
            "worst error on the job that taught this. Use method='savgol' with a "
            "local window, and only against true time.")

    if true_time is None:
        raise ValueError(
            "smoothing needs a true time vector. Run `cadence` first: if the "
            "plate is a conformed one the frame index is not time, and a filter "
            "run on the index smooths across a lurch that is real motion. If "
            "cadence says NATIVE, pass true_time=range(n) explicitly to say so.")

    t = np.asarray(true_time, dtype=np.float64)[:n]
    if len(t) != n:
        raise ValueError("true_time and values must be the same length")

    win = int(window)
    if win % 2 == 0:
        win += 1
    win = min(win, n if n % 2 else n - 1)
    if win < 5:
        return {"values": v.squeeze(), "applied": False,
                "reason": f"only {n} frames; nothing to smooth"}

    # Resample onto an even grid in TRUE time, filter there, sample back.
    grid = np.linspace(t.min(), t.max(), n)
    from scipy.signal import savgol_filter
    out = np.empty_like(v)
    for c in range(v.shape[1]):
        even = np.interp(grid, t, v[:, c])
        filt = savgol_filter(even, win, int(order))
        out[:, c] = np.interp(t, grid, filt)

    resid = np.linalg.norm((out - v).reshape(n, -1), axis=1)
    return {"values": out.squeeze(), "applied": True, "window": win,
            "order": int(order), "domain": "true time",
            "residual_vs_raw_px": {"max": float(resid.max()),
                                   "mean": float(resid.mean()),
                                   "p95": float(np.percentile(resid, 95))},
            "note": "the residual above is against the RAW measurements, not "
                    "against this smoothed copy"}
