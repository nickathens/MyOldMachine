#!/usr/bin/env python3
"""Resolution: restore at size, enlarge to the delivery raster, and prove either.

The department that is entered TWICE, which is the whole reason it is written
down. Restoring a soft or compressed source is a stage 3 job, before the cut and
long before colour, because it changes every pixel the grade will then be built
on. Enlarging to a delivery raster is a stage 10 job, at the master, because
enlarging early makes every department downstream pay for pixels the client
never asked for and forces the colour work to be rebuilt when the raster moves.
Doing the second one in the first one's place is the common and expensive error.

Two measurements here exist nowhere else in this toolkit:

  EFFECTIVE RESOLUTION. Whether a file carries the detail its raster claims. A
  whole review set once arrived named 4K and none of it was: it was 720 blown up.
  The frame size cannot say that and the spectrum can.

  TEMPORAL STABILITY. Whether an enlargement boils. The stills upscaler in this
  toolkit is a good tool with no idea that a frame has neighbours, so run down a
  clip it re-invents its detail every picture and the surface crawls. Nothing
  said that out loud before this file, and no still frame will ever show it.

And the pairing that decides whether an upscale was worth running at all:

  knee HIGH and downscale-back PSNR HIGH   detail was added and it is faithful
  knee HIGH and downscale-back PSNR LOW    detail was invented, not recovered
  knee LOW  and downscale-back PSNR HIGH   a resample with extra steps
  knee LOW  and downscale-back PSNR LOW    the run damaged the picture

Commands:
  routes      the routes, what each fixes, what each costs, where each belongs
  effres      does this file carry the resolution it claims
  route       measure a file and say which route, and what it will NOT fix
  temporal    does this enlargement boil
  verify      the whole gate, source against candidate
  superscale  the Resolve route, with the exact API and the free edition refusal

Usage:
  python upres.py routes
  python upres.py effres PLATE.mov --frames 5
  python upres.py route SOURCE.mov --target 3840x2160
  python upres.py temporal SOURCE.mov CANDIDATE.mov --frames 8
  python upres.py verify SOURCE.mov CANDIDATE.mov --frames 8
  python upres.py superscale --scale 2 --sharpness 0.5 --noise 0.2
  (add --json for structured output)

Needs this skill's own environment for everything except `routes` and
`superscale`: ~/.venvs/post/bin/python upres.py ...
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402

# Every route, with the thing about it that decides the job. Dates are when the
# figure was read in the primary source; see reference/09_sources.md.
ROUTES = {
    "none": {
        "name": "Leave it alone",
        "does": "Nothing.",
        "fixes": "Nothing.",
        "cost": "Free.",
        "stage": "n/a",
        "licence": "n/a",
        "when": "The file already carries its raster and the delivery raster "
                "matches it. This is the right answer more often than it is "
                "given, and `effres` is how you find out.",
    },
    "lanczos": {
        "name": "Plain resample (Lanczos)",
        "does": "Enlarges without inventing anything.",
        "fixes": "Nothing. It moves existing detail onto a bigger grid.",
        "cost": "Free, seconds, on machine.",
        "stage": "10, master.",
        "licence": "n/a",
        "when": "The delivery raster is bigger than the source and the source is "
                "clean. It cannot boil, because it has nothing to invent. It is "
                "the CONTROL every other route here is measured against, and on "
                "a clean source the honest gap between it and anything cleverer "
                "is smaller than the argument about it.",
    },
    "hybrid": {
        "name": "Faithful hybrid (Real-ESRGAN on a structure mask)",
        "does": "Enlarges a STILL, keeping the model only where there are edges.",
        "fixes": "Softness on edges and lettering, with the flat areas left as "
                "Lanczos truth so no micro texture is invented.",
        "cost": "Free, on machine, about 4 seconds a megapixel at 2x.",
        "stage": "10, master, and STILLS ONLY.",
        "licence": "BSD-3 (Real-ESRGAN weights).",
        "when": "One frame, a card, a logo, a poster. Measured 39.96 dB "
                "downscale-back against plain ESRGAN's 35.1 and a creative "
                "upscaler's 34.1. Run down a clip it BOILS, because it is a "
                "stills model with no memory. The `upscale` skill owns it; this "
                "department's job is to say when it is the wrong tool.",
    },
    "video_restore": {
        "name": "Video restoration model at the SAME size (SeedVR2)",
        "does": "Rebuilds detail across neighbouring frames, raster unchanged.",
        "fixes": "Compression damage, softness, sensor noise, generation mush. "
                "The faults that are baked into the pixels.",
        "cost": "Free, Apache 2.0, but heavy: the 3B weights are about 8 GB and "
                "the run wants most of the machine.",
        "stage": "3, restoration, BEFORE the cut and before colour.",
        "licence": "Apache 2.0, clean for paid client work.",
        "when": "The source is damaged rather than small. On Apple silicon the "
                "route is the MLX build, not the reference repository, which "
                "asks for an H100 80G for a single 720p pass. Restoring at size "
                "is the mode that fits a 24 GB machine; ENLARGING video is "
                "refused by that tool's own memory guard here and forcing past "
                "it is how a render dies at hour three.",
    },
    "superscale": {
        "name": "DaVinci Resolve Super Scale",
        "does": "Enlarges a clip 2x, 3x or 4x inside Resolve, scriptable.",
        "fixes": "Resolution, with a sharpness and a noise control on 2x.",
        "cost": "Included with a Resolve Studio licence. Not in the free edition.",
        "stage": "10, master.",
        "licence": "Commercial, per seat.",
        "when": "The delivery raster is bigger than the source, the job is "
                "already in a Resolve project, and the whole clip must move "
                "together. It is temporally aware, which is the thing the stills "
                "route is not, and it is the only route here that needs no "
                "Python environment at all.",
    },
    "reimagine": {
        "name": "Generative reimagine",
        "does": "Repaints the picture at a larger size.",
        "fixes": "Flaws that are CONTENT rather than resolution: garbled small "
                "lettering, invented geometry, a hand with six fingers.",
        "cost": "Real credits, per frame. On a clip, per frame times the count, "
                "and it will not match frame to frame.",
        "stage": "3 if the picture is being rebuilt, never at the master.",
        "licence": "Per model.",
        "when": "No upscaler can fix a flaw that is in the content, because it "
                "is not a resolution problem. On a STILL this is a real route "
                "and the user picks it knowing the cost. On a CLIP it is not a "
                "route at all: independent frames do not cut together.",
    },
}

# Measured on synthetic ground truth in selftest, and the reason `verify` reads
# a ratio rather than a level. A candidate at or under this much of the neutral
# enlargement's warping error added no flicker of its own.
STABLE_RATIO = 1.15

# A detail band that moves more than this much further from the control's own
# band is either invented or deleted, and the direction matters: the same
# structure mask both invented texture on a cloud sky at 1.86x and deleted a
# star field at 0.61x, in the same recipe on the same day.
BAND_TOLERANCE = 0.25


def _need_res():
    try:
        import _res  # noqa: F401
    except ImportError as e:
        raise C.ToolMissing(
            "This command measures real pixels, so it needs numpy, OpenCV and "
            "SciPy in this skill's OWN environment:\n"
            "  python3 -m venv ~/.venvs/post\n"
            "  ~/.venvs/post/bin/pip install pillow numpy opencv-python-headless scipy\n"
            f"  ~/.venvs/post/bin/python {os.path.basename(__file__)} ...\n"
            f"({e})")
    import _res
    return _res


def _require_same_clock(a, b, what):
    """Refuse to compare two files whose frame N is not the same moment.

    Frame 8 of a 25 fps file and frame 8 of a 30 fps file are different pictures
    of the world. Every per frame comparison below silently assumes they are the
    same one, so the ambiguity is failed rather than absorbed: an enlargement is
    allowed to change pixels and is never allowed to change the clock.
    """
    if a["rate"] != b["rate"]:
        raise ValueError(
            f"{what} cannot run: the frame rate differs ({a['rate_str']} against "
            f"{b['rate_str']}), so frame N of one is not frame N of the other. "
            "An enlargement must not touch the clock. Fix the rate first, or use "
            "`spec.py check` to see what else moved.")
    if a["frames"] and b["frames"] and a["frames"] != b["frames"]:
        raise ValueError(
            f"{what} cannot run: the frame count differs ({a['frames']} against "
            f"{b['frames']}), so the two files do not describe the same span of "
            "time. An enlargement must not touch the clock.")


def _sample(path, start, count, bits=8):
    """Consecutive frames, one named decode path, for both files alike."""
    import _pix as P
    return [im.rgb for _, im in P.read_frames(path, start=start, count=count,
                                              bits=bits)]


def _still(path, bits=8):
    import _pix as P
    return P.read_image(path).rgb


def _is_still(path):
    return os.path.splitext(str(path))[1].lower() in (
        ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".webp", ".bmp")


# ---------------------------------------------------------------- effres


def effres(path, frames=5, start=None, bits=8):
    """Does this file carry the resolution its raster claims?

    Several frames, never one. A single frame can be a defocus, a whip pan or a
    flat card, and any of those reads SHORT for reasons that have nothing to do
    with the file's resolution. The verdict is taken from the BEST frame
    measured, because resolution is a ceiling: one frame that carries detail to
    Nyquist proves the file can, and no number of soft frames disproves it.
    """
    R = _need_res()
    import _pix as P
    out = {"file": os.path.abspath(str(path)), "frames": []}
    if _is_still(path):
        rgb = _still(path)
        raster = (rgb.shape[1], rgb.shape[0])
        out["raster"] = f"{raster[0]}x{raster[1]}"
        out["decode_path"] = "pillow"
        reads = [R.effective_resolution(rgb, raster)]
        out["frames"] = [dict(index=0, **reads[0])]
    else:
        info = P.clip_info(path)
        raster = (info["width"], info["height"])
        out["raster"] = f"{raster[0]}x{raster[1]}"
        out["rate"] = info["rate_str"]
        out["total_frames"] = info["frames"]
        out["decode_path"] = f"ffmpeg:rawvideo:{'rgb48le' if bits == 16 else 'rgb24'}"
        n = max(1, int(frames))
        if start is None:
            # Spread the samples across the clip rather than taking the head,
            # where a fade from black carries no spectrum at all.
            total = info["frames"] or n
            step = max(1, total // (n + 1))
            idx = [min(total - 1, step * (k + 1)) for k in range(n)]
        else:
            idx = [int(start) + k for k in range(n)]
        reads = []
        for i in sorted(set(idx)):
            try:
                rgb = P.frame_at(path, i, bits=bits).rgb
            except Exception as e:
                out["frames"].append({"index": i, "verdict": "UNREADABLE",
                                      "note": str(e)})
                continue
            r = R.effective_resolution(rgb, raster)
            reads.append(r)
            out["frames"].append(dict(index=i, **r))

    usable = [r for r in reads if r.get("knee") is not None]
    if not usable:
        out["verdict"] = "UNDETERMINED"
        out["note"] = ("No frame sampled had enough ordinary texture to read a "
                       "spectrum from. Try more frames, or a busier span.")
        return out
    best = max(usable, key=lambda r: r["knee"])
    out["knee"] = best["knee"]
    out["verdict"] = best["verdict"]
    out["effective_raster"] = best["effective_raster"]
    out["consistent_with"] = best["consistent_with"]
    out["measured_frames"] = len(usable)
    out["note"] = best["note"] + (
        f" Taken from the best of {len(usable)} readable frames, because "
        "resolution is a ceiling: one frame carrying detail to Nyquist proves "
        "the file can, and soft frames never disprove it.")
    return out


# ---------------------------------------------------------------- route


def route(path, target=None, frames=5, bits=8):
    """Measure the file, then say which route, and what it will not fix."""
    import _pix as P
    e = effres(path, frames=frames, bits=bits)
    still = _is_still(path)
    if still:
        rgb = _still(path)
        sw, sh = rgb.shape[1], rgb.shape[0]
        info = {"width": sw, "height": sh}
    else:
        info = P.clip_info(path)
        sw, sh = info["width"], info["height"]

    out = {"file": e["file"], "kind": "still" if still else "clip",
           "raster": e["raster"], "effective": e.get("effective_raster"),
           "carries": e["verdict"], "knee": e.get("knee"),
           "target": None, "scale": None, "recommend": None,
           "will_not_fix": [], "routes": [], "note": ""}
    if not still:
        out["rate"] = info["rate_str"]
        out["frames"] = info["frames"]

    tw = th = None
    if target:
        tw, th = [int(v) for v in str(target).lower().split("x")]
        out["target"] = f"{tw}x{th}"
        out["scale"] = round(max(tw / sw, th / sh), 4)

    picks = []
    knee = e.get("knee")
    if tw and (tw <= sw and th <= sh):
        picks.append("none")
        out["note"] = ("The target is not bigger than the source, so there is "
                       "nothing to enlarge. A deliverable is never made smaller; "
                       "if a smaller file is wanted it is a viewing copy, "
                       "labelled as one, at the SAME frame size.")
    else:
        if knee is not None and knee < 0.85:
            # The file does not carry its own raster, so enlarging it further
            # enlarges nothing. Restoration is the only thing that can add.
            picks.append("video_restore" if not still else "hybrid")
            if tw:
                picks.append("superscale" if not still else "hybrid")
            out["note"] = (
                f"This file carries detail only to {knee:.2f} of its own "
                "Nyquist, so it is already an enlargement of something smaller "
                "or it is soft. Enlarging it again multiplies the softness. "
                "Restore first, at the SAME size, and measure again before "
                "deciding whether an enlargement is worth anything.")
        elif tw:
            picks.append("superscale" if not still else "hybrid")
            picks.append("lanczos")
            out["note"] = (
                "The source carries its raster, so an enlargement has real "
                "detail to work from. Run the neutral resample as the control "
                "in the same pass: if the clever route cannot beat it on the "
                "measurements in `verify`, it is not earning its risk.")
        else:
            picks.append("none")
            out["note"] = ("The file carries its raster and no target was "
                           "named, so nothing needs doing. Name a target "
                           "raster with --target if the delivery calls for one.")

    if not still and "hybrid" in picks:
        picks = [p for p in picks if p != "hybrid"]
    if not still:
        out["will_not_fix"].append(
            "A stills upscaler run frame by frame down a clip. It has no memory, "
            "so it re-invents its detail every picture and the surface crawls. "
            "`upres.py temporal` is the measurement; no still frame shows it.")
    out["will_not_fix"] += [
        "Anything that is CONTENT rather than resolution: garbled lettering, "
        "wrong geometry, a hand with six fingers. That is a repaint, it costs "
        "credits, and on a clip it is not a route at all because independent "
        "frames do not cut together.",
        "A fault the grade will make worse. Restoration belongs BEFORE colour, "
        "at stage 3, because it changes every pixel the grade is built on.",
    ]
    out["recommend"] = picks[0] if picks else "none"
    out["routes"] = [dict(key=k, **ROUTES[k]) for k in
                     (picks + [k for k in ("lanczos", "reimagine") if k not in picks])]
    return out


# ---------------------------------------------------------------- temporal


def temporal(src, cand, frames=8, start=None, bits=8):
    """Does this enlargement boil?

    The control is the source resampled to the candidate's raster with nothing
    invented. Both are measured with ONE flow field, solved on the control, so
    the only thing that can differ is the candidate's own stability.
    """
    R = _need_res()
    import _pix as P
    a, b = P.clip_info(src), P.clip_info(cand)
    _require_same_clock(a, b, "The temporal measurement")
    n = max(2, int(frames))
    if start is None:
        total = min(a["frames"] or n, b["frames"] or n)
        start = max(0, (total - n) // 2)
    fa = _sample(src, start, n, bits=bits)
    fb = _sample(cand, start, n, bits=bits)
    if len(fa) < 2 or len(fb) < 2:
        raise ValueError("Need at least two frames from each file.")
    k = min(len(fa), len(fb))
    fa, fb = fa[:k], fb[:k]
    out_size = (b["width"], b["height"])
    control = [R.resize(f, out_size) for f in fa]
    res = R.temporal_stability(control, fb)
    res.update({
        "source": os.path.abspath(str(src)),
        "candidate": os.path.abspath(str(cand)),
        "control": f"the source resampled to {out_size[0]}x{out_size[1]} "
                   "(Lanczos), which invents nothing",
        "frames": f"{start} to {start + k - 1}",
        "decode_path": f"ffmpeg:rawvideo:{'rgb48le' if bits == 16 else 'rgb24'}",
        "not_checked": [
            "Whether the detail the candidate added is CORRECT. This measures "
            "only whether it stays put; `verify` measures whether it is true.",
            "Any span outside the frames sampled. A cut or a whip pan elsewhere "
            "in the clip can behave differently.",
        ],
    })
    return res


# ---------------------------------------------------------------- verify


# 6 dB is a factor of four in power. Below it a candidate is inside the
# resampler's own round trip loss; above it the picture moved further from the
# source than enlarging it would have. Calibrated on four materials (two graded
# commercials, a 4K master and a worst case grain plate), reduced and enlarged
# by five kernels each: an honest Lanczos, spline or bicubic enlargement reads
# 0.0 to 2.1 dB, and every fault reads 6.9 or more. The gap is real and the
# limit sits inside it.
DEFICIT_STRIKE_DB = 6.0


def downscale_back(candidate_psnr, control_psnr, same_raster,
                   same_clock=True, rgb_deficit_db=None):
    """The fidelity reading, and what it says when there is nothing to read against.

    The candidate reduced to the source raster should BE the source, and the
    neutral resample's OWN round trip is the ceiling, because a resampler is not
    lossless either and demanding zero where a resample cannot give zero fails a
    correct file.

    BOTH readings are taken on LUMA, not on RGB. A 4:2:0 source carries its
    chroma at half raster, and every real enlarger resamples in YUV while this
    control resamples in RGB, so the two reconstruct that chroma differently.
    That difference alone read 7.07 dB on a real 1080p job, which is more than
    the strike below, and it struck a lossless Lanczos enlargement for inventing
    detail it had never touched. The same file reads 0.76 dB on luma while the
    real faults read 8.6 to 9.8, so the fault is an order of magnitude clear of
    the nuisance instead of buried under it. Chroma is not left unwatched: the
    colour tags and the colour drift check are what watch it, and the RGB
    deficit is carried here as evidence, never as a verdict.

    Two inputs leave nothing to read against, and each says so rather than
    returning a number:

    * The clocks disagree. Frame N of one file is a different moment of the
      world from frame N of the other, so this is not a comparison at all.
    * The candidate is at the source's own raster. There is no round trip to
      make, the control IS the source, every control reading is infinite, and
      there is no ceiling. That is the stage 3 restore case, a first class use
      of this department. Averaging the infinities away instead produced a nan,
      which printed as a number AND disabled the strike in silence, because
      every comparison against a nan is False.

    `candidate_psnr` and `control_psnr` are means over the finite readings, or
    None when nothing finite was left.
    """
    out = {"verdict": "PASS",
           "measured_on": "luma",
           "candidate_psnr_db": (round(candidate_psnr, 2)
                                 if candidate_psnr is not None else None),
           "control_ceiling_db": (round(control_psnr, 2)
                                  if control_psnr is not None else None),
           "deficit_db": None,
           "rgb_deficit_db": rgb_deficit_db,
           "unproven_reason": None,
           "note": "Read the deficit, never the level. A noisy plate round trips "
                   "at 43 dB where a smooth one reaches 57, so the same absolute "
                   "number is near ceiling on one file and poor on another. The "
                   "RGB deficit beside it carries the chroma path as well and is "
                   "evidence, not a verdict."}
    if not same_clock:
        out["verdict"] = "UNPROVEN"
        out["unproven_reason"] = "the clocks disagree"
        out["note"] = (
            "UNPROVEN: the two files disagree about the clock, so frame N of one "
            "is not frame N of the other and this reading compares two different "
            "moments of the world. It cannot bound the enlargement in either "
            "direction. The clock is already a strike above; fix the rate and the "
            "count, then measure the pixels.")
        return out
    if same_raster:
        out["verdict"] = "UNPROVEN"
        out["unproven_reason"] = "no ceiling at the same raster"
        out["note"] = (
            "UNPROVEN: the candidate is at the source's own raster, so the "
            "neutral control IS the source and its round trip is lossless. There "
            "is no ceiling to read the candidate against, and this check cannot "
            "bound a same raster job. The level against the source is still "
            "reported and is worth reading, but the fidelity question belongs to "
            "the department that made the change: a grade to `prove.py`, a repair "
            "to its own before and after.")
        return out
    if candidate_psnr is None:
        out["verdict"] = "PASS"
        out["note"] = ("The candidate reduces back to the source EXACTLY, which "
                       "is better than the neutral resample's own round trip. "
                       "Necessary and not sufficient: a nearest neighbour "
                       "enlargement also reduces back exactly and is unusable, so "
                       "read the detail bands before calling this clean. "
                       + out["note"])
        return out
    if control_psnr is None:
        out["verdict"] = "UNPROVEN"
        out["unproven_reason"] = "the control round tripped losslessly"
        out["note"] = ("UNPROVEN: the neutral control round tripped losslessly at "
                       "a raster where it should not have, so there is no ceiling "
                       "to read against.")
        return out
    out["deficit_db"] = round(control_psnr - candidate_psnr, 2)
    if out["deficit_db"] > DEFICIT_STRIKE_DB:
        out["verdict"] = "STRIKE"
    return out


def _downscale_line(d):
    """One printed line for that check, with no nan in it, ever."""
    lvl, ceil = d.get("candidate_psnr_db"), d.get("control_ceiling_db")
    if d.get("verdict") == "UNPROVEN":
        where = f"{lvl} dB against the source" if lvl is not None else "no reading"
        return f"{where}, UNPROVEN: {d.get('unproven_reason')}"
    if lvl is None:
        return f"exact against the source, against a ceiling of {ceil} dB (luma)"
    return f"{lvl} dB against a ceiling of {ceil} dB (luma)"


def verify(src, cand, frames=8, start=None, bits=8):
    """The whole gate: what changed, whether it was faithful, whether it holds."""
    R = _need_res()
    import _pix as P
    import numpy as np

    a, b = P.clip_info(src), P.clip_info(cand)
    strikes, notes = [], []
    out = {"source": a["path"], "candidate": b["path"],
           "source_raster": f"{a['width']}x{a['height']}",
           "candidate_raster": f"{b['width']}x{b['height']}",
           "decode_path": f"ffmpeg:rawvideo:{'rgb48le' if bits == 16 else 'rgb24'}",
           "checks": {}}

    # 1. The raster. A deliverable is never made smaller, in either axis.
    sx, sy = b["width"] / a["width"], b["height"] / a["height"]
    out["scale"] = {"x": round(sx, 5), "y": round(sy, 5)}
    if b["width"] < a["width"] or b["height"] < a["height"]:
        strikes.append(f"The candidate is SMALLER than the source "
                       f"({b['width']}x{b['height']} against {a['width']}x{a['height']}). "
                       "A deliverable is never downscaled. If this is a viewing "
                       "copy it must be labelled as one and it must keep the "
                       "source's frame size.")
    if abs(sx - sy) > 0.002:
        strikes.append(f"The two axes were scaled differently ({sx:.4f} against "
                       f"{sy:.4f}), so the picture's shape changed. Circles in "
                       "the source are the test.")
    out["checks"]["raster"] = "STRIKE" if any("SMALLER" in s or "axes" in s
                                              for s in strikes) else "PASS"

    # 2. Time. An enlargement changes pixels, never the clock.
    time_faults = []
    if a["rate"] != b["rate"]:
        time_faults.append(f"frame rate moved from {a['rate_str']} to {b['rate_str']}")
    if a["frames"] and b["frames"] and a["frames"] != b["frames"]:
        time_faults.append(f"frame count moved from {a['frames']} to {b['frames']}")
    out["checks"]["time"] = "STRIKE" if time_faults else "PASS"
    for f in time_faults:
        strikes.append("An enlargement must not touch the clock: " + f + ".")
    comparable = not time_faults

    # 3. Depth. Declared, and then measured, because a promoted file lies.
    out["checks"]["declared_bits"] = {"source": a["declared_bits"],
                                      "candidate": b["declared_bits"]}
    if (a["declared_bits"] or 0) > (b["declared_bits"] or 0):
        strikes.append(f"Bit depth fell from {a['declared_bits']} to "
                       f"{b['declared_bits']}. Native depth always.")

    # 4. Colour tags. A hash cannot see these and a client's player can.
    tag_faults = [k for k in ("primaries", "transfer", "matrix", "range")
                  if a["colour"].get(k) != b["colour"].get(k)]
    out["checks"]["colour_tags"] = {"source": a["colour"], "candidate": b["colour"],
                                    "changed": tag_faults}
    for k in tag_faults:
        strikes.append(f"Colour tag '{k}' changed from {a['colour'].get(k)} to "
                       f"{b['colour'].get(k)}. The pixels can be right and the "
                       "file still play wrong.")

    # 5. Audio carried, or knowingly not.
    def _has_audio(path):
        d = C.ffprobe_json(path, ["-select_streams", "a", "-show_streams"])
        return len(d.get("streams") or [])
    na, nb = _has_audio(src), _has_audio(cand)
    out["checks"]["audio_streams"] = {"source": na, "candidate": nb}
    if na and not nb:
        strikes.append(f"The source has {na} audio stream(s) and the candidate "
                       "has none. Most upscalers drop sound silently.")

    # ---- pixels
    n = max(2, int(frames))
    if start is None:
        total = min(a["frames"] or n, b["frames"] or n)
        start = max(0, (total - n) // 2)
    fa = _sample(src, start, n, bits=bits)
    fb = _sample(cand, start, n, bits=bits)
    k = min(len(fa), len(fb))
    if k < 1:
        raise ValueError("No frames could be read from one of the files.")
    fa, fb = fa[:k], fb[:k]
    out["frames_measured"] = f"{start} to {start + k - 1}"
    if not comparable:
        notes.append(
            "The clock differs between the two files, so every pixel measurement "
            "below is comparing frame N of one against a DIFFERENT moment in the "
            "other. Read them as evidence that something is wrong, never as a "
            "measurement of the enlargement. Nothing below can raise a strike of "
            "its own while that is true: a wrong clock would otherwise be "
            "reported as invented texture or a colour drift, which is a true "
            "verdict with a false cause, and the cause is what gets acted on.")

    def _fault(msg):
        """A pixel fault is a strike, or a note when the clock makes it unreadable."""
        (strikes if comparable else notes).append(
            msg if comparable else "UNPROVEN, the clocks disagree: " + msg)
    out_size = (b["width"], b["height"])
    src_size = (a["width"], a["height"])
    control = [R.resize(f, out_size) for f in fa]

    # 6. Downscale back. The candidate reduced to the source raster should BE
    # the source. The neutral resample's own round trip is the ceiling, because
    # the resampler is not lossless either and demanding zero where a resample
    # cannot give zero fails a correct file.
    red_cand = [R.resize(c, src_size) for c in fb]
    red_ctrl = [R.resize(c, src_size) for c in control]

    def _mean_finite(vals):
        fin = [v for v in vals if np.isfinite(v)]
        return float(np.mean(fin)) if fin else None

    y_cand = _mean_finite([R.psnr(R.gray(r), R.gray(s))
                           for r, s in zip(red_cand, fa)])
    y_ctrl = _mean_finite([R.psnr(R.gray(r), R.gray(s))
                           for r, s in zip(red_ctrl, fa)])
    rgb_cand = _mean_finite([R.psnr(r, s) for r, s in zip(red_cand, fa)])
    rgb_ctrl = _mean_finite([R.psnr(r, s) for r, s in zip(red_ctrl, fa)])
    rgb_def = (round(rgb_ctrl - rgb_cand, 2)
               if (rgb_cand is not None and rgb_ctrl is not None) else None)
    dsb = downscale_back(y_cand, y_ctrl, out_size == src_size,
                         same_clock=comparable, rgb_deficit_db=rgb_def)
    out["checks"]["downscale_back"] = dsb
    if dsb["verdict"] == "STRIKE":
        strikes.append(f"The candidate is {dsb['deficit_db']:.1f} dB below the "
                       "neutral resample's own round trip on luma, so it moved "
                       "the picture further from the source than simply enlarging "
                       "it would have. The detail bands say which way: above the "
                       "control is invented texture, below it is detail removed, "
                       "and bands that read ok with a deficit this size mean the "
                       "enlargement itself used a poor kernel (a bilinear one "
                       "reads 6.9 to 9.4 dB here).")
    elif dsb["verdict"] == "UNPROVEN":
        notes.append("DOWNSCALE BACK: " + dsb["note"])

    # 7. Detail by luminance band, both directions. The sign flips with the
    # material, so a single global figure hides half the faults.
    bc = R.band_detail(control[0])
    bk = R.band_detail(fb[0])
    bands = []
    for u, v in zip(bc, bk):
        if not u["fine_std"] or not v["fine_std"]:
            continue
        ratio = v["fine_std"] / u["fine_std"]
        verdict = "ok"
        if ratio > 1 + BAND_TOLERANCE:
            verdict = "invented"
        elif ratio < 1 - BAND_TOLERANCE:
            verdict = "deleted"
        bands.append({"band": u["band"], "luma_range": u["luma_range"],
                      "ratio": round(float(ratio), 3), "verdict": verdict})
    out["checks"]["detail_bands"] = bands
    for bnd in bands:
        if bnd["verdict"] == "invented":
            _fault(f"Luminance band {bnd['band']} carries "
                   f"{bnd['ratio']:.2f} times the neutral resample's "
                   "fine detail. That is invented micro texture, and on "
                   "a flat surface it reads as worms.")
        elif bnd["verdict"] == "deleted":
            _fault(f"Luminance band {bnd['band']} carries only "
                   f"{bnd['ratio']:.2f} of the neutral resample's fine "
                   "detail. The model read that band's real texture as "
                   "noise and removed it. A star field is the case that "
                   "made this check exist.")

    # 8. Colour drift. Against the control, which by construction has none.
    drift = [round(float(np.mean([f[..., c] for f in fb]) -
                         np.mean([f[..., c] for f in control])) * 255.0, 3)
             for c in range(3)]
    out["checks"]["colour_drift_levels"] = {"r": drift[0], "g": drift[1],
                                            "b": drift[2]}
    if max(abs(d) for d in drift) > 1.0:
        _fault(f"The picture drifted {drift} code levels against the "
               "neutral resample. Plain ESRGAN drifts about two levels "
               "cool on a clean render; that is this fault.")

    # 9. Did the enlargement put anything ABOVE the source's own Nyquist?
    ea = R.effective_resolution(fa[0], src_size)
    eb = R.effective_resolution(fb[0], out_size)
    out["checks"]["effective_resolution"] = {
        "source": {"knee": ea["knee"], "verdict": ea["verdict"]},
        "candidate": {"knee": eb["knee"], "verdict": eb["verdict"],
                      "effective_raster": eb["effective_raster"]}}
    if sx > 1.05 and eb["knee"] is not None and eb["knee"] <= (1.0 / sx) * 1.25:
        notes.append(
            f"The candidate's detail still stops at {eb['knee']:.2f} of its new "
            f"Nyquist, which is where the SOURCE's detail stopped. The "
            f"enlargement moved the picture onto a bigger grid and added "
            "nothing above it. That is a resample with extra steps, and it may "
            "be exactly what was wanted, but it is not a restoration.")

    # 10. Time. The check nothing here could make before.
    if not comparable:
        out["checks"]["temporal"] = {
            "verdict": "UNDETERMINED",
            "note": "Refused: the two files disagree about the clock, so frame N "
                    "of one is not frame N of the other and every per frame "
                    "number above is comparing different moments. Fix the rate "
                    "and the count first."}
    elif k >= 2:
        t = R.temporal_stability(control, fb)
        t.pop("per_pair", None)
        out["checks"]["temporal"] = t
        if t.get("verdict") == "BOILS":
            strikes.append("TEMPORAL: " + t["note"])
        elif t.get("verdict") == "MARGINAL":
            notes.append("TEMPORAL: " + t["note"])
    else:
        out["checks"]["temporal"] = {"verdict": "UNDETERMINED",
                                     "note": "Needs at least two frames."}

    out["strikes"] = strikes
    out["notes"] = notes
    out["verdict"] = "STRIKES" if strikes else ("NOTES" if notes else "PASS")
    out["not_checked"] = [
        f"Every frame outside {out['frames_measured']}. A cut, a whip pan or a "
        "dissolve elsewhere in the clip can behave differently, and a "
        "restoration model's worst frame is usually its hardest one.",
        "Whether the added detail matches what was really there. Nothing can "
        "check that without the higher resolution original; the downscale back "
        "bounds it and cannot settle it.",
        "The audio itself. Stream presence is counted here; loudness and layout "
        "are `audio.py`.",
        f"The picture above {bits} bits. These are ratio measurements and 8 bits "
        "carries them; a delivery depth check is `spec.py depth`.",
    ]
    return out


# ---------------------------------------------------------------- resolve


def superscale(scale=2, sharpness=None, noise=None):
    """The Resolve route, with the enumerations read out of the shipped manual.

    Read on 2026-08-23 in the Developer/Scripting README that ships inside the
    installed application, not from a web page about it.
    """
    proj = ("0=Auto, 1=no scaling, 2, 3 and 4 are the 2x, 3x and 4x multipliers"
            " (the PROJECT setting alone has the Auto value)")
    clip = ("1=no scaling, 2, 3 and 4 are the 2x, 3x and 4x multipliers"
            " (the CLIP property has no Auto)")
    enhanced = (sharpness is not None or noise is not None)
    s = 0.5 if sharpness is None else float(sharpness)
    nz = 0.0 if noise is None else float(noise)
    for v, nm in ((s, "sharpness"), (nz, "noise reduction")):
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{nm} must be between 0.0 and 1.0, got {v}")
    if enhanced and int(scale) != 2:
        raise ValueError("2x Enhanced is a 2x mode only. Drop --sharpness and "
                         "--noise, or set --scale 2.")
    call = (f"clip.SetClipProperty('Super Scale', 2, {s}, {nz})" if enhanced
            else f"clip.SetClipProperty('Super Scale', {int(scale)})")
    return {
        "edition": "DaVinci Resolve Studio only. On the free edition an external "
                   "process cannot reach the API at all, and a Studio-only call "
                   "made from free returns false rather than raising, so ALWAYS "
                   "check the return value.",
        "project_setting": {"key": "superScale", "values": proj,
                            "call": "project.SetSetting('superScale', 2)"},
        "clip_property": {"key": "Super Scale", "values": clip, "call": call},
        "enhanced": {
            "how": "Exactly four arguments, or it silently falls back to plain "
                   "2x: SetClipProperty('Super Scale', 2, sharpness, noise).",
            "ranges": "sharpness 0.0 to 1.0, noise reduction 0.0 to 1.0",
            "chosen": {"sharpness": s, "noise_reduction": nz} if enhanced else None},
        "order": [
            "Set the property on the MEDIA POOL ITEM, before the clip is cut in. "
            "Super Scale is a decode-side setting, so it feeds everything "
            "downstream of it including the grade.",
            "Check the return value of every Set call. A false here is the only "
            "sign you are on the free edition.",
            "Render, then bring the result back to `upres.py verify`. Resolve "
            "will not tell you whether the result boils or whether it invented "
            "detail; that measurement is this skill's job.",
        ],
        "source": "Developer/Scripting/README.txt inside the installed "
                  "application, read 2026-08-23 against Resolve 21.0.4.",
        "driving_it": "The davinci-resolve skill owns the connection "
                      "(scripts/resolve_api.py status | launch | render). This "
                      "command states the property, not the plumbing.",
    }


# ---------------------------------------------------------------- cli


def main(argv=None):
    p = C.parser_for(__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    subparsers = []
    subparsers.append(sub.add_parser("routes",
                                     help="the routes and where each belongs"))

    q = sub.add_parser("effres", help="does the file carry its raster")
    subparsers.append(q)
    q.add_argument("file")
    q.add_argument("--frames", type=int, default=5)
    q.add_argument("--start", type=int, default=None)
    q.add_argument("--bits", type=int, default=8, choices=(8, 16))

    q = sub.add_parser("route", help="measure and recommend a route")
    subparsers.append(q)
    q.add_argument("file")
    q.add_argument("--target", help="delivery raster, e.g. 3840x2160")
    q.add_argument("--frames", type=int, default=5)
    q.add_argument("--bits", type=int, default=8, choices=(8, 16))

    for name, helptext in (("temporal", "does this enlargement boil"),
                           ("verify", "the whole gate")):
        q = sub.add_parser(name, help=helptext)
        subparsers.append(q)
        q.add_argument("source")
        q.add_argument("candidate")
        q.add_argument("--frames", type=int, default=8)
        q.add_argument("--start", type=int, default=None)
        q.add_argument("--bits", type=int, default=8, choices=(8, 16))

    q = sub.add_parser("superscale", help="the Resolve route")
    subparsers.append(q)
    q.add_argument("--scale", type=int, default=2, choices=(1, 2, 3, 4))
    q.add_argument("--sharpness", type=float, default=None)
    q.add_argument("--noise", type=float, default=None)

    for q in subparsers:
        C.add_json(q)
    args = p.parse_args(argv)

    if args.cmd == "routes":
        res = {"stages": {"3": "restore at size, before the cut and before colour",
                          "10": "enlarge to the delivery raster, at the master"},
               "routes": [dict(key=k, **v) for k, v in ROUTES.items()]}
        return C.emit(res, args.json, lambda r: (
            print("  Two homes in the spine, and putting the second one in the "
                  "first one's place is the expensive error:"),
            [print(f"    stage {k}: {v}") for k, v in r["stages"].items()],
            print(),
            [(print(f"  {x['name']}  [{x['key']}]"),
              print(f"    fixes   {x['fixes']}"),
              print(f"    cost    {x['cost']}"),
              print(f"    stage   {x['stage']}"),
              print(f"    licence {x['licence']}"),
              print(f"    when    {x['when']}"), print())
             for x in r["routes"]]))

    if args.cmd == "effres":
        res = effres(args.file, frames=args.frames, start=args.start,
                     bits=args.bits)
        return C.emit(res, args.json, lambda r: (
            print(f"  {os.path.basename(r['file'])}  {r['raster']}"),
            print(f"  {r['verdict']}" + (f"  knee {r['knee']} of Nyquist"
                                         if r.get('knee') else "")),
            print(f"  effective raster {r['effective_raster']}")
            if r.get("effective_raster") else None,
            print(f"  consistent with {r['consistent_with']}")
            if r.get("consistent_with") else None,
            print(f"\n  {r['note']}")))

    if args.cmd == "route":
        res = route(args.file, target=args.target, frames=args.frames,
                    bits=args.bits)
        return C.emit(res, args.json, lambda r: (
            print(f"  {os.path.basename(r['file'])}  {r['raster']}"
                  + (f" -> {r['target']} ({r['scale']}x)" if r['target'] else "")),
            print(f"  carries: {r['carries']}"
                  + (f", effective {r['effective']}" if r['effective'] else "")),
            print(f"\n  RECOMMEND: {ROUTES[r['recommend']]['name']}"),
            print(f"  {r['note']}\n"),
            print("  It will NOT fix:"),
            [print(f"    - {w}") for w in r["will_not_fix"]]))

    if args.cmd == "temporal":
        res = temporal(args.source, args.candidate, frames=args.frames,
                       start=args.start, bits=args.bits)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r.get('verdict')}"),
            print(f"  warping error {r.get('candidate_mae')} against the "
                  f"control's {r.get('control_mae')}, ratio {r.get('ratio')}"),
            print(f"  detail incoherence {r.get('candidate_incoherence')} "
                  f"against {r.get('control_incoherence')} "
                  f"(fully independent would be {r.get('independent_at')})"),
            print(f"\n  {r.get('note','')}"),
            print("\n  Not checked:"),
            [print(f"    - {w}") for w in r["not_checked"]]))

    if args.cmd == "verify":
        res = verify(args.source, args.candidate, frames=args.frames,
                     start=args.start, bits=args.bits)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['verdict']}   {r['source_raster']} -> "
                  f"{r['candidate_raster']}  frames {r['frames_measured']}"),
            print(f"  downscale back "
                  f"{_downscale_line(r['checks']['downscale_back'])}"),
            print(f"  temporal {r['checks']['temporal'].get('verdict')} "
                  f"(ratio {r['checks']['temporal'].get('ratio')})"),
            print("\n  STRIKES:") if r["strikes"] else None,
            [print(f"    - {s}") for s in r["strikes"]],
            print("\n  Notes:") if r["notes"] else None,
            [print(f"    - {s}") for s in r["notes"]],
            print("\n  Not checked:"),
            [print(f"    - {s}") for s in r["not_checked"]]))

    if args.cmd == "superscale":
        res = superscale(args.scale, args.sharpness, args.noise)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['edition']}\n"),
            print(f"  project: {r['project_setting']['call']}"),
            print(f"           {r['project_setting']['values']}"),
            print(f"  clip:    {r['clip_property']['call']}"),
            print(f"           {r['clip_property']['values']}"),
            print(f"\n  2x Enhanced: {r['enhanced']['how']}"),
            print(f"  {r['enhanced']['ranges']}\n"),
            [print(f"  - {o}") for o in r["order"]],
            print(f"\n  source: {r['source']}")))
    return 0


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
