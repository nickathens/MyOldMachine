#!/usr/bin/env python3
"""Compositing: track it, key it, put it in, and prove it from the PLATE.

The department where the most convincing wrong answers live. A composite can be
built on a track that is six pixels out and every check will pass, because the
matte came from the plate's own green and clipped the result to the right region
no matter where the track thought the quad was. So the shape of this tool is:
measure, then prove the measurement from something the build did not use.

The order is not decoration:

  1  cadence   is this plate a conformed one? Do this BEFORE tracking.
  2  track     solve it twice, by methods that share no assumption.
  3  quad      an ordered ring, per edge MEASURED or UNMEASURED.
  4  aspect    the anisotropy R, and whether the true aspect is knowable at all.
  5  key       pull the matte, and report where the plate breaks the assumption.
  6  warp      build the composite, in linear light, premultiplied, padded.
  7  verify    every check anchored in the PLATE, never in the model the build
               used.

Commands:
  cadence   the plate's own beat, and the true time vector to smooth against
  track     reference to frame solve, model chosen by cross validation
  quad      ordered ring from a matte, with per edge verdicts and the hull cost
  aspect    R from the outline, and the rectangle aspect with its verdict
  key       colour difference, backing model, or both, plus the violation map
  despill   a named despill form, with the luma cost and the hue rotation
  triangulate  the exact matte, when the object was shot against two backings
  insert    the horizon ratio: the same real size in two frames of a moving shot
  grain     measure a plate's own grain, or lay it back onto a composite
  warp      composite an artwork onto a tracked region and encode it
  verify    ring, content, channels, notch, rank
  holdout   leave one out on the rigid shape: the gate for any occluded edge

Usage:
  python comp.py cadence PLATE.mov --json
  python comp.py track PLATE.mov --region 820,410,300,170 --out track.json
  python comp.py quad --frame FRAME.png --mask MATTE.png
  python comp.py aspect --quad 100,80,700,60,720,500,90,520 --raster 1920x1080
  python comp.py key PLATE.png --screen green --method union --out matte.png
  python comp.py warp PLATE.mov --track track.json --art UI.png --out comp.mov
  python comp.py verify ring --plate PLATE.mov --comp COMP.mov --track track.json
  (add --json for structured output; every command has it)

Needs numpy, OpenCV and SciPy in this skill's OWN environment:
  python3 -m venv ~/.venvs/post
  ~/.venvs/post/bin/pip install pillow numpy opencv-python-headless scipy
Run this file with ~/.venvs/post/bin/python.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover - the message is the point
    C.fail("comp.py needs numpy, OpenCV and SciPy in this skill's own "
           "environment. Create it once:\n"
           "  python3 -m venv ~/.venvs/post\n"
           "  ~/.venvs/post/bin/pip install pillow numpy "
           "opencv-python-headless scipy\n"
           "then run this file with ~/.venvs/post/bin/python.")

import _geom as G  # noqa: E402
import _matte as M  # noqa: E402
import _pix as P  # noqa: E402
import _track as T  # noqa: E402


# ---------------------------------------------------------------- arg helpers


def parse_region(text):
    parts = [float(v) for v in str(text).replace(" ", "").split(",")]
    if len(parts) != 4:
        raise ValueError("--region wants x,y,w,h")
    return parts


def parse_quad(text):
    parts = [float(v) for v in str(text).replace(" ", "").split(",")]
    if len(parts) != 8:
        raise ValueError("--quad wants eight numbers: x1,y1,x2,y2,x3,y3,x4,y4")
    return np.array(parts, dtype=np.float64).reshape(4, 2)


def parse_raster(text):
    w, h = str(text).lower().split("x")
    return int(w), int(h)


def load_mask(path, threshold=0.5):
    img = P.read_image(path)
    a = img.alpha if img.alpha is not None else (img.rgb @ P.LUMA_709)
    return (np.asarray(a, dtype=np.float32) > threshold).astype(np.uint8)


def _clean(obj):
    """Drop the big arrays so a result can be printed or written as JSON."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()
                if not isinstance(v, np.ndarray) or v.size <= 64}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    return obj


def emit(obj, as_json, printer=None):
    return C.emit(_clean(obj), as_json, printer)


# ---------------------------------------------------------------- cadence


def cmd_cadence(args):
    region = parse_region(args.region) if args.region else None
    r = T.cadence(args.clip, scale=args.scale, limit=args.limit, region=region)

    def show(d):
        print(f"cadence: {os.path.basename(d['clip'])}")
        print(f"  declared rate     {d.get('declared_rate')}")
        print(f"  measured on       {d.get('measured_on')} at scale "
              f"{d.get('measured_at_scale')}"
              + ("  (rescaled: the motion was too small to measure smaller)"
                 if d.get("rescaled_because_motion_was_small") else ""))
        if d.get("motion_px_median") is not None:
            print(f"  motion            {d['motion_px_median']:.2f} px per frame, median")
        print(f"  verdict           {d['verdict']}")
        if d.get("period"):
            print(f"  period            every {d['period']} frames, phase {d['phase']}")
            print(f"  lurch             {d['ratio']:.2f}x the other steps "
                  f"(separation {d['separation']:.2f} sigma)")
        if d.get("implied_source_rate"):
            print(f"  implied           {d['implied_source_rate']}")
        print(f"  {d['reason']}")
        if d["verdict"] == "CONFORMED":
            print("\n  Do not smooth this away. Track it, and pass the true time "
                  "vector to anything that filters.")
    r.pop("true_time_normalised", None) if not args.json else None
    return emit(r, args.json, show)


# ---------------------------------------------------------------- track


def cmd_track(args):
    mask = load_mask(args.mask) if args.mask else None
    region = parse_region(args.region) if args.region else None
    if mask is None and region is None:
        raise ValueError("track needs --region x,y,w,h or --mask FILE.png")

    cad = T.cadence(args.clip, limit=args.limit, region=region)
    r = T.track(args.clip, region=region, mask=mask, ref=args.ref,
                model=args.model, scale=args.scale, gauss=args.gauss,
                start=args.start, count=args.count,
                certify_every=args.certify_every, min_cc=args.min_cc,
                tune_tolerance=args.tune_tolerance)
    r["cadence"] = {k: v for k, v in cad.items()
                    if k != "true_time_normalised"}

    if args.out:
        payload = {
            "clip": r["clip"], "decode_path": r["decode_path"],
            "reference_frame": r["reference_frame"], "model": r["model"],
            "scale": r["scale"], "gauss": r["gauss"],
            "tuning": _clean(r.get("tuning")),
            "corners": r["corners"].tolist(),
            "frames": r["frames"],
            "warps": {str(k): (v.tolist() if v is not None else None)
                      for k, v in r["warps"].items()},
            "quads": {str(k): (v.tolist() if v is not None else None)
                      for k, v in r["quads"].items()},
            "certificate": r["certificate"],
            "mask_signature": _mask_signature(mask if mask is not None
                                              else np.ones((1, 1), np.uint8)),
            "model_selection": _clean(r["model_selection"]),
            "cadence": _clean(r["cadence"]),
            "true_time_normalised": cad.get("true_time_normalised"),
        }
        P.save_json(args.out, payload)
        r["written"] = os.path.abspath(args.out)

    def show(d):
        print(f"track: {os.path.basename(d['clip'])}")
        print(f"  reference frame   {d['reference_frame']}")
        print(f"  model             {d['model']}", end="")
        sel = d.get("model_selection")
        if sel and sel.get("verdict") == "MEASURED":
            print(f"   ({sel['reason']})")
            for m, v in sel["models"].items():
                if v:
                    print(f"      {m:12s} held out {v['heldout_median_px']:.3f} px "
                          f"+/- {v['standard_error_px']:.3f}")
            if sel.get("inlier_fraction") is not None:
                print(f"      one rigid surface? {sel['one_surface']}  "
                      f"({100 * sel['inlier_fraction']:.0f}% of correspondences "
                      f"fit a single planar motion)")
            if sel.get("warning"):
                print(f"      {sel['warning']}")
        elif sel:
            print("   NOT MEASURED, defaulted")
            print(f"      {sel.get('reason')}")
            print("      A default is not a measurement. Track a region with "
                  "texture in it and\n      the model choice becomes a number "
                  "rather than an assumption.")
        else:
            print()
        tx = d.get("texture") or {}
        if tx.get("verdict") == "BLIND SPOT":
            print(f"  texture           BLIND SPOT")
            print(f"      {tx['reason']}")
        elif tx.get("flat_fraction") is not None:
            print(f"  texture           {100 * (1 - tx['flat_fraction']):.0f}% of "
                  f"the region carries something to lock onto")
        tn = d.get("tuning")
        if tn:
            print(f"  solve settings    scale {d['scale']}, {d['gauss']} px blur, "
                  f"MEASURED on this plate")
            for row in tn["table"]:
                cc = f"{row['cc_median']:.3f}" if row["cc_median"] is not None else "  -  "
                cost = ("  -  " if row["cost_px_vs_full"] is None
                        else f"{row['cost_px_vs_full']:5.3f}")
                mark = ("<-" if (row["scale"] == d["scale"] and
                                 row["gauss"] == d["gauss"]) else "  ")
                print(f"      {mark} scale {row['scale']:<4} blur {row['gauss']}  "
                      f"solved {row['solved']}/{row['of']}  cc {cc}  "
                      f"cost vs full {cost} px")
            print(f"      {tn['reason']}")
        else:
            print(f"  solve settings    scale {d['scale']}, {d['gauss']} px blur "
                  f"(given, not measured)")
        print(f"  frames solved     {d['frames_solved']} of {d['frames_total']}")
        if d.get("frames_by_fallback"):
            print(f"  by the feature route (ECC would not converge): "
                  f"{d['frames_by_fallback']}")
        if d.get("frames_unsolved"):
            print(f"  UNSOLVED          {len(d['frames_unsolved'])} frames: "
                  f"{d['frames_unsolved'][:12]}")
            print(f"      {d['first_unsolved_reason']}")
            print("      These carry no warp. They were deliberately not used to "
                  "initialise the\n      next frame either: one bad solve "
                  "accepted becomes the starting point for\n      every frame "
                  "after it.")
        cert = d["certificate"]
        print(f"  certificate       {cert['verdict']}")
        if cert["verdict"] == "MEASURED":
            print(f"      two independent solves agree to {cert['worst_px']:.3f} px "
                  f"worst, {cert['median_px']:.3f} px median, over "
                  f"{cert['checked_frames']} frames")
            print(f"      route: {cert.get('route')}")
            if cert.get("also_weak_route"):
                w = cert["also_weak_route"]
                print(f"      a weaker route also ran on {w['checked_frames']} "
                      f"frames: {w['worst_px']:.3f} px worst")
            if cert.get("frames_that_could_not_be_certified"):
                print(f"      {cert['frames_that_could_not_be_certified']} frame(s) "
                      f"could not be certified at all:")
                print(f"      {cert['first_reason']}")
            if cert.get("weak"):
                print("      This is the WEAKER certificate, and its number "
                      "bounds the disagreement\n      between two HALF solves, "
                      "not the track. A large value here can mean the\n      "
                      "halves are weak rather than that the track is wrong. A "
                      "region with real\n      texture in it gets the strong "
                      "route: track a shape LARGER than the\n      screen, so "
                      "the solve has the bezel and the surround to hold on to.")
        else:
            print(f"      {cert['reason']}")
            print("      Track a shape LARGER than the screen. A flat backing "
                  "carries nothing to\n      match, and the bezel and the "
                  "surround do.")
        cad = d.get("cadence", {})
        if cad.get("verdict") == "CONFORMED":
            print(f"  cadence           CONFORMED, {cad['ratio']:.2f}x lurch every "
                  f"{cad['period']} frames. Smooth against true time or not at all.")
        if d.get("written"):
            print(f"  written           {d['written']}")
        print("\n  This says the track is self consistent. It says NOTHING about "
              "the composite:\n  a keyed matte hides track error from every check "
              "that is not anchored in the plate.\n  Run `comp.py verify ring` and "
              "`comp.py verify content` on the rendered file.")

    r["texture"] = _clean(r.get("texture"))
    r["tuning"] = _clean(r.get("tuning"))
    r["frames_solved"] = sum(1 for v in r["warps"].values() if v is not None)
    r["frames_total"] = len(r["frames"])
    r["frames_by_fallback"] = [x["frame"] for x in r["records"]
                               if x.get("route") == "features"]
    r["frames_unsolved"] = list(r.get("unsolved_frames") or [])
    r["first_unsolved_reason"] = next(
        (x.get("reason") for x in r["records"] if x.get("route") == "unsolved"),
        None)
    r["cc_median"] = float(np.median([x["cc"] for x in r["records"]
                                      if x.get("cc") is not None])) \
        if any(x.get("cc") is not None for x in r["records"]) else None
    for k in ("warps", "quads", "records", "corners"):
        r.pop(k, None)
    r.pop("frames", None)
    return emit(r, args.json, show)


# ---------------------------------------------------------------- quad


def cmd_quad(args):
    mask = load_mask(args.mask)
    ring = G.ring_from_mask(mask, corner_frac=args.corner_frac,
                            order=2 if args.curved else 1)
    if ring is None:
        raise RuntimeError("no quadrilateral region found in that matte")

    alpha = P.read_image(args.mask)
    a = alpha.alpha if alpha.alpha is not None else (alpha.rgb @ P.LUMA_709)

    corners = ring["corners"]
    names = ["top", "right", "bottom", "left"]
    edges = []
    for i in range(4):
        p0, p1 = corners[i], corners[(i + 1) % 4]
        pts, _, params, attempted = G.subpixel_edge_samples(
            a, p0, p1, n_lines=args.scanlines)
        length = float(np.linalg.norm(p1 - p0))
        v = G.edge_verdict(pts, length, params=params, attempted=attempted,
                           min_lines=args.min_lines, max_rms=args.max_rms,
                           min_span_frac=args.min_span,
                           expected_gap_px=0.08 * length)
        v["edge"] = names[i]
        v.pop("line", None)
        v.pop("poly2", None)
        edges.append(v)

    # A CORNER is what a composite reads off this, so give it its own verdict.
    # Corner i is where edge i-1 ends and edge i begins, so it is measured only
    # if the tail of one and the head of the other both reached it.
    corner_names = ["top left", "top right", "bottom right", "bottom left"]
    corner_rows = []
    for i in range(4):
        prev_e, next_e = edges[i - 1], edges[i]
        tail = prev_e.get("tail_coverage")
        head = next_e.get("head_coverage")
        ok = ("end" not in (prev_e.get("ends_unmeasured") or []) and
              "start" not in (next_e.get("ends_unmeasured") or []))
        corner_rows.append({
            "corner": corner_names[i], "x": float(corners[i][0]),
            "y": float(corners[i][1]),
            "verdict": "MEASURED" if ok else "UNMEASURED",
            "from_edges": [prev_e["edge"], next_e["edge"]],
            "coverage": [tail, head],
            "gap_px": [prev_e.get("tail_gap_px"), next_e.get("head_gap_px")],
            "expected_gap_px": prev_e.get("expected_gap_px")})

    out = {"mask": os.path.abspath(args.mask),
           "corners": corners.tolist(),
           "corner_verdicts": corner_rows,
           "bowed_edges": [names[i] for i in ring["bowed_edges"]],
           "hull": ring["hull"],
           "edges": edges,
           "measured_edges": sum(1 for e in edges if e["verdict"] == "MEASURED"),
           "measured_corners": sum(1 for c in corner_rows
                                   if c["verdict"] == "MEASURED")}
    if args.out:
        P.save_json(args.out, out)
        out["written"] = os.path.abspath(args.out)

    def show(d):
        print(f"quad: {os.path.basename(d['mask'])}")
        for c in d["corner_verdicts"]:
            tag = "MEASURED  " if c["verdict"] == "MEASURED" else "UNMEASURED"
            gaps = "/".join("-" if v is None else f"{v:.0f}"
                            for v in c.get("gap_px", []))
            print(f"  {c['corner']:13s} {c['x']:9.2f} {c['y']:9.2f}   {tag}"
                  f"  (read {gaps} px past the last real sample)")
        print()
        for e in d["edges"]:
            tag = "MEASURED  " if e["verdict"] == "MEASURED" else "UNMEASURED"
            print(f"  {e['edge']:7s} {tag} {e['n_samples']:4d} scanlines", end="")
            if e.get("rms") is not None:
                print(f"  rms {e['rms']:5.2f} px  spans {e['span_frac']:.2f}"
                      f"  bow {e['bow']:+6.2f} px", end="")
            print()
            if e["reason"]:
                print(f"          {e['reason']}")
        print()
        h = d["hull"]
        if h["max_px"] > 2.0 and h["deep_fraction"] > 0.05:
            print(f"  A CONVEX HULL WOULD MOVE THIS OUTLINE BY "
                  f"{h['max_px']:.1f} px, over {h['deep_points']} points "
                  f"({100 * h['deep_fraction']:.0f}% of the outline).")
            print("  This panel is NOT FLAT. Exactly one projected edge of a "
                  "panel that is concave\n  toward the camera bows INTO the "
                  "shape, and a hull replaces that edge with\n  its chord. Fill "
                  "and mask from the ordered ring, never from a hull.")
        else:
            print(f"  hull cost {h['max_px']:.1f} px over {h['deep_points']} "
                  f"points: this outline is convex to within its own jaggedness, "
                  f"so\n  a hull would have been close to a no op. A flat panel "
                  f"is immune to this fault.")
        if d["bowed_edges"]:
            print(f"  bowed edges: {', '.join(d['bowed_edges'])}")
        un = [e["edge"] for e in d["edges"] if e["verdict"] == "UNMEASURED"]
        uc = [c["corner"] for c in d["corner_verdicts"]
              if c["verdict"] == "UNMEASURED"]
        if un:
            print(f"\n  {len(un)} edge(s) UNMEASURED: {', '.join(un)}.")
        if uc:
            print(f"  {len(uc)} CORNER(S) UNMEASURED: {', '.join(uc)}.")
            print("  A bite out of the middle of an edge invents nothing: the "
                  "bridge lies along\n  the edge it came out of. A missing "
                  "CORNER is different. The edge still\n  fits a line "
                  "beautifully and has simply stopped short of the corner "
                  "everyone\n  is about to read off it, so the corner is a "
                  "chord across a gap.")
            print("  Place those corners rigidly from the population and prove "
                  "it with `comp.py holdout`.")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- aspect


def cmd_aspect(args):
    quad = parse_quad(args.quad)
    raster = parse_raster(args.raster) if args.raster else None
    ra = G.rectangle_aspect(quad, raster=raster, focal_px=args.focal)

    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)
    H = G.h_from_quad(unit, quad)
    an = G.anisotropy(H)

    out = {"quad": quad.tolist(), "raster": raster,
           "anisotropy": an, "rectangle_aspect": ra}
    if ra.get("aspect"):
        out["content_viewport_hint"] = {
            "set_canvas_aspect_to_R": an["R"],
            "circle_renders_at_w_over_h": an["R"] / an["R"],
            "note": "lay the content out at R. A circle authored in the source "
                    "then renders at w/h = R divided by the canvas aspect, which "
                    "is 1 when the canvas aspect IS R."}

    def show(d):
        a = d["anisotropy"]
        print("image anisotropy, from the outline")
        print(f"  R                 {a['R']:.4f}   "
              f"(spread {a['R_min']:.4f} to {a['R_max']:.4f} across the panel)")
        print("  Lay the content out at R. This is what governs the SHAPE of "
              "anything drawn on\n  the panel, and it is an invariant of the "
              "outline: force the solve through any\n  assumed aspect you like "
              "and R does not move.")
        r = d["rectangle_aspect"]
        print(f"\nthe rectangle's TRUE aspect")
        print(f"  verdict           {r['verdict']}")
        if r.get("aspect"):
            print(f"  aspect            {r['aspect']:.4f}")
            print(f"  focal length      {r['focal_px']:.1f} px  ({r.get('focal_source')})")
            b = r.get("aspect_band_half_px_noise")
            if b:
                print(f"  half a pixel of corner noise moves it over "
                      f"{b[0]:.3f} to {b[1]:.3f}")
        print(f"  vanishing points  width {r['vp_width_distance_pictures']:.1f}, "
              f"height {r['vp_height_distance_pictures']:.1f} pictures away")
        if r.get("reason"):
            print(f"  {r['reason']}")
        if r["verdict"] != "DETERMINED":
            print("\n  The aspect is a gauge this outline cannot pin, and the "
                  "solver trades it\n  against yaw: the solve can be right on the "
                  "boundary and wrong in the\n  interior, and a green key hides "
                  "that completely. Verify on the DELIVERED\n  file against "
                  "something whose true shape you know. Round UI elements are\n"
                  "  ideal, and the same screen's closeup elsewhere in the cut is "
                  "better still.")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- key


def cmd_key(args):
    img = P.read_image(args.plate)
    clean = P.read_image(args.clean_plate) if args.clean_plate else None

    # A garbage matte is not a nicety. A key is a statement about a BACKING, and
    # anything else in frame that happens to be near the backing colour, a green
    # exit sign, a plant, a stripe on a jacket, joins the backing as far as the
    # key is concerned. Restricting the key to where the backing actually is is
    # the difference between a matte and a guess.
    garbage = None
    if args.garbage:
        garbage = load_mask(args.garbage).astype(bool)
    elif args.region:
        x, y, w, h = [int(v) for v in parse_region(args.region)]
        garbage = np.zeros((img.height, img.width), dtype=bool)
        garbage[y:y + h, x:x + w] = True

    if args.method == "difference":
        r = M.key_difference(img, screen=args.screen, softness=args.softness,
                             clean_plate=clean, roi=garbage)
    elif args.method == "model":
        r = M.key_backing_model(img, screen=args.screen, order=args.order,
                                roi=garbage)
        if r.get("alpha") is None:
            raise RuntimeError(r["reason"])
    else:
        r = M.key_union(img, screen=args.screen, softness=args.softness,
                        clean_plate=clean, order=args.order, roi=garbage)

    alpha = r["alpha"]
    if garbage is not None:
        # Outside the garbage matte there is no backing, so there is nothing to
        # key: alpha is solid there by definition, not by measurement.
        alpha = np.where(garbage, alpha, 1.0).astype(np.float32)
        r["alpha"] = alpha
    if args.choke or args.soften or args.gamma != 1.0:
        alpha = M.choke(alpha, pixels=args.choke, softness=args.soften,
                        gamma=args.gamma)
        r["alpha"] = alpha

    lin = img.as_linear()
    viol = M.violation_report(lin.rgb, alpha, screen=args.screen, a2=args.a2)

    rgb = lin.rgb
    dsp = None
    if args.despill != "none":
        dsp = M.despill(rgb, screen=args.screen, form=args.despill,
                        strength=args.despill_strength,
                        preserve_luma=args.preserve_luma, alpha=alpha)
        rgb = dsp["rgb"]

    out = {"plate": os.path.abspath(args.plate), "method": r["method"],
           "screen": args.screen,
           "provenance": img.provenance(),
           "garbage_matte": (os.path.abspath(args.garbage) if args.garbage
                             else (args.region if args.region else None)),
           "coverage": {"alpha_mean": float(alpha.mean()),
                        "solid_px": int((alpha > 0.99).sum()),
                        "clear_px": int((alpha < 0.01).sum()),
                        "soft_px": int(((alpha >= 0.01) & (alpha <= 0.99)).sum())},
           "violation": {k: v for k, v in viol.items() if k != "map"},
           "union": _clean(r.get("union")) if r.get("union") else None,
           "backing_rule": (r.get("backing_rule")
                            or (r.get("model_detail") or {}).get("backing_rule")),
           "despill": ({k: v for k, v in dsp.items()
                        if k not in ("rgb", "removed", "hue_map_deg")}
                       if dsp else None),
           "note": M.UNDERDETERMINED}

    if args.out:
        res = P.Image(rgb, "linear", img.source, img.decode_path,
                      alpha=alpha, premul=False, bits=img.bits)
        out["written"] = P.write_image(args.out, res, transfer=args.transfer)
    if args.out_alpha:
        a3 = np.repeat(alpha[..., None], 3, axis=2)
        out["written_alpha"] = P.write_image(
            args.out_alpha,
            P.Image(a3, "linear", img.source, img.decode_path, bits=img.bits),
            transfer="linear")

    def show(d):
        print(f"key: {os.path.basename(d['plate'])}   {d['method']}")
        cov = d["coverage"]
        print(f"  solid {cov['solid_px']:>9d} px   soft {cov['soft_px']:>9d} px"
              f"   clear {cov['clear_px']:>9d} px")
        if d.get("garbage_matte"):
            print(f"  garbage matte     {d['garbage_matte']}")
        else:
            print("  garbage matte     NONE. Everything in frame that is near "
                  "the backing colour\n                    joins the backing. "
                  "Pass --garbage or --region.")
        if d.get("backing_rule"):
            print(f"  backing rule      {d['backing_rule']}")
        u = d.get("union")
        if u and u.get("verdict") == "MEASURED":
            print(f"  the two keys disagree over {u['disagreement_px']} px "
                  f"({100 * u['disagreement_fraction']:.1f}% of the matte). "
                  f"Look there.")
        v = d["violation"]
        print(f"\n  Vlahos violation  {v['violating_px']} px = "
              f"{100 * v['violating_fraction']:.1f}% of the foreground")
        if v["violating_fraction"] > 0.005:
            print(f"  {v['meaning']}")
        ds = d.get("despill")
        if ds:
            print(f"\n  despill           {ds['form']}"
                  f"{' (luma preserved)' if ds['preserve_luma'] else ''}")
            print(f"      touched {100 * ds['foreground_affected_fraction']:.1f}% "
                  f"of the foreground, luma "
                  f"{ds['foreground_luma_change_mean']:+.4f} mean")
            if ds["hue_rotated_px"]:
                print(f"      {ds['hue_rotated_px']} px rotated in hue, worst "
                      f"{ds['hue_rotation_worst_deg']:+.1f} degrees")
                print(f"      {ds['hue_risk_note']}")
        print(f"\n  {d['note']}")
        for k in ("written", "written_alpha"):
            if d.get(k):
                print(f"  {k:14s} {d[k]['path']}  ({d[k]['bits']} bit, "
                      f"{d[k]['transfer']})")

    return emit(out, args.json, show)


def cmd_despill(args):
    img = P.read_image(args.plate).as_linear()
    alpha = None
    if args.alpha:
        alpha = P.read_image(args.alpha)
        alpha = (alpha.alpha if alpha.alpha is not None
                 else (alpha.rgb @ P.LUMA_709))
    d = M.despill(img.rgb, screen=args.screen, form=args.form,
                  strength=args.strength, preserve_luma=args.preserve_luma,
                  alpha=alpha)
    out = {k: v for k, v in d.items()
           if k not in ("rgb", "removed", "hue_map_deg")}
    out["plate"] = os.path.abspath(args.plate)
    if args.out:
        res = P.Image(d["rgb"], "linear", img.source, img.decode_path,
                      alpha=alpha, bits=img.bits)
        out["written"] = P.write_image(args.out, res, transfer=args.transfer)

    def show(x):
        print(f"despill {x['form']}: {os.path.basename(x['plate'])}")
        if not x["foreground_measured"]:
            print("  NO ALPHA GIVEN. Every number below counts the backing too, "
                  "where despill\n  is supposed to flatten everything, so they "
                  "mean nothing. Pass --alpha.")
        print(f"  affected          {100 * x['foreground_affected_fraction']:.1f}%"
              f" of the foreground")
        print(f"  luminance         {x['foreground_luma_change_mean']:+.4f} mean, "
              f"{x['foreground_luma_change_worst']:+.4f} worst")
        print(f"  hue rotated       {x['hue_rotated_px']} px, worst "
              f"{x['hue_rotation_worst_deg']:+.1f} degrees, "
              f"dE76 up to {x['delta_e76_worst']:.1f}")
        print(f"  {x['hue_risk_note']}")

    return emit(out, args.json, show)


def cmd_triangulate(args):
    r = M.triangulate(P.read_image(args.fg1), P.read_image(args.fg2),
                      P.read_image(args.backing1), P.read_image(args.backing2))
    out = {k: v for k, v in r.items()
           if k not in ("alpha", "foreground", "unsolved_map")}
    if args.out:
        res = P.Image(r["foreground"], "linear", args.fg1, "triangulation",
                      alpha=r["alpha"], premul=True)
        out["written"] = P.write_image(args.out, res, transfer=args.transfer)

    def show(d):
        print("triangulation matte  " + d["method"])
        print(f"  unsolved          {d['unsolved_px']} px "
              f"({100 * d['unsolved_fraction']:.3f}%) where the two backings "
              f"are identical")
        print(f"  clipped           {d['clipped_px']} px fell outside 0..1")
        print(f"  {d['note']}")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- insert


def cmd_insert(args):
    r = G.horizon_insert(args.image_height, args.horizon, args.base,
                         args.camera_height, args.object_height)
    if args.base_b is not None:
        rb = G.horizon_insert(args.image_height_b or args.image_height,
                              args.horizon_b if args.horizon_b is not None
                              else args.horizon,
                              args.base_b, args.camera_height,
                              args.object_height)
        r["second_frame"] = rb
        r["ratio_between_frames"] = r["object_px"] / rb["object_px"]

    def show(d):
        print("horizon ratio insert")
        print(f"  horizon at y      {d['y_horizon']:.1f}")
        print(f"  base at y         {d['y_base']:.1f}   "
              f"(drop {d['drop_px']:.1f} px)")
        print(f"  object            {d['object_height']:.3f} tall, camera at "
              f"{d['camera_height']:.3f}")
        print(f"  render it at      {d['object_px']:.1f} px tall")
        if d.get("second_frame"):
            s = d["second_frame"]
            print(f"  second frame      {s['object_px']:.1f} px tall "
                  f"(drop {s['drop_px']:.1f} px)")
            print("  Feeding ONE ratio into both frames is the only checkable "
                  "meaning of\n  'the same size in both shots'. Matching pixel "
                  "heights is wrong: the camera moved.")
        print("\n  Take the horizon from the vanishing point and cross check it "
              "against eye\n  lines: a taller person's eyes plot above it, a "
              "shorter person's below.\n  Do not register the two frames first. "
              "Feature matching and dense flow both\n  lock onto bokeh discs and "
              "wet road reflections, which are virtual images\n  and do not move "
              "like the ground plane.")

    return emit(r, args.json, show)


# ---------------------------------------------------------------- grain


def cmd_grain(args):
    plate = P.read_image(args.plate) if not args.frame_of else \
        P.frame_at(args.plate, args.frame_of)
    g = P.grain_extract(plate, sigma=args.sigma)
    out = {k: v for k, v in g.items() if k != "residual"}

    if args.apply_to:
        target = P.read_image(args.apply_to).as_linear()
        mask = None
        if args.mask:
            mask = load_mask(args.mask).astype(np.float32)
        rgb = P.grain_apply(target.rgb, g["residual"], mask=mask, gain=args.gain)
        res = P.Image(rgb, "linear", target.source, target.decode_path,
                      bits=target.bits)
        out["written"] = P.write_image(args.out or "regrained.png", res,
                                       transfer=args.transfer)

    def show(d):
        print(f"grain measured on {os.path.basename(d['source'])}")
        print(f"  sigma per channel R {d['sigma_rgb'][0]:.5f}  "
              f"G {d['sigma_rgb'][1]:.5f}  B {d['sigma_rgb'][2]:.5f}   (linear)")
        if d["bands"]:
            print("  by luminance band, because grain on a real plate is density "
                  "dependent:")
            for b in d["bands"]:
                print(f"      {b['luma_lo']:.2f} to {b['luma_hi']:.2f}  "
                      f"R {b['sigma_rgb'][0]:.5f} G {b['sigma_rgb'][1]:.5f} "
                      f"B {b['sigma_rgb'][2]:.5f}   ({b['pixels']} px)")
            print("  A flat grain laid over an insert reads as video noise. "
                  "Weight it by band.")
        if d.get("written"):
            print(f"  written           {d['written']['path']}")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- warp


def _load_track(path):
    d = P.load_json(path)
    d["warps"] = {int(k): (np.array(v, dtype=np.float64) if v else None)
                  for k, v in d["warps"].items()}
    d["quads"] = {int(k): (np.array(v, dtype=np.float64) if v else None)
                  for k, v in d["quads"].items()}
    d["corners"] = np.array(d["corners"], dtype=np.float64)
    return d


def cmd_warp(args):
    import cv2

    tr = _load_track(args.track)
    art = P.read_image(args.art)
    info = P.clip_info(args.clip)
    W, H = info["width"], info["height"]

    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)
    ref_quad = (parse_quad(args.quad) if args.quad
                else np.asarray(tr["corners"], dtype=np.float64))

    # Lay the artwork out at the panel's own anisotropy, not at its own aspect.
    H_ref = G.h_from_quad(unit, ref_quad)
    an = G.anisotropy(H_ref)
    R = an["R"]
    src_h = int(round(args.art_height or art.height))
    src_w = int(round(src_h * R))
    art_lin = art.as_linear()
    art_r = P.resize(art_lin.rgb, (src_w, src_h))
    art_a = (P.resize(art_lin.alpha, (src_w, src_h))
             if art_lin.alpha is not None else np.ones((src_h, src_w), np.float32))
    src_quad = np.array([[0, 0], [src_w - 1, 0], [src_w - 1, src_h - 1],
                         [0, src_h - 1]], dtype=np.float64)

    matte_mask = load_mask(args.matte) if args.matte else None

    stats = {"frames": 0, "unsolved": []}

    def frames():
        for idx, img in P.read_frames(args.clip):
            Wm = tr["warps"].get(idx)
            if Wm is None:
                stats["unsolved"].append(idx)
                yield img
                continue
            dst = G.apply_h(Wm, ref_quad)
            Hm = G.h_from_quad(src_quad, dst)
            warped = cv2.warpPerspective(art_r, Hm, (W, H),
                                         flags=cv2.INTER_LANCZOS4,
                                         borderMode=cv2.BORDER_REPLICATE)
            wa = cv2.warpPerspective(art_a, Hm, (W, H), flags=cv2.INTER_LINEAR,
                                     borderValue=0.0)
            # Confine to the ordered ring, at sub pixel, never a truncated cast.
            ring = G.fill_poly_subpixel((H, W), dst).astype(np.float32) / 255.0
            a = wa * ring
            if matte_mask is not None:
                a = a * matte_mask.astype(np.float32)
            if args.choke or args.soften:
                a = M.choke(a, pixels=args.choke, softness=args.soften)

            plate = img.as_linear()
            rgb = plate.rgb
            if args.despill != "none":
                band = cv2.dilate((ring > 0.01).astype(np.uint8),
                                  np.ones((args.despill_band,) * 2, np.uint8))
                d = M.despill(rgb, screen=args.screen, form=args.despill,
                              strength=args.despill_strength,
                              preserve_luma=args.preserve_luma,
                              alpha=(band > 0).astype(np.float32))
                rgb = np.where((band > 0)[..., None], d["rgb"], rgb)

            fg = P.Image(warped, "linear", args.art, art.decode_path, alpha=a)
            bg = P.Image(rgb, "linear", img.source, img.decode_path)
            out = P.composite_over(fg, bg)
            if args.light_wrap > 0:
                wrap, _ = M.light_wrap(bg.rgb, a, width=args.light_wrap_width,
                                       gain=args.light_wrap)
                out.rgb = out.rgb + wrap
            stats["frames"] += 1
            yield out

    res = P.write_clip(args.out, frames(), info["rate"],
                       source_audio=args.clip, crf=args.crf,
                       transfer=args.transfer)
    out = {"clip": info["path"], "art": os.path.abspath(args.art),
           "track": os.path.abspath(args.track),
           "anisotropy_R": R, "artwork_laid_out_at": f"{src_w}x{src_h}",
           "artwork_native": f"{art.width}x{art.height}",
           "unsolved_frames": stats["unsolved"], "written": res}

    def show(d):
        print(f"warp: {os.path.basename(d['art'])} onto "
              f"{os.path.basename(d['clip'])}")
        print(f"  panel anisotropy  R = {d['anisotropy_R']:.4f}")
        print(f"  artwork laid out  {d['artwork_laid_out_at']} "
              f"(native {d['artwork_native']})")
        if d["unsolved_frames"]:
            print(f"  UNSOLVED frames   {len(d['unsolved_frames'])}: "
                  f"{d['unsolved_frames'][:12]}")
            print("  Those frames were passed through untouched. They are not "
                  "composited.")
        print(f"  written           {d['written']['path']}  "
              f"({d['written']['frames']} frames, {d['written']['raster']})")
        print("\n  Nothing here is proof. Run:")
        print("    comp.py verify ring     --plate ... --comp ... --track ...")
        print("    comp.py verify content  --plate ... --comp ... --track ...")
        print("    comp.py verify channels --art ... --comp ... --track ...")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- verify


def _ring_bands(quad, shape, inner=6, outer=26):
    """A band just OUTSIDE a quad: the real bezel, which the build never touched."""
    import cv2
    h, w = shape[:2]
    fill = G.fill_poly_subpixel((h, w), quad)
    k_in = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (inner * 2 + 1,) * 2)
    k_out = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (outer * 2 + 1,) * 2)
    outside = cv2.dilate(fill, k_out) > 0
    near = cv2.dilate(fill, k_in) > 0
    return outside & ~near


def cmd_verify(args):
    if args.check == "ring":
        return _verify_ring(args)
    if args.check == "content":
        return _verify_content(args)
    if args.check == "channels":
        return _verify_channels(args)
    if args.check == "notch":
        return _verify_notch(args)
    if args.check == "rank":
        return _verify_rank(args)
    raise ValueError(f"unknown check {args.check!r}")


def _verify_ring(args):
    """Did the REAL bezel move? Anchored entirely in the plate."""
    tr = _load_track(args.track)
    quad_ref = tr["corners"]
    plate_frames = dict(P.read_frames(args.plate, start=args.start,
                                      count=args.count))
    comp_frames = dict(P.read_frames(args.comp, start=args.start,
                                     count=args.count))
    common = sorted(set(plate_frames) & set(comp_frames))
    if not common:
        raise RuntimeError("the plate and the composite share no frames")

    rows = []
    for i in common:
        Wm = tr["warps"].get(i)
        if Wm is None:
            continue
        quad = G.apply_h(Wm, quad_ref)
        band = _ring_bands(quad, (plate_frames[i].height, plate_frames[i].width),
                           inner=args.inner, outer=args.outer)
        if band.sum() < 200:
            continue
        lp = P.linear_luma(plate_frames[i].as_linear().rgb)[band]
        lc = P.linear_luma(comp_frames[i].as_linear().rgb)[band]
        d = np.abs(lc - lp)
        rows.append({"frame": i, "band_px": int(band.sum()),
                     "mean_abs": float(d.mean()), "p99": float(np.percentile(d, 99)),
                     "max": float(d.max())})
    if not rows:
        raise RuntimeError("no frame produced a usable ring")

    worst = max(rows, key=lambda r: r["p99"])
    floor = args.floor
    out = {"plate": os.path.abspath(args.plate), "comp": os.path.abspath(args.comp),
           "frames_checked": len(rows), "band": f"{args.inner} to {args.outer} px "
                                                f"outside the tracked quad",
           "worst_frame": worst,
           "mean_of_means": float(np.mean([r["mean_abs"] for r in rows])),
           "generation_floor": floor,
           "verdict": "PASS" if worst["p99"] <= floor else "FAIL",
           "rows": rows}

    def show(d):
        print("verify ring: did the real bezel move?")
        print(f"  band              {d['band']}, outside the composite entirely")
        print(f"  frames checked    {d['frames_checked']}")
        print(f"  worst frame       {d['worst_frame']['frame']}  "
              f"p99 {d['worst_frame']['p99']:.5f} linear luma")
        print(f"  generation floor  {d['generation_floor']:.5f}")
        print(f"  verdict           {d['verdict']}")
        if d["verdict"] == "FAIL":
            print("\n  The plate outside the composite is not the plate any more. "
                  "Either the\n  matte leaked, a despill band reached past the "
                  "bezel, or the whole frame\n  was re-encoded differently. Read "
                  "this against the FILE's own generation\n  floor "
                  "(`prove.py floor`), never against zero.")
        else:
            print("\n  This proves the composite stayed inside its panel. It does "
                  "NOT prove the\n  content is in the right place inside it: run "
                  "`verify content` for that.")

    out.pop("rows") if not args.json else None
    return emit(out, args.json, show)


def _verify_content(args):
    """Where did the content actually LAND, measured on the delivered file?

    The measurement a keyed matte cannot fake, and it deliberately uses nothing
    the build used. Two outlines, per frame:

      the BACKING, measured on the PLATE by keying it;
      the CONTENT, measured on the COMP as the region where the composite
      differs from the plate at all.

    Comparing those two catches both faults, and they are different faults. A
    constant offset is a REGISTRATION error: the content sits six pixels off the
    panel in every frame including the first, and a drift check would call it
    perfect because nothing is drifting. A growing difference is a TRACKING
    error. Both are reported.

    Do not point this at the region the track was solved on. Re-solving the same
    pixels with the same model against the same reference is not a second
    measurement, it is the same measurement typed twice, and it agrees to zero.
    """
    tr = _load_track(args.track) if args.track else None
    plate_frames = dict(P.read_frames(args.plate, start=args.start,
                                      count=args.count))
    comp_frames = dict(P.read_frames(args.comp, start=args.start,
                                     count=args.count))
    common = sorted(set(plate_frames) & set(comp_frames))
    if not common:
        raise RuntimeError("the plate and the composite share no frames")

    roi = None
    if args.region:
        x, y, w, h = [int(v) for v in parse_region(args.region)]
        roi = np.zeros((plate_frames[common[0]].height,
                        plate_frames[common[0]].width), dtype=bool)
        roi[y:y + h, x:x + w] = True
    elif tr is not None:
        # A generous box around wherever the track ever put the region: enough
        # to exclude the rest of the film, nowhere near tight enough to decide
        # the answer.
        pts = np.vstack([q for q in tr["quads"].values() if q is not None])
        pad = 0.25 * max(pts[:, 0].ptp() if hasattr(pts[:, 0], "ptp")
                         else pts[:, 0].max() - pts[:, 0].min(),
                         pts[:, 1].max() - pts[:, 1].min())
        roi = np.zeros((plate_frames[common[0]].height,
                        plate_frames[common[0]].width), dtype=bool)
        y0 = max(0, int(pts[:, 1].min() - pad))
        y1 = min(roi.shape[0], int(pts[:, 1].max() + pad))
        x0 = max(0, int(pts[:, 0].min() - pad))
        x1 = min(roi.shape[1], int(pts[:, 0].max() + pad))
        roi[y0:y1, x0:x1] = True

    # The threshold that decides what counts as "composited" is a MEASUREMENT,
    # not a setting. Everything outside the region was re-encoded but not
    # touched, so what it differs by IS this pair of files' own generation
    # floor. Set the threshold well above that and the outline is the artwork;
    # set it at the floor and the outline is codec noise, the check's own spread
    # swamps the thing it is measuring, and it bounds without ranking.
    floor_note = "given"
    if args.diff_floor is None:
        samples = []
        for i in common[:8]:
            d = np.abs(comp_frames[i].as_linear().rgb -
                       plate_frames[i].as_linear().rgb).max(axis=2)
            outside = d if roi is None else d[~roi]
            if outside.size:
                samples.append(float(np.percentile(outside, 99.5)))
        gen_floor = float(np.median(samples)) if samples else 0.01
        args.diff_floor = max(8.0 * gen_floor, 0.05)
        floor_note = (f"measured: the generation floor between these two files "
                      f"is {gen_floor:.4f} outside the region, so the threshold "
                      f"is 8x that")

    rows = []
    for i in common:
        plate = plate_frames[i].as_linear()
        comp = comp_frames[i].as_linear()

        # 1. the BACKING, on the plate
        k = M.key_difference(plate_frames[i], screen=args.screen, roi=roi)
        backing = (k["alpha"] < 0.5)
        if roi is not None:
            backing &= roi
        # 2. the CONTENT, on the comp: where it differs from the plate at all
        diff = np.abs(comp.rgb - plate.rgb).max(axis=2)
        content = diff > args.diff_floor
        if roi is not None:
            content &= roi

        if backing.sum() < 500 or content.sum() < 500:
            rows.append({"frame": i, "ok": False,
                         "reason": (f"backing {int(backing.sum())} px, content "
                                    f"{int(content.sum())} px: not enough of "
                                    "either to outline")})
            continue

        rb = G.ring_from_mask(backing.astype(np.uint8))
        rc = G.ring_from_mask(content.astype(np.uint8))
        if rb is None or rc is None:
            rows.append({"frame": i, "ok": False,
                         "reason": "no quadrilateral outline on one of them"})
            continue
        d = np.linalg.norm(rb["corners"] - rc["corners"], axis=1)
        centre = np.linalg.norm(rb["corners"].mean(axis=0) -
                                rc["corners"].mean(axis=0))
        rows.append({"frame": i, "ok": True, "worst_px": float(d.max()),
                     "mean_px": float(d.mean()), "centre_offset_px": float(centre),
                     "backing_px": int(backing.sum()),
                     "content_px": int(content.sum())})

    good = [r for r in rows if r.get("ok")]
    bad = [r for r in rows if not r.get("ok")]
    if not good:
        raise RuntimeError(
            "no frame could be outlined. "
            + (bad[0]["reason"] if bad else "")
            + "\nThis check needs a keyable backing on the plate and a "
            "composite that visibly differs from it. Give --region if the "
            "backing colour appears elsewhere in frame.")

    worst = max(good, key=lambda r: r["worst_px"])
    offsets = np.array([r["centre_offset_px"] for r in good])
    # A registration error is a constant; a tracking error grows. Separating
    # them is what tells somebody whether to nudge the insert or re-solve.
    registration = float(np.median(offsets))
    drift = float(offsets.max() - offsets.min())

    out = {"plate": os.path.abspath(args.plate),
           "comp": os.path.abspath(args.comp),
           "reference": "the backing keyed on the PLATE against the changed "
                        "region measured on the COMP; the track was not used",
           "frames_checked": len(good),
           "frames_unmeasurable": len(bad),
           "first_unmeasurable_reason": bad[0]["reason"] if bad else None,
           "worst": worst,
           "median_worst_px": float(np.median([r["worst_px"] for r in good])),
           "registration_offset_px": registration,
           "drift_px": drift,
           "tolerance_px": args.tolerance,
           "diff_floor": args.diff_floor, "diff_floor_note": floor_note,
           "outline_noise_px": float(np.std([r["worst_px"] for r in good])),
           # The verdict is on the MEDIAN and on the registration, not on the
           # worst frame. A single frame's outline carries this check's own
           # spread, and judging a film on the worst reading of a noisy
           # measurement fails correct work. The worst frame is reported beside
           # it, as something to go and look at.
           "verdict": ("PASS" if (float(np.median([r["worst_px"] for r in good]))
                                  <= args.tolerance
                                  and registration <= args.tolerance)
                       else "FAIL"),
           "outlier_frames": [r["frame"] for r in good
                              if r["worst_px"] > np.median(
                                  [g["worst_px"] for g in good]) +
                              3 * np.std([g["worst_px"] for g in good])],
           "rows": rows}

    # Optional second reading: does the content move with the object's own BODY?
    if args.body or args.body_mask:
        out["body_check"] = _body_drift(args, tr, plate_frames, common)

    def show(d):
        print("verify content: where did the content actually land?")
        print(f"  measured from     {d['reference']}")
        print(f"  change threshold  {d['diff_floor']:.4f}  ({d['diff_floor_note']})")
        print(f"  frames checked    {d['frames_checked']}")
        if d.get("frames_unmeasurable"):
            print(f"  UNMEASURABLE      {d['frames_unmeasurable']} frames: "
                  f"{d['first_unmeasurable_reason']}")
        print(f"  worst frame       {d['worst']['frame']}  "
              f"{d['worst']['worst_px']:.3f} px at a corner")
        print(f"  median            {d['median_worst_px']:.3f} px")
        print(f"  registration      {d['registration_offset_px']:.3f} px "
              f"constant offset")
        print(f"  drift             {d['drift_px']:.3f} px of change across the "
              f"clip")
        print(f"  outline noise     {d['outline_noise_px']:.3f} px, this check's "
              f"own spread")
        if d["drift_px"] <= 2.0 * d["outline_noise_px"]:
            print("      The drift is inside this check's own noise, so it "
                  "bounds the drift and\n      cannot rank it. Read the "
                  "registration number, which is a median and is\n      "
                  "therefore far steadier than any single frame.")
        print(f"  tolerance         {d['tolerance_px']:.3f} px, applied to the "
              f"median and the registration")
        if d.get("outlier_frames"):
            print(f"  outlier frames    {d['outlier_frames'][:10]}  "
                  f"(look at these; they are not what decided the verdict)")
        print(f"  verdict           {d['verdict']}")
        bc = d.get("body_check")
        if bc:
            print(f"\n  against the object's own body: {bc['verdict']}, "
                  f"worst {bc.get('worst_px', float('nan')):.3f} px")
            if bc.get("note"):
                print(f"      {bc['note']}")
        print("\n  This is the check a keyed matte cannot fake. 'No plate green "
              "survives' and\n  'nothing outside the panel moved' both read zero "
              "on a composite that is six\n  pixels out, because the alpha came "
              "from the plate's own green.")
        if d["verdict"] == "FAIL" and d["registration_offset_px"] > d["drift_px"]:
            print("\n  The offset is mostly CONSTANT, so this is a registration "
                  "error, not a\n  tracking one: the insert is in the wrong "
                  "place, but it is following the\n  panel correctly. Nudge it; "
                  "do not re-solve.")
        elif d["verdict"] == "FAIL":
            print("\n  The offset CHANGES across the clip, so the track itself "
                  "is wrong. Re-solve.")

    if not args.json:
        out.pop("rows")
    return emit(out, args.json, show)


def _body_drift(args, tr, plate_frames, common):
    """Does the content move with the object's own body? A second, weaker read.

    Weaker because it needs a track file to have something to compare against,
    and because a body window that happens to be the region the track was solved
    on is not a second measurement at all: it is the same solve run twice, and
    it agrees to zero. That case is detected and refused.
    """
    if tr is None:
        return {"verdict": "UNPROVEN", "note": "no track file to compare with"}
    ref = common[0]
    if args.body_mask:
        body_mask = load_mask(args.body_mask)
    else:
        x, y, w, h = [int(v) for v in parse_region(args.body)]
        body_mask = np.zeros((plate_frames[ref].height, plate_frames[ref].width),
                             np.uint8)
        body_mask[y:y + h, x:x + w] = 1

    same = None
    if tr.get("mask_signature"):
        same = tr["mask_signature"] == _mask_signature(body_mask)
    if same:
        return {"verdict": "REFUSED",
                "note": ("this body window is the region the track was solved "
                         "on. Re-solving the same pixels with the same model "
                         "against the same reference is not a second "
                         "measurement, and it will agree to zero. Choose a "
                         "different part of the object.")}

    quad_ref = tr["corners"]
    init = np.eye(3)
    errs = []
    for i in common:
        Wm = tr["warps"].get(i)
        if Wm is None:
            continue
        r = T.ecc_solve(plate_frames[ref], plate_frames[i],
                        model=args.body_model, mask=body_mask, scale=1.0,
                        gauss=args.gauss, init=init)
        if not r["ok"] or r["cc"] < args.min_cc:
            continue
        plaus = T.warp_plausible(r["warp"], quad_ref,
                                 (plate_frames[ref].width,
                                  plate_frames[ref].height))
        if not plaus["ok"]:
            continue
        init = r["warp"]
        d = np.linalg.norm(G.apply_h(r["warp"], quad_ref) -
                           G.apply_h(Wm, quad_ref), axis=1)
        errs.append(float(d.max()))
    if not errs:
        return {"verdict": "UNPROVEN",
                "note": "the body window would not solve on any frame"}
    return {"verdict": "PASS" if max(errs) <= args.tolerance else "FAIL",
            "worst_px": float(max(errs)), "median_px": float(np.median(errs)),
            "frames": len(errs),
            "note": ("a window on the room BEHIND the screen is a different "
                     "rigid thing and will disagree however good the track is")}


def _mask_signature(mask):
    import hashlib
    return hashlib.sha256(np.ascontiguousarray(
        (np.asarray(mask) > 0).astype(np.uint8)).tobytes()).hexdigest()[:16]


def _verify_channels(args):
    """Red and blue swapped, at the one place in the frame that can see it.

    A swap is a no op on every neutral and on anything whose colour lives in the
    green channel, which covers most user interfaces. A mean colour cannot see
    it and neither can a histogram: the set of channel values is unchanged. The
    only thing that sees it is the most saturated NON GREEN element in the
    artwork, looked at 1:1 in the RENDERED frame.
    """
    art = P.read_image(args.art).as_linear()
    tr = _load_track(args.track)
    frame = P.frame_at(args.comp, args.frame).as_linear()

    a = art.rgb
    chroma = a.max(axis=2) - a.min(axis=2)
    greenish = (np.argmax(a, axis=2) == 1)
    score = np.where(greenish, 0.0, chroma)
    if args.alpha_gate and art.alpha is not None:
        score = np.where(art.alpha > 0.9, score, 0.0)
    if score.max() <= 0.02:
        return emit({"verdict": "UNPROVEN",
                     "reason": "this artwork has no saturated non green element, "
                               "so nothing in it can see a red and blue swap. "
                               "Find a frame of the film that has one."},
                    args.json)

    ys, xs = np.nonzero(score > score.max() * 0.9)
    pick = len(ys) // 2
    py, px = int(ys[pick]), int(xs[pick])
    expected = a[py, px]

    unit_src = np.array([[0, 0], [a.shape[1] - 1, 0],
                         [a.shape[1] - 1, a.shape[0] - 1], [0, a.shape[0] - 1]],
                        dtype=np.float64)
    Wm = tr["warps"].get(args.frame)
    if Wm is None:
        raise RuntimeError(f"the track has no solve for frame {args.frame}")
    dst = G.apply_h(Wm, tr["corners"])
    Hm = G.h_from_quad(unit_src, dst)
    q = G.apply_h(Hm, [[px, py]])[0]
    qx, qy = int(round(q[0])), int(round(q[1]))
    if not (0 <= qx < frame.width and 0 <= qy < frame.height):
        raise RuntimeError("that element does not land inside the rendered frame")

    r = args.patch
    patch = frame.rgb[max(0, qy - r):qy + r + 1, max(0, qx - r):qx + r + 1]
    got = patch.reshape(-1, 3).mean(axis=0)

    straight = float(np.linalg.norm(got - expected))
    swapped = float(np.linalg.norm(got - expected[::-1]))
    out = {"art": os.path.abspath(args.art), "comp": os.path.abspath(args.comp),
           "frame": args.frame,
           "element_in_art": [px, py], "element_in_frame": [qx, qy],
           "expected_rgb": expected.tolist(),
           "measured_rgb": got.tolist(),
           "distance_as_written": straight,
           "distance_if_swapped": swapped,
           "verdict": ("SWAPPED" if swapped < straight * 0.5 else
                       "OK" if straight < swapped * 0.5 else "AMBIGUOUS")}

    def show(d):
        print("verify channels: red and blue")
        print(f"  most saturated non green element in the artwork at "
              f"{d['element_in_art']}")
        print(f"  lands at          {d['element_in_frame']} in frame {d['frame']}")
        print(f"  expected RGB      {np.round(d['expected_rgb'], 4).tolist()}")
        print(f"  measured RGB      {np.round(d['measured_rgb'], 4).tolist()}")
        print(f"  distance as written {d['distance_as_written']:.4f}   "
              f"if swapped {d['distance_if_swapped']:.4f}")
        print(f"  verdict           {d['verdict']}")
        if d["verdict"] == "SWAPPED":
            print("\n  Red and blue are swapped. Find the boundary where the "
                  "content crosses\n  between an OpenCV path (BGR) and an ffmpeg "
                  "raw path (usually RGB).")
        elif d["verdict"] == "AMBIGUOUS":
            print("\n  The two hypotheses are not separated. This element is not "
                  "saturated\n  enough at this size to decide. Pick another, or "
                  "look at 1:1.")

    return emit(out, args.json, show)


def _verify_notch(args):
    """The premultiplied fringe, measured on the actual artwork.

    Detects whether the artwork fills its own canvas, and if it does, measures
    what a straight blur would do at the four points where it touches.
    """
    img = P.read_image(args.art)
    if img.alpha is None:
        return emit({"verdict": "NOT APPLICABLE",
                     "reason": "this file has no alpha channel, so there is "
                               "nothing to premultiply"}, args.json)
    lin = img.as_linear()
    a = lin.alpha
    h, w = a.shape
    edges = {"top": a[0, :], "bottom": a[-1, :], "left": a[:, 0], "right": a[:, -1]}
    touching = {k: float((v > 0.5).mean()) for k, v in edges.items()}
    fills = [k for k, v in touching.items() if v > 0.02]

    sig = args.sigma
    # Reproduce what actually happens: the artwork gets PLACED into a bigger
    # canvas and then softened. Blurring it in isolation cannot show the fault,
    # because a blur of a mark against its own reflected edge has nothing
    # foreign to drag in. The pad is where the foreign colour comes from.
    pad = max(4, int(np.ceil(sig * 4)))
    rgb_p = np.pad(lin.rgb, ((pad, pad), (pad, pad), (0, 0)), mode="constant")
    a_p = np.pad(a, ((pad, pad), (pad, pad)), mode="constant")
    cn, an = P.blur_naive(rgb_p, a_p, sig)
    cg, ag = P.blur_rgba(rgb_p, a_p, sig)
    H2, W2 = a_p.shape
    pts = {"top": (pad + 1, W2 // 2), "bottom": (pad + h - 2, W2 // 2),
           "left": (H2 // 2, pad + 1), "right": (H2 // 2, pad + w - 2)}
    ref = float(P.linear_luma(lin.rgb[h // 2:h // 2 + 1, w // 2:w // 2 + 1])[0, 0])
    rows = {}
    for name, (y, x) in pts.items():
        if name not in fills:
            continue
        ln = float(P.linear_luma(cn[y:y + 1, x:x + 1])[0, 0])
        lg = float(P.linear_luma(cg[y:y + 1, x:x + 1])[0, 0])
        rows[name] = {"straight_blur": ln / max(ref, 1e-9),
                      "premultiplied_padded": lg / max(ref, 1e-9),
                      "notch_depth": float(1.0 - ln / max(lg, 1e-9))}

    worst = max((r["notch_depth"] for r in rows.values()), default=0.0)
    out = {"art": os.path.abspath(args.art), "sigma": sig, "pad_px": pad,
           "alpha_reaches_canvas_edge": touching,
           "edges_that_fill": fills, "points": rows,
           "worst_notch_depth": worst,
           "verdict": ("AT RISK" if worst > 0.02 else
                       "SAFE" if fills else "NOT APPLICABLE")}

    def show(d):
        print(f"verify notch: {os.path.basename(d['art'])}")
        if not d["edges_that_fill"]:
            print("  The artwork does not reach its own canvas edge, so the four "
                  "notch fault\n  cannot occur here. Any blur must still be "
                  "premultiplied and padded.")
            return
        print(f"  the artwork fills its canvas at: "
              f"{', '.join(d['edges_that_fill'])}")
        print(f"  with a blur of sigma {d['sigma']}:")
        for name, r in d["points"].items():
            print(f"      {name:7s} straight blur {r['straight_blur']:.3f} of the "
                  f"mark's own value, premultiplied and padded "
                  f"{r['premultiplied_padded']:.3f}")
        print(f"  worst notch       {100 * d['worst_notch_depth']:.1f}% darker")
        print(f"  verdict           {d['verdict']}")
        print("\n  A client reads this as 'dark spots top, bottom, left and "
              "right', never as\n  'a dark ring'. Blur rgb*a and a, divide back "
              "out, and PAD the working copy\n  first or the blur has nowhere to "
              "fall off to.")

    return emit(out, args.json, show)


def _verify_rank(args):
    """Can this check rank these versions, or does it only bound them?

    Cross pair every version with every reference. If the spread a single
    version shows ACROSS references is as large as the spread across versions
    for a fixed reference, the check's own noise is bigger than the difference
    it is being asked to rank, and it bounds but cannot rank. Saying that is the
    correct answer; picking a winner is not.
    """
    versions = [P.read_image(p).as_linear() for p in args.versions]
    refs = [P.read_image(p).as_linear() for p in args.references]
    if len(refs) < 2:
        raise ValueError("ranking needs at least two references; one reference "
                         "cannot tell you what the check's own spread is")

    matrix = []
    for v, vp in zip(versions, args.versions):
        row = []
        for r in refs:
            if v.rgb.shape != r.rgb.shape:
                raise ValueError(f"{vp} and a reference are different rasters")
            row.append(float(np.abs(v.rgb - r.rgb).mean()))
        matrix.append(row)
    Mx = np.array(matrix)

    across_refs = float(np.median(Mx.max(axis=1) - Mx.min(axis=1)))
    across_versions = float(np.median(Mx.max(axis=0) - Mx.min(axis=0)))
    can_rank = across_versions > 2.0 * across_refs

    order = np.argsort(Mx.mean(axis=1))
    out = {"versions": [os.path.basename(p) for p in args.versions],
           "references": [os.path.basename(p) for p in args.references],
           "matrix": Mx.tolist(),
           "spread_across_references": across_refs,
           "spread_across_versions": across_versions,
           "verdict": "CAN RANK" if can_rank else "BOUNDS BUT CANNOT RANK",
           "ordering": ([os.path.basename(args.versions[i]) for i in order]
                        if can_rank else None)}

    def show(d):
        print("verify rank: can this check separate these versions?")
        print(f"  spread across references, for a fixed version: "
              f"{d['spread_across_references']:.6f}")
        print(f"  spread across versions,  for a fixed reference: "
              f"{d['spread_across_versions']:.6f}")
        print(f"  verdict           {d['verdict']}")
        if d["ordering"]:
            print(f"  ordering          {' < '.join(d['ordering'])}")
        else:
            print("\n  The check's own noise is as large as the difference it is "
                  "being asked to\n  rank. It bounds the versions and it cannot "
                  "order them. Move the ranking to\n  a measurement with real "
                  "ground: hold out frames, or a reference the film\n  already "
                  "contains.")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- holdout


def cmd_holdout(args):
    tr = _load_track(args.track)
    quads = P.load_json(args.detections) if args.detections else None
    frames = sorted(tr["warps"])
    if quads is None:
        qs = [tr["quads"].get(i) for i in frames]
    else:
        qs = [np.array(quads[str(i)], dtype=np.float64) if quads.get(str(i))
              else None for i in frames]
    ws = [tr["warps"].get(i) for i in frames]

    anch = G.anchored_shape([q for q in qs if q is not None],
                            [w for q, w in zip(qs, ws) if q is not None])
    ho = G.holdout_shape(qs, ws)
    out = {"track": os.path.abspath(args.track),
           "frames": len(frames),
           "anchored": {k: v for k, v in anch.items()
                        if k not in ("shape", "per_frame_scatter")},
           "shape": anch["shape"].tolist(),
           "holdout": {k: v for k, v in ho.items() if k != "per_frame_px"},
           "verdict": ("PASS" if ho.get("worst_px", 1e9) <= args.tolerance
                       else "FAIL" if ho.get("verdict") == "MEASURED"
                       else "UNPROVEN")}

    def show(d):
        print("holdout: the rigid shape, proved by leaving frames out")
        a = d["anchored"]
        print(f"  frames used       {a['n_frames']}")
        print(f"  scatter           mean {a['scatter_px_mean']:.3f} px, "
              f"p95 {a['scatter_px_p95']:.3f} px")
        h = d["holdout"]
        if h.get("verdict") == "MEASURED":
            print(f"  hold out          worst {h['worst_px']:.3f} px, "
                  f"median {h['median_px']:.3f} px over {h['n_folds']} folds")
        else:
            print(f"  hold out          {h.get('reason')}")
        print(f"  tolerance         {args.tolerance:.3f} px")
        print(f"  verdict           {d['verdict']}")
        print("\n  The shape came from the population, pulled back through each "
              "frame's OWN\n  homography, so nothing in it varies with frame "
              "number and no real motion\n  can have been smoothed away by it. "
              "Each frame's POSITION is still its own.")

    return emit(out, args.json, show)


# ---------------------------------------------------------------- cli


def build_parser():
    ap = argparse.ArgumentParser(
        prog="comp.py", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("cadence", help="Is this plate a conformed one?")
    p.add_argument("clip")
    p.add_argument("--region", help="x,y,w,h over the thing that moves")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=None)
    C.add_json(p)
    p.set_defaults(fn=cmd_cadence)

    p = sub.add_parser("track", help="Reference to frame solve, certified twice")
    p.add_argument("clip")
    p.add_argument("--region", help="x,y,w,h on the reference frame")
    p.add_argument("--mask", help="a matte defining the region")
    p.add_argument("--ref", type=int, default=0)
    p.add_argument("--model", default="auto", choices=("auto",) + T.MODELS)
    p.add_argument("--scale", type=float, default=None,
                   help="leave unset and it is MEASURED on this plate")
    p.add_argument("--gauss", type=int, default=None)
    p.add_argument("--tune-tolerance", type=float, default=0.25)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--certify-every", type=int, default=8)
    p.add_argument("--min-cc", type=float, default=T.MIN_CC,
                   help="below this correlation a frame is UNSOLVED, not "
                        "guessed")
    p.add_argument("--out", help="write the track as JSON")
    C.add_json(p)
    p.set_defaults(fn=cmd_track)

    p = sub.add_parser("quad", help="Ordered ring, per edge verdicts, hull cost")
    p.add_argument("--mask", required=True)
    p.add_argument("--frame", help="the frame the matte came from, for the record")
    p.add_argument("--curved", action="store_true",
                   help="fit a bow on each edge (a curved panel needs this)")
    p.add_argument("--corner-frac", type=float, default=0.15)
    p.add_argument("--scanlines", type=int, default=64)
    p.add_argument("--min-lines", type=int, default=12)
    p.add_argument("--max-rms", type=float, default=1.5)
    p.add_argument("--min-span", type=float, default=0.35)
    p.add_argument("--out")
    C.add_json(p)
    p.set_defaults(fn=cmd_quad)

    p = sub.add_parser("aspect", help="R from the outline, and the true aspect")
    p.add_argument("--quad", required=True)
    p.add_argument("--raster", help="WxH, for the principal point")
    p.add_argument("--focal", type=float, default=None,
                   help="focal length in pixels, if the lens is known")
    C.add_json(p)
    p.set_defaults(fn=cmd_aspect)

    p = sub.add_parser("key", help="Pull a matte, and report what it cannot know")
    p.add_argument("plate")
    p.add_argument("--screen", default="green", choices=tuple(M.SCREENS))
    p.add_argument("--method", default="union",
                   choices=("difference", "model", "union"))
    p.add_argument("--clean-plate", help="the backing shot without the object")
    p.add_argument("--garbage", help="a garbage matte: where the backing IS")
    p.add_argument("--region", help="x,y,w,h as a rectangular garbage matte")
    p.add_argument("--softness", type=float, default=0.72)
    p.add_argument("--order", type=int, default=2)
    p.add_argument("--a2", type=float, default=1.0)
    p.add_argument("--choke", type=float, default=0.0)
    p.add_argument("--soften", type=float, default=0.0)
    p.add_argument("--gamma", type=float, default=1.0)
    p.add_argument("--despill", default="limit_max",
                   choices=("none",) + M.DESPILL_FORMS)
    p.add_argument("--despill-strength", type=float, default=1.0)
    p.add_argument("--preserve-luma", action="store_true")
    p.add_argument("--transfer", default="srgb", choices=P.TRANSFERS)
    p.add_argument("--out")
    p.add_argument("--out-alpha")
    C.add_json(p)
    p.set_defaults(fn=cmd_key)

    p = sub.add_parser("despill", help="A named despill form, with its cost")
    p.add_argument("plate")
    p.add_argument("--screen", default="green", choices=tuple(M.SCREENS))
    p.add_argument("--form", default="limit_max", choices=M.DESPILL_FORMS)
    p.add_argument("--strength", type=float, default=1.0)
    p.add_argument("--alpha", help="the matte, so the numbers mean something")
    p.add_argument("--preserve-luma", action="store_true")
    p.add_argument("--transfer", default="srgb", choices=P.TRANSFERS)
    p.add_argument("--out")
    C.add_json(p)
    p.set_defaults(fn=cmd_despill)

    p = sub.add_parser("triangulate", help="The exact matte, from two backings")
    p.add_argument("fg1")
    p.add_argument("fg2")
    p.add_argument("backing1")
    p.add_argument("backing2")
    p.add_argument("--transfer", default="srgb", choices=P.TRANSFERS)
    p.add_argument("--out")
    C.add_json(p)
    p.set_defaults(fn=cmd_triangulate)

    p = sub.add_parser("insert", help="The horizon ratio, for a level camera")
    p.add_argument("--image-height", type=float, required=True)
    p.add_argument("--horizon", type=float, required=True)
    p.add_argument("--base", type=float, required=True)
    p.add_argument("--camera-height", type=float, required=True)
    p.add_argument("--object-height", type=float, required=True)
    p.add_argument("--base-b", type=float, default=None,
                   help="the object's base in the SECOND frame")
    p.add_argument("--horizon-b", type=float, default=None)
    p.add_argument("--image-height-b", type=float, default=None)
    C.add_json(p)
    p.set_defaults(fn=cmd_insert)

    p = sub.add_parser("grain", help="Measure a plate's grain, or lay it back on")
    p.add_argument("plate")
    p.add_argument("--frame-of", type=int, default=None,
                   help="treat the plate as a clip and take this frame")
    p.add_argument("--sigma", type=float, default=1.6)
    p.add_argument("--apply-to")
    p.add_argument("--mask")
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--transfer", default="srgb", choices=P.TRANSFERS)
    p.add_argument("--out")
    C.add_json(p)
    p.set_defaults(fn=cmd_grain)

    p = sub.add_parser("warp", help="Composite an artwork onto a tracked region")
    p.add_argument("clip")
    p.add_argument("--track", required=True)
    p.add_argument("--art", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--quad", help="override the reference quad")
    p.add_argument("--matte", help="an extra matte, for anything in front")
    p.add_argument("--art-height", type=int, default=None)
    p.add_argument("--choke", type=float, default=0.0)
    p.add_argument("--soften", type=float, default=0.0)
    p.add_argument("--screen", default="green", choices=tuple(M.SCREENS))
    p.add_argument("--despill", default="limit_max",
                   choices=("none",) + M.DESPILL_FORMS)
    p.add_argument("--despill-strength", type=float, default=0.15)
    p.add_argument("--despill-band", type=int, default=21)
    p.add_argument("--preserve-luma", action="store_true")
    p.add_argument("--light-wrap", type=float, default=0.0)
    p.add_argument("--light-wrap-width", type=float, default=12.0)
    p.add_argument("--crf", type=int, default=16)
    p.add_argument("--transfer", default="srgb", choices=P.TRANSFERS)
    C.add_json(p)
    p.set_defaults(fn=cmd_warp)

    p = sub.add_parser("verify", help="Checks anchored in the PLATE")
    p.add_argument("check", choices=("ring", "content", "channels", "notch",
                                     "rank"))
    p.add_argument("--plate")
    p.add_argument("--comp")
    p.add_argument("--track")
    p.add_argument("--art")
    p.add_argument("--screen", default="green", choices=tuple(M.SCREENS))
    p.add_argument("--region", help="x,y,w,h to look inside")
    p.add_argument("--body", help="x,y,w,h on the object's body, off the screen")
    p.add_argument("--body-mask", help="a matte over the object's body")
    p.add_argument("--body-model", default="affine", choices=T.MODELS)
    p.add_argument("--gauss", type=int, default=3)
    p.add_argument("--min-cc", type=float, default=T.MIN_CC)
    p.add_argument("--frame", type=int, default=0)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--inner", type=int, default=6)
    p.add_argument("--outer", type=int, default=26)
    p.add_argument("--floor", type=float, default=0.004,
                   help="the FILE's own generation floor, never zero")
    p.add_argument("--tolerance", type=float, default=1.0)
    p.add_argument("--patch", type=int, default=3)
    p.add_argument("--sigma", type=float, default=3.0)
    p.add_argument("--alpha-gate", action="store_true")
    p.add_argument("--diff-floor", type=float, default=None,
                   help="how much a pixel must change to count as composited. "
                        "Leave unset and it is MEASURED from the generation "
                        "floor between the two files")
    p.add_argument("--versions", nargs="*", default=[])
    p.add_argument("--references", nargs="*", default=[])
    C.add_json(p)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("holdout", help="Leave one out on the rigid shape")
    p.add_argument("--track", required=True)
    p.add_argument("--detections", help="per frame measured quads, as JSON")
    p.add_argument("--tolerance", type=float, default=1.0)
    C.add_json(p)
    p.set_defaults(fn=cmd_holdout)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
