#!/usr/bin/env python3
"""Mattes: pull the key, and say out loud what the key cannot know.

The thing to hold on to about constant colour matting is that it is not a hard
problem, it is an UNDERSPECIFIED one. Smith and Blinn proved it in 1996: the
matting equation gives three equations, one per primary, in four unknowns, the
three foreground primaries and alpha. There is an infinity of solutions and no
algorithm can choose between them without an extra assumption.

Every keyer in the world therefore ships an assumption, and the useful question
is not "is the key good" but "where does this plate break the assumption I am
keying it with". Vlahos's assumption is that a foreground's blue is bounded by
its green (for a blue backing); the green screen form of it is that a
foreground's green is bounded by its red and blue. Where the plate breaks it,
the key eats the foreground and no amount of tuning fixes it, because the
information is genuinely not there.

So every key in this module reports its own violation map. That number is the
honest limit of the matte, and it is the number to hand a client when a green
jacket will not hold.

Two other things measured on real jobs and built in here:

**Two keys unioned beat either alone on a green stage.** A ratio key keeps a
whole silhouette including tyres sitting in their own shadow but drags a ragged
smear where the shadow falls on the floor; a residual against a FITTED backing
colour model gives a clean body but eats anything picking up green bounce.
Plain chromaticity fails the same way as the second. Where the two disagree is
reported, because that region is where somebody has to look.

**A keyed matte hides track error from every check.** The alpha comes from the
plate's own green, so a composite is clipped to the green region no matter where
the track thinks the quad is. "No plate green survives" and "nothing outside
moved" both read zero on a badly tracked composite. Nothing in here is evidence
about a track. `comp.py verify` is, and it is anchored in the plate.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pix as P  # noqa: E402

UNDERDETERMINED = (
    "Constant colour matting is three equations in four unknowns (Smith and "
    "Blinn 1996): there is an infinity of solutions and the one this key "
    "returns is the one its assumption picked. Read the violation map."
)

SCREENS = {"green": 1, "blue": 2, "red": 0}
DESPILL_FORMS = ("average", "double_second", "double_third", "limit_max",
                 "limit_min")


def _channels(screen):
    """(key channel, the two others) for a named backing colour."""
    k = SCREENS[screen]
    return k, [c for c in range(3) if c != k]


# ---------------------------------------------------------------- diagnostics


def vlahos_violation(rgb, screen="green", a2=1.0):
    """Where the plate breaks the assumption the key is about to make.

    Vlahos's working assumption, transposed to a green backing, is that a real
    foreground's green does not exceed its red and blue by more than a factor;
    the range his own technique allows is roughly 0.5 to 1.5. Green foliage,
    yellow signage, a lime jacket and anything picking up bounce off the floor
    all break it, and a colour difference key will eat every one of them.

    Returned as a map and a percentage of the non backing area, so the limit can
    be handed over rather than discovered in a review.
    """
    c = np.asarray(rgb, dtype=np.float32)
    k, others = _channels(screen)
    excess = c[..., k] - a2 * np.maximum(c[..., others[0]], c[..., others[1]])
    return excess


def violation_report(rgb, alpha, screen="green", a2=1.0, thresh=0.02):
    """The violation map restricted to what the key called foreground."""
    excess = vlahos_violation(rgb, screen=screen, a2=a2)
    fg = np.asarray(alpha, dtype=np.float32) > 0.5
    bad = fg & (excess > thresh)
    n_fg = int(fg.sum())
    return {"screen": screen, "a2": a2, "threshold": thresh,
            "foreground_px": n_fg,
            "violating_px": int(bad.sum()),
            "violating_fraction": float(bad.sum() / max(n_fg, 1)),
            "worst_excess": float(excess[fg].max()) if n_fg else 0.0,
            "map": bad,
            "meaning": ("these foreground pixels carry more backing colour than "
                        "the key's assumption allows. The key cannot separate "
                        "them from the backing, and tuning will not fix it: the "
                        "information is not in the frame. Roto them, relight "
                        "them, or accept the loss."),
            "note": UNDERDETERMINED}


# ---------------------------------------------------------------- key one


def _local_backing_level(d, seed_bg, k=81, floor=8e-3):
    """The backing's own level, estimated per pixel from the backing pixels.

    This is screen correction: instead of one number for the whole stage, the
    key gets the level the backing actually has AT that pixel, so a hot spot on
    one side of the cyc stops eating the foreground on the other. A clean plate
    does this exactly; where there is no clean plate, a wide local mean over the
    pixels that are unambiguously backing does it well enough.
    """
    import cv2

    valid = seed_bg.astype(np.float32)
    filled = np.where(seed_bg, d, 0.0).astype(np.float32)
    num = cv2.blur(filled, (k, k))
    den = np.maximum(cv2.blur(valid, (k, k)), 1e-6)
    lvl = cv2.blur(num / den, (k, k))
    return np.maximum(lvl, floor)


def key_difference(img, screen="green", softness=0.72, seed_quantile=0.6,
                   clean_plate=None, close=5, roi=None):
    """The colour difference key, with the backing level measured per pixel.

    d = key channel minus the larger of the other two. On the backing d is large
    and positive; on almost any real foreground it is negative. Alpha is how far
    d has fallen from the backing's own local level:

        alpha = clip((level - d) / (level * softness), 0, 1)

    which is Vlahos's first form with the tuning constant replaced by a measured
    field rather than a knob. Where a clean plate exists it is used directly and
    the level is exact.
    """
    import cv2

    lin = img.as_linear()
    c = lin.rgb
    k, others = _channels(screen)
    d = (c[..., k] - np.maximum(c[..., others[0]], c[..., others[1]])).astype(np.float32)

    if clean_plate is not None:
        cp = clean_plate.as_linear().rgb
        level = (cp[..., k] - np.maximum(cp[..., others[0]],
                                         cp[..., others[1]])).astype(np.float32)
        level = np.maximum(level, 8e-3)
        level_source = "clean plate, exact"
    else:
        # The seed is where the backing certainly IS, and it must be looked for
        # only inside the garbage matte. Seeded from the whole frame, a green
        # exit sign forty pixels wide sets the level for a wall it is nowhere
        # near.
        pool = d if roi is None else d[roi]
        thr = float(np.quantile(pool, seed_quantile))
        seed = d > max(thr, 0.02)
        if roi is not None:
            seed &= roi
        if seed.sum() < max(pool.size * 0.02, 64):
            seed = d > np.quantile(pool, 0.85)
            if roi is not None:
                seed &= roi
        seed = cv2.morphologyEx(seed.astype(np.uint8), cv2.MORPH_OPEN,
                                np.ones((close, close), np.uint8)) > 0
        level = _local_backing_level(d, seed)
        level_source = "local mean over the backing pixels (screen correction)"

    alpha = np.clip((level - d) / np.maximum(level * softness, 1e-6), 0.0, 1.0)
    return {"alpha": alpha.astype(np.float32), "difference": d,
            "level": level, "level_source": level_source,
            "method": "colour difference (Vlahos first form, level measured)",
            "screen": screen, "softness": softness}


def key_backing_model(img, screen="green", order=2, rel_thresh=0.14,
                      gain_max=1.25, seed_ratio=0.44, min_value=0.06, roi=None):
    """A residual against a fitted model of the backing colour.

    The backing is not one colour: it is a smoothly varying field, lit unevenly
    and falling off toward the edges. Fit that field as a low order polynomial
    in x and y on the pixels that are unambiguously backing, then ask of every
    pixel: is it this field scaled by some brightness, or is it something else?

        k     = (S . F) / (F . F)          the best scalar per pixel
        resid = |S - k F| / (|F| * k)      how far off that model it is

    A pixel that is only the backing in shadow has a small k and a tiny
    residual, which is how a body's own shadow on the floor stays background
    while the body does not. What this key eats is anything picking up bounce
    off the backing, because bounce really is the backing's colour.
    """
    from scipy import ndimage

    lin = img.as_linear()
    S = lin.rgb.astype(np.float64)
    h, w = S.shape[:2]
    k, _ = _channels(screen)

    tot = S.sum(axis=2) + 1e-6
    seed = (S[..., k] / tot > seed_ratio) & (S.mean(axis=2) > min_value)
    if roi is not None:
        seed &= roi
    if seed.sum() < 500:
        return {"alpha": None, "verdict": "UNPROVEN",
                "reason": (f"only {int(seed.sum())} pixels look unambiguously "
                           f"like a {screen} backing; there may not be one")}

    yy, xx = np.mgrid[0:h, 0:w]
    xn, yn = xx / w, yy / h
    terms = [np.ones_like(xn), xn, yn]
    if order >= 2:
        terms += [xn * yn, xn ** 2, yn ** 2]
    if order >= 3:
        terms += [xn ** 2 * yn, xn * yn ** 2, xn ** 3, yn ** 3]
    A = np.stack(terms, axis=-1)
    F = np.zeros_like(S)
    for c in range(3):
        coef, *_ = np.linalg.lstsq(A[seed], S[seed][:, c], rcond=None)
        F[..., c] = A @ coef

    num = (S * F).sum(axis=2)
    den = (F * F).sum(axis=2) + 1e-9
    gain = num / den
    res = np.linalg.norm(S - gain[..., None] * F, axis=2)
    scale = np.linalg.norm(F, axis=2) * np.clip(gain, 0.12, None)
    rel = res / (scale + 1e-9)

    bg = (rel < rel_thresh) & (gain < gain_max)
    if roi is not None:
        bg &= roi
    bg = ndimage.binary_closing(bg, np.ones((3, 3)), border_value=1)
    if roi is not None:
        bg &= roi
    # border_value=1 above is not decoration: without it, closing ERODES the
    # image border, and a flood fill started from the border then finds nothing.
    lab, n = ndimage.label(bg)

    # Which components are the backing? Two different films, two different
    # answers, and getting this wrong silently inverts the matte.
    #
    #   a green STAGE fills the frame edges, so the backing is whatever touches
    #   the border, and anything enclosed is the subject;
    #   a green PANEL sits INSIDE the shot, touches no border, and the rule
    #   above would find nothing at all.
    #
    # So look at where the seed actually is, and say which rule was used.
    if roi is None:
        border = np.zeros(seed.shape, bool)
        border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    else:
        # With a garbage matte the relevant "border" is the matte's own edge,
        # not the picture's.
        border = roi & ~ndimage.binary_erosion(roi, np.ones((3, 3)))
    seed_on_border = bool((seed & border).any())
    if seed_on_border:
        touching = set(np.unique(lab[border]))
        touching.discard(0)
        bg = np.isin(lab, list(touching)) if touching else bg
        rule = ("the backing reaches the frame edge, so it is a STAGE: the "
                "background is what touches the border and anything enclosed "
                "is the subject")
    elif n:
        sizes = ndimage.sum(bg, lab, range(1, n + 1))
        keep = np.where(sizes > 0.15 * sizes.max())[0] + 1
        bg = np.isin(lab, keep)
        rule = ("the backing touches no frame edge, so it is a PANEL inside the "
                "shot: the background is the backing coloured region itself, "
                "and the border rule would have found nothing")
    else:
        rule = "no background component survived"

    alpha = (~bg).astype(np.float32)
    return {"alpha": alpha, "relative_residual": rel.astype(np.float32),
            "gain": gain.astype(np.float32), "model": F.astype(np.float32),
            "seed_px": int(seed.sum()),
            "seed_residual_p50": float(np.percentile(rel[seed], 50)),
            "seed_residual_p99": float(np.percentile(rel[seed], 99)),
            "method": f"residual against a fitted order {order} backing model",
            "backing_rule": rule, "backing_is_a_panel": not seed_on_border,
            "screen": screen, "verdict": "MEASURED"}


def key_union(img, screen="green", **kw):
    """Both keys, unioned, with the disagreement reported rather than hidden.

    Measured on a green stage: the difference key holds a whole silhouette
    including tyres sitting in their own shadow, and drags a ragged smear where
    the shadow falls on the floor. The model key gives a clean body and eats
    anything picking up bounce. Unioned, each covers the other's hole. The area
    where they disagree is exactly the area somebody has to look at.
    """
    a = key_difference(img, screen=screen,
                       **{k: v for k, v in kw.items()
                          if k in ("softness", "seed_quantile", "clean_plate",
                                   "roi")})
    b = key_backing_model(img, screen=screen,
                          **{k: v for k, v in kw.items()
                             if k in ("order", "rel_thresh", "gain_max", "roi")})
    if b.get("alpha") is None:
        out = dict(a)
        out["union"] = {"verdict": "UNPROVEN", "reason": b["reason"],
                        "fell_back_to": "colour difference alone"}
        return out

    fa = a["alpha"]
    fb = b["alpha"]
    union = np.maximum(fa, fb)
    inter = np.minimum(fa, fb)
    disagree = (fa > 0.5) ^ (fb > 0.5)
    return {"alpha": union.astype(np.float32),
            "alpha_difference_key": fa, "alpha_model_key": fb,
            "alpha_intersection": inter.astype(np.float32),
            "method": "union of the colour difference and backing model keys",
            "screen": screen,
            "union": {"verdict": "MEASURED",
                      "difference_key_px": int((fa > 0.5).sum()),
                      "model_key_px": int((fb > 0.5).sum()),
                      "union_px": int((union > 0.5).sum()),
                      "disagreement_px": int(disagree.sum()),
                      "disagreement_fraction": float(
                          disagree.sum() / max((union > 0.5).sum(), 1)),
                      "map": disagree,
                      "meaning": "where the two independent keys disagree. Look "
                                 "here; a threshold will not settle it."},
            "difference_detail": {k: v for k, v in a.items()
                                  if k not in ("alpha", "difference", "level")},
            "model_detail": {k: v for k, v in b.items()
                             if k not in ("alpha", "relative_residual", "gain",
                                          "model")}}


# ---------------------------------------------------------------- exact cases


def triangulate(fg1, fg2, bk1, bk2):
    """The exact solution, when the object was shot against two backings.

    Smith and Blinn's Theorem 3. If the same uncomposited foreground is known
    against two backings that differ anywhere, the matting problem stops being
    underdetermined and has one answer:

        alpha = 1 - (sum(Cf1) - sum(Cf2)) / (sum(Ck1) - sum(Ck2))
        Co    = Cf1 - (1 - alpha) * Ck1

    The backings do not have to be constant, or clean, or even the same hue;
    they only have to differ. In a studio the cheap version is one pass with the
    cyc lit and one with it dark. Where the two backings happen to be identical
    at a pixel the denominator vanishes and that pixel is UNSOLVED, which the
    result marks rather than filling in.
    """
    a1 = fg1.as_linear().rgb.astype(np.float64)
    a2 = fg2.as_linear().rgb.astype(np.float64)
    k1 = bk1.as_linear().rgb.astype(np.float64)
    k2 = bk2.as_linear().rgb.astype(np.float64)
    if not (a1.shape == a2.shape == k1.shape == k2.shape):
        raise ValueError("all four plates must be the same raster")

    den = (k1 - k2).sum(axis=2)
    solved = np.abs(den) > 1e-4
    alpha = np.where(solved,
                     1.0 - (a1 - a2).sum(axis=2) / np.where(solved, den, 1.0),
                     np.nan)
    alpha_c = np.clip(np.nan_to_num(alpha, nan=0.0), 0.0, 1.0)
    co = a1 - (1.0 - alpha_c)[..., None] * k1
    return {"alpha": alpha_c.astype(np.float32),
            "foreground": np.clip(co, 0.0, None).astype(np.float32),
            "premultiplied": True,
            "unsolved_px": int((~solved).sum()),
            "unsolved_fraction": float((~solved).sum() / solved.size),
            "unsolved_map": ~solved,
            "clipped_px": int(((alpha < -1e-3) | (alpha > 1 + 1e-3)).sum()),
            "method": "triangulation (Smith and Blinn 1996, Theorem 3)",
            "note": "exact where the two backings differ; a pixel where they "
                    "are identical carries no information and is marked "
                    "UNSOLVED rather than guessed"}


# ---------------------------------------------------------------- despill


def despill(rgb, screen="green", form="limit_max", strength=1.0,
            preserve_luma=False, alpha=None):
    """Pull the backing colour back out of the foreground, by a NAMED rule.

    Every despill is the same shape: clamp the key channel to a limit built from
    the other two. What differs is the limit, and the choice is not cosmetic.

        average        limit = (a + b) / 2
        double_second  limit = (2a + b) / 3       weights the first companion
        double_third   limit = (a + 2b) / 3
        limit_max      limit = max(a, b)          the gentlest
        limit_min      limit = min(a, b)          the harshest, and the darkest

    Two costs, both reported rather than left to be discovered. Despill removes
    light, so the picture gets darker, worst on a limit_min; `preserve_luma` puts
    the removed energy back neutrally instead of throwing it away. And despill
    shifts hue on colours that legitimately sit near the backing, which on a
    green stage means the YELLOWS: a yellow is red plus green, and clamping its
    green turns it orange.
    """
    c = np.asarray(rgb, dtype=np.float32).copy()
    k, others = _channels(screen)
    a, b = c[..., others[0]], c[..., others[1]]
    limits = {"average": (a + b) / 2.0,
              "double_second": (2.0 * a + b) / 3.0,
              "double_third": (a + 2.0 * b) / 3.0,
              "limit_max": np.maximum(a, b),
              "limit_min": np.minimum(a, b)}
    if form not in limits:
        raise ValueError(f"unknown despill form {form!r}; one of {DESPILL_FORMS}")
    limit = limits[form]

    g = c[..., k]
    removed = np.maximum(g - limit, 0.0) * float(strength)
    c[..., k] = g - removed

    before = P.linear_luma(np.stack([np.asarray(rgb, dtype=np.float32)[..., i]
                                     for i in range(3)], axis=-1))
    if preserve_luma:
        # Put the removed light back into the other two channels, split so the
        # pixel's luminance returns to what it was. Never back into the key
        # channel, which would undo the despill.
        wk = float(P.LUMA_709[k])
        wo = float(P.LUMA_709[others[0]] + P.LUMA_709[others[1]])
        add = removed * wk / max(wo, 1e-6)
        c[..., others[0]] += add
        c[..., others[1]] += add
    after = P.linear_luma(c)

    hit = removed > 1e-4
    # Every number below is reported twice: over the whole frame, and over the
    # FOREGROUND only. Over the whole frame the answer is always "most of it",
    # because the backing is nothing but backing colour and despilling it is
    # supposed to flatten it. The number that means anything is the one inside
    # the matte, which is why an alpha should be passed in.
    fg = (np.asarray(alpha, dtype=np.float32) > 0.5) if alpha is not None \
        else np.ones(hit.shape, dtype=bool)
    fhit = hit & fg
    # The yellow problem, MEASURED. Not a heuristic about channel ratios: the
    # actual hue rotation in Lab, in degrees, between before and after. A yellow
    # that goes orange has rotated, and "delta E 6" does not say which way.
    lab_before = P.linear_rgb_to_lab(np.asarray(rgb, dtype=np.float32))
    lab_after = P.linear_rgb_to_lab(c)
    hue_deg, de76 = P.lab_hue_shift(lab_before, lab_after)
    rotated = fhit & (np.abs(hue_deg) > 5.0)
    dl = (after - before)
    return {"rgb": c,
            "form": form, "strength": strength, "preserve_luma": preserve_luma,
            "affected_px": int(hit.sum()),
            "affected_fraction": float(hit.sum() / hit.size),
            "mean_removed": float(removed[hit].mean()) if hit.any() else 0.0,
            "luma_change_mean": float(dl[hit].mean()) if hit.any() else 0.0,
            "luma_change_worst": float(dl[hit].min()) if hit.any() else 0.0,
            "foreground_measured": alpha is not None,
            "foreground_affected_px": int(fhit.sum()),
            "foreground_affected_fraction": float(fhit.sum() / max(fg.sum(), 1)),
            "foreground_luma_change_mean": float(dl[fhit].mean()) if fhit.any() else 0.0,
            "foreground_luma_change_worst": float(dl[fhit].min()) if fhit.any() else 0.0,
            "hue_rotated_px": int(rotated.sum()),
            "hue_rotated_fraction": float(rotated.sum() / max(fg.sum(), 1)),
            "hue_rotation_worst_deg": (float(hue_deg[fhit][
                np.argmax(np.abs(hue_deg[fhit]))]) if fhit.any() else 0.0),
            "hue_rotation_mean_deg": (float(hue_deg[rotated].mean())
                                      if rotated.any() else 0.0),
            "delta_e76_mean": float(de76[fhit].mean()) if fhit.any() else 0.0,
            "delta_e76_worst": float(de76[fhit].max()) if fhit.any() else 0.0,
            "hue_risk_note": ("foreground pixels whose hue rotated by more than "
                              "5 degrees in Lab. On a green stage these are the "
                              "yellows, and the direction of the rotation is the "
                              "artefact: they go orange. Pass an alpha or this "
                              "counts the backing too and means nothing."),
            "hue_map_deg": hue_deg,
            "removed": removed.astype(np.float32)}


# ---------------------------------------------------------------- edge


def choke(alpha, pixels=0.0, softness=0.0, gamma=1.0):
    """Move the matte edge in or out, and soften it, in that order.

    A choke is a real decision with a real cost: taking the edge in by a pixel
    removes a pixel of the subject everywhere, including where the edge was
    already correct. The amount is reported in pixels so it can be argued about.
    """
    import cv2

    a = np.asarray(alpha, dtype=np.float32)
    if pixels:
        r = int(abs(round(pixels))) * 2 + 1
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r))
        a = cv2.erode(a, kern) if pixels > 0 else cv2.dilate(a, kern)
    if softness:
        a = cv2.GaussianBlur(a, (0, 0), float(softness))
    if gamma and gamma != 1.0:
        a = np.power(np.clip(a, 0, 1), float(gamma))
    return np.clip(a, 0.0, 1.0).astype(np.float32)


def light_wrap(bg_linear, alpha, width=12.0, gain=0.35):
    """Let the background's light fall onto the edge of the foreground.

    The background is blurred hard, so what lands on the edge is LIGHT and not
    detail; a sharp wrap paints a ghost of the background onto the subject and
    reads as a transparent edge. It is confined to a band just inside the matte
    by the difference between the matte and a blurred copy of it, and it is
    ADDED, because light adds.
    """
    import cv2

    a = np.asarray(alpha, dtype=np.float32)
    bgb = cv2.GaussianBlur(np.asarray(bg_linear, dtype=np.float32), (0, 0),
                           float(width))
    inner = cv2.GaussianBlur(a, (0, 0), float(width))
    band = np.clip(a - inner, 0.0, 1.0) * a
    return (bgb * band[..., None] * float(gain)).astype(np.float32), band
