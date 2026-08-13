#!/usr/bin/env python3
"""Self test for frame repair. Run it after touching cgflow.py or cgyuv.py.

    python scripts/selftest_frames.py

Every check here is a measurement with a stated tolerance, and every one of them
exists because the thing it measures went wrong on a real delivery. The two that
matter most:

  a frame synthesised at the very start of an interval must come back byte for
  byte identical to the frame it was built from, which is only true if the
  picture never leaves its own colour space

  the round trip that was used instead must be shown to shift the picture, or
  the check above is guarding nothing
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import cgyuv as Y

_fail = 0
_ran = 0
_skip = 0


def check(name, ok, detail=""):
    global _fail, _ran
    _ran += 1
    print(("PASS  " if ok else "FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        _fail += 1


def skip(name, why):
    global _skip
    _skip += 1
    print(f"SKIP  {name}   {why}")


def synthetic_clip(path, w=320, h=180, n=24):
    """A short clip with real motion and real detail, tagged bt709 limited."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=size={w}x{h}:rate=24:duration={n / 24}",
         "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-color_range", "tv", path], check=True)
    return path


def conform_18_to_24(path, w=320, h=180, n=72, plate_amp=60, plate_shift=3,
                     ov=26, ov_step=14, blur=6):
    """Live action at 18 fps repeated up to 24, with a 24 fps graphic over it.

    Three unique plates per four slots, which is what 24 from 18 produces, and a
    small bright block that keeps moving every single frame the way an animated
    overlay does.

    The proportions are not free. They are set so the clip lands in the same
    regime as the film this was found on, where the mean step on a repeated
    frame reads 0.26 of the shot's typical value and the area reads 0.089. Here
    they read about 0.20 and 0.03: the mean stays clear of the 0.10 the census
    needs, so the mean is genuinely blind, and the area is clear the other side,
    so it genuinely catches it. A first attempt used sharp noise for the plate
    and a tiny overlay, which put the mean ratio near zero, and the mean caught
    every repeat: the clip has to model the FAULT, not merely contain repeats.
    """
    rng = np.random.default_rng(4)
    base = rng.normal(0, 1, (h, w))
    k = np.ones(blur) / blur
    for ax in (0, 1):
        base = np.apply_along_axis(lambda m: np.convolve(m, k, "same"), ax, base)
    base = (base - base.min()) / (base.max() - base.min())
    plate = (60 + plate_amp * base).astype(np.float32)
    frames = []
    for i in range(n):
        uniq = (i // 4) * 3 + max(0, i % 4 - 1)      # slots 0 and 1 share a plate
        f = np.roll(plate, uniq * plate_shift, axis=1).copy()
        x = 8 + (i * ov_step) % (w - ov - 16)        # the overlay moves every frame
        f[30:48, x:x + ov] = 248
        frames.append(np.clip(f, 0, 255).astype(np.uint8))
    raw = b"".join(f.tobytes() + bytes([128] * (w * h // 2)) for f in frames)
    assert len(raw) == n * w * h * 3 // 2
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
         "-s", f"{w}x{h}", "-r", "24", "-i", "-",
         "-vf", "setparams=colorspace=bt709:color_primaries=bt709:"
                "color_trc=bt709:range=tv",
         "-c:v", "libx264", "-crf", "8", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         path], input=raw, check=True)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="cgframes_selftest_")
    clip = synthetic_clip(f"{tmp}/clip.mp4")
    spec = Y.Spec(320, 180)
    frames = {i: b for i, b in Y.decode(clip, spec, 0, 8)}
    if len(frames) < 4:
        sys.exit("could not decode the test clip")
    a, b = frames[2], frames[3]

    # ---- plane handling -------------------------------------------------
    y, u, v = Y.planes(a, spec)
    check("the three planes are the right shape and size",
          y.shape == (180, 320) and u.shape == (90, 160) and v.shape == (90, 160)
          and len(a) == spec.frame_bytes,
          f"{y.shape} {u.shape} {v.shape}, {len(a)} bytes")

    rebuilt = y.tobytes() + u.tobytes() + v.tobytes()
    check("splitting and rejoining a frame changes nothing", rebuilt == a)

    # ---- the invariant that stops the flicker ---------------------------
    try:
        import torch  # noqa: F401
        import cgflow
        have_flow = True
    except ImportError as e:
        have_flow = False
        skip("a frame synthesised at t=0 is byte identical to the frame it came from",
             f"{e}")
        skip("a frame synthesised at t=1 is byte identical to its other source", "")

    if have_flow:
        import torch
        zero = torch.zeros(1, 2, spec.height, spec.width, device=cgflow.device())
        f01 = f10 = zero
        # Pinned to the raft path on purpose. With a flow known to be zero this
        # is a control on the COLOUR path and nothing else: any difference at
        # all is the picture having left its own space and come back. rife
        # derives its own flow, so it cannot be held to a zero flow control and
        # is proved separately below.
        s0 = Y.synth(a, b, 0.0, f01, f10, spec, how="raft")
        check("a frame synthesised at t=0 is byte identical to the frame it came from",
              s0 == a, "zero flow, so nothing may move and nothing may shift")
        s1 = Y.synth(a, b, 1.0, f01, f10, spec, how="raft")
        check("a frame synthesised at t=1 is byte identical to its other source", s1 == b)

        ft0, ft1 = cgflow.split(torch.ones(1, 2, 4, 4), torch.ones(1, 2, 4, 4), 0.0)
        check("the flow split leans entirely on the near frame at t=0",
              float(ft0.abs().max()) < 1e-6, f"max {float(ft0.abs().max()):.2e}")

    # ---- the same invariant for the preferred engine --------------------
    # rife cannot be given a zero flow, so the equivalent control is a pair of
    # IDENTICAL frames: there is nothing to move towards, so whatever flow and
    # fusion mask the network returns, the answer must still be that picture.
    # This is the check that would catch rife being wired up through an RGB
    # round trip, which is the failure the whole module exists to prevent.
    try:
        how = Y.engine()
    except Exception as exc:
        how = "raft"
        skip("the preferred engine reports itself", str(exc))
    if how == "rife":
        s = Y.synth(a, a, 0.5, spec=spec, how="rife")
        check("rife returns the right number of bytes", len(s) == spec.frame_bytes,
              f"{len(s)} of {spec.frame_bytes}")
        d = np.abs(np.frombuffer(s, np.uint8).astype(np.int16)
                   - np.frombuffer(a, np.uint8).astype(np.int16))
        # Not bit exact, and it does not need to be: a network is not arithmetic.
        # What must not happen is a WHOLE FRAME BIAS, because that is what reads
        # as a flicker when rebuilt and untouched frames alternate. The RGB round
        # trip this replaces shifts luma by 0.84 levels on real footage, so the
        # bar is an order of magnitude under that. A single stray pixel is not
        # the failure mode and is not tested as one.
        # This is also the check that catches scale being set back to 0.5, which
        # reads 0.883 levels here, i.e. as bad as the round trip. See cgrife.
        check("rife rebuilding between two identical frames returns that frame",
              d.mean() < 0.25 and d.max() <= 16,
              f"worst {int(d.max())} levels, mean {d.mean():.4f}, bar is 0.25 mean")
        ys = spec.ysize
        cshift = abs(float(np.frombuffer(s, np.uint8)[ys:].astype(np.float32).mean()
                           - np.frombuffer(a, np.uint8)[ys:].astype(np.float32).mean()))
        check("rife introduces no chroma bias, which is what reads as a flicker",
              cshift < 0.05, f"{cshift:.4f} levels across both chroma planes")
    else:
        skip("rife rebuilding between two identical frames returns that frame",
             "rife not set up, the raft fallback is in use")

    # ---- and the round trip it replaced ---------------------------------
    try:
        import cv2
        cap = cv2.VideoCapture(clip)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 2)
        ok, bgr = cap.read()
        cap.release()
        if not ok:
            raise RuntimeError("no frame")
        back = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
        shift = np.abs(back[:180].astype(np.float32) - y.astype(np.float32)).mean()
        check("the RGB round trip that caused the flicker really does shift the picture",
              shift > 0.2,
              f"{shift:.3f} code levels of luma on a round trip that should cost nothing")
    except Exception as e:                                  # noqa: BLE001
        skip("the RGB round trip that caused the flicker really does shift the picture",
             str(e))

    # ---- the ruler ------------------------------------------------------
    ruler = Y.Ruler(spec, view_w=320)
    tagged = ruler.lapvar(ruler.small(a))
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", "320x180", "-i", "-", "-vf", "scale=320:180", "-pix_fmt", "gray",
         "-f", "rawvideo", "-"], input=y.tobytes(), capture_output=True)
    import cv2
    bare = float(cv2.Laplacian(
        np.frombuffer(p.stdout[:320 * 180], np.uint8).reshape(180, 320), cv2.CV_32F).var())
    check("a bare luma plane and a tagged frame do not measure the same",
          abs(tagged / max(bare, 1e-9) - 1) > 0.05,
          f"tagged reads {tagged / max(bare, 1e-9):.3f} times the bare plane, which is why "
          f"a frame is always handed over whole")

    # ---- a deliberate hold is not a broken cadence ----------------------
    # A solid run of frozen frames has every gap equal to 1, so it reads as
    # PERFECTLY regular and scores better on the cadence test than a real
    # retime does. Measured on real footage before the run cap existed: one 20
    # frame hold arrived at plan as density 0.26, gap spread 0.00, was called a
    # retimed shot, and planned to move 48 real frames that nobody had touched.
    import cgframes as CF
    faults, held = CF._drop_holds(list(range(24, 44)))
    check("a solid 20 frame run is read as a hold and not as a fault",
          faults == [] and len(held) == 20, f"{len(faults)} faults, {len(held)} held")
    # and the cadence signature it would have produced, had it not been dropped
    g = np.diff(np.array(list(range(24, 44)), float))
    check("that run really does look perfectly regular, which is why it fooled the test",
          float(g.std() / g.mean()) == 0.0, f"gap spread {float(g.std() / g.mean()):.2f}")
    # a real rate conversion repeats one frame at a time, so it must survive
    conv = sorted(list(range(10, 60, 3)) + list(range(11, 60, 6)))
    f2, h2 = CF._drop_holds(conv)
    check("a real rate conversion is still caught after the run cap",
          h2 == [] and len(f2) == len(conv), f"{len(f2)} faults, {len(h2)} held")

    # ---- split screen ---------------------------------------------------
    merged = Y.paste_panel(a, b, 0, 160, spec)
    my, mu, mv = Y.planes(merged, spec)
    by = Y.planes(b, spec)[0]
    check("pasting one panel leaves the other side untouched",
          np.array_equal(my[:, 160:], y[:, 160:]) and np.array_equal(my[:, :160], by[:, :160]),
          "luma and both chroma planes cut on the same pixel")

    # ---- cadence --------------------------------------------------------
    import cgframes
    st = {"input": clip, "width": 320, "height": 180, "fps": 24.0, "nb_frames": 24,
          "shots": [[0, 24]]}
    reps = list(range(2, 24, 3))
    jobs = cgframes.rebuild_jobs(st, 0, 23, reps, _Args())
    slots = [j[0] for j in jobs]
    synth_n = sum(1 for j in jobs if j[4] == "rebuild")
    check("a retime writes every slot it moves, not only the new frames",
          len(slots) == len(set(slots)) and len(jobs) > synth_n,
          f"{len(jobs)} slots written, {synth_n} of them synthesised")
    check("a retime never synthesises the first or last frame of the shot",
          0 not in [j[0] for j in jobs if j[4] == "rebuild"]
          and 23 not in [j[0] for j in jobs if j[4] == "rebuild"],
          "so no cut point can soften")

    # ---- a frozen plate under a moving graphic --------------------------
    # Live action generated at 18 fps and conformed to 24 by repeating one
    # frame in four, with a bright overlay running over the top at the full
    # rate. This is the shape the mean step cannot see, so build it and require
    # BOTH answers: the mean must miss it, and the area must catch it. Only the
    # pair proves anything. If the mean ever starts catching it, this clip has
    # stopped modelling the fault and the check has to be rebuilt, not relaxed.
    overlaid = os.path.join(tmp, "overlaid.mp4")
    conform_18_to_24(overlaid, n=72)
    ruler = Y.Ruler(Y.Spec(320, 180, "tv", "bt709"), view_w=320)
    _, d18, _, a18 = ruler.scan(overlaid, verbose=False)
    st18 = {"width": 320, "height": 180, "nb_frames": len(d18), "shots": [[0, len(d18)]]}
    dup = [f for f in range(1, len(d18)) if f % 4 == 1]      # where the plate repeats
    by_mean = cgframes.census_frozen(st18, d18)
    both = cgframes.census_frozen(st18, d18, a18)
    found_mean = set(sum((c["repeats"] for c in by_mean.values()), []))
    found_both = set(sum((c["repeats"] for c in both.values()), []))
    check("the mean step is blind to a frozen plate under a moving graphic",
          len(found_mean & set(dup)) <= len(dup) // 4,
          f"mean found {len(found_mean & set(dup))} of {len(dup)} repeated frames")
    check("the area that moved finds them",
          len(found_both & set(dup)) >= 3 * len(dup) // 4,
          f"area found {len(found_both & set(dup))} of {len(dup)}")
    check("and it invents nothing that was not repeated",
          not (found_both - set(dup)),
          f"{len(found_both - set(dup))} frames flagged that do move")

    print()
    if _skip:
        print(f"{_ran} checks, {_fail} failures, {_skip} skipped")
    else:
        print(f"{_ran} checks, {_fail} failures")
    return 1 if _fail else 0


class _Args:
    force_mode = None


if __name__ == "__main__":
    sys.exit(main())
