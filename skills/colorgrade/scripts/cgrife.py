#!/usr/bin/env python3
"""RIFE 4.25 as a plain function, applied to yuv planes rather than to RGB.

Why this and not optical flow with a warp-and-blend. Warping both neighbours and
averaging them is what makes a rebuilt frame soft: two warps that disagree by a
pixel average into a two tap blur. On real footage that landed every twelve
frames, measured 10 to 25 per cent softer than its neighbours, and read to the
viewer as the subject twitching sideways and back for one frame. No measurement
of motion can see it, because nothing moved.

Measured against the warp-and-blend it replaced, on frames hidden from both:

    error 2.078 against 2.306, detail held 99.9 per cent against 95.2,
    edge placement 0.7883 against 0.7665, and about five times faster.

The picture never goes to RGB and back. RGB is fed to the network so it can
think; what comes back is a vector field and a fusion weight, and those are
applied to the file's own y, u and v. In fastmode 4.25 has no refinement net, so
warping the planes with its own flow reproduces exactly what it would have output.

Three things about the model that cost time to find:
  * `IFNet` returns `(flow_list, mask_list[4], merged)`, so the mask is a TENSOR
    and not a list, and it needs a sigmoid.
  * the useful flow is `flow_list[4]`, at full resolution.
  * SCALE 0.5, WHICH IS THE USUAL RECOMMENDATION FOR 4K, IS WRONG FOR THIS JOB.
    That advice is for interpolating across large motion, where a coarse
    pyramid is needed to track a big displacement. Frame repair works between
    ADJACENT frames, where the motion is small, and there the coarse pyramid
    invents movement that is not there. The control is a pair of identical
    frames: nothing moved, so the answer must be that frame. At 1080p, scale
    0.5 came back off by 0.883 code levels on average, worst 177, with 3.85 per
    cent of pixels off by more than two levels, AND THE ERROR WAS WORST IN THE
    CENTRE OF THE FRAME, so it is invention and not an edge artifact. That is
    the same order as the RGB round trip this whole module exists to avoid.
    Scale 1.0 came back off by 0.015 levels, worst 7. At 4K: 0.794 against
    0.0095. On held out frames with real rotation and zoom, scale 1.0 also won
    outright on all three scores, error 1.141 against 1.257, detail 1.015
    against 0.949, placement 0.7758 against 0.7133. It is also FASTER, because
    the padding unit is 64/scale and a smaller unit pads less: 0.61s against
    1.80s per 4K frame. There is no axis on which 0.5 is better here.

Set up with `bash scripts/setup_rife.sh`, or point `CG_RIFE_HOME` at an existing
Practical-RIFE checkout. Weights: gpanaretou/practical-rife-interpolation on
HuggingFace, MIT licensed, about 80 MB.
"""

from __future__ import annotations

import os
import sys

import numpy as np

HOME = os.environ.get("CG_RIFE_HOME", os.path.expanduser("~/.models/rife"))

# Full resolution. See the note above: 0.5 invents motion between adjacent frames.
SCALE = float(os.environ.get("CG_RIFE_SCALE", "1.0"))

_net = None
_torch = None
_F = None


def _load():
    global _net, _torch, _F
    if _net is not None:
        return _net
    try:
        import torch
        import torch.nn.functional as F
    except ImportError as exc:
        raise RuntimeError(
            "torch is not installed in the colorgrade environment. "
            "Run: ~/.venvs/colorgrade/bin/pip install torch") from exc
    _torch, _F = torch, F

    weights = os.path.join(HOME, "train_log", "flownet.pkl")
    if not os.path.exists(weights):
        raise RuntimeError(f"no RIFE weights at {weights}. Run scripts/setup_rife.sh")
    if HOME not in sys.path:
        sys.path.insert(0, HOME)
    from train_log.IFNet_HDv3 import IFNet          # type: ignore

    m = IFNet()
    sd = torch.load(weights, map_location="cpu", weights_only=True)
    sd = {k.replace("module.", ""): v for k, v in sd.items()}
    missing, _ = m.load_state_dict(sd, strict=False)
    real = [k for k in missing if not k.startswith(("teacher", "caltime"))]
    if real:
        raise RuntimeError(f"RIFE weights are missing {real[:6]}")
    _net = m.eval().to(device())
    return _net


def device():
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _yuv_to_rgb_t(planes, x0, x1, H):
    """A column band as an RGB tensor, for flow estimation only."""
    import torch
    y, u, v = planes
    yf = y[:, x0:x1].astype(np.float32)
    uf = np.repeat(np.repeat(u[:, x0 // 2:x1 // 2], 2, 0), 2, 1).astype(np.float32)
    vf = np.repeat(np.repeat(v[:, x0 // 2:x1 // 2], 2, 0), 2, 1).astype(np.float32)
    yl = (yf - 16.0) / 219.0
    cb, cr = (uf - 128.0) / 224.0, (vf - 128.0) / 224.0
    rgb = np.clip(np.stack([yl + 1.5748 * cr,
                            yl - 0.1873 * cb - 0.4681 * cr,
                            yl + 1.8556 * cb], -1), 0, 1)
    return torch.from_numpy(rgb).permute(2, 0, 1)[None].to(device())


# RIFE's timestep is not the fraction of the move it delivers.
#
# Measured 31 Aug 2026 on RIFE 4.25, three source pairs of a real 24 fps shot,
# by tracking the delivered displacement against the source pair it was built
# from:
#
#     asked  0.0  0.1  0.2  0.3  0.4  0.5  0.6  0.7  0.8  0.9  1.0
#     got   .003 .003 .147 .275 .403 .520 .639 .760 .903 1.00 1.00
#
# Flat at both ends. A slot asked for 0.93 of a pair lands on the NEXT source
# frame outright, and the following slot, asked for 0.29 of the next pair, has
# already travelled 0.27. So every gap that straddles a source frame carries
# about a third of the ground it should.
#
# NO DUPLICATE COUNT OR STALL TEST CAN SEE THIS. Nothing is repeated, every
# frame is distinct, and every one is wrong by a fraction. It shows only in
# tracked displacement per gap. On that shot the correction took the moving
# panel from three effectively frozen gaps and a worst step of 2.17x the median
# to zero frozen gaps and 1.95x.
#
# This only bites a FRACTIONAL PHASE rebuild. A plain 2x interpolation asks for
# t=0.5, which the curve delivers almost exactly, which is why it went unseen
# through every earlier job.
#
# THIS IS A DEFAULT, NOT A CONSTANT, and the part of it that travels is not the
# part it would be natural to assume. A second shot was measured the same day,
# also 24 fps, also RIFE 4.25:
#
#     asked                  0.10   0.25   0.50   0.75   0.90
#     this curve            0.003  0.211  0.520  0.832  1.000
#     a second real shot    0.007  0.205  0.480  0.755  0.999
#     a synthetic pattern   0.000  0.177  0.454  0.748  1.000
#
# Three responses, one conclusion: the ends are the network and the upper middle
# is the shot. The 0.832 in the top row is the odd one out, not the rule.
#
# The flat ENDS are the network's timestep conditioning and they hold. The
# middle is the SHOT'S and does not. Applying THIS curve to a shot that answers
# like the second one, error in the phase delivered:
#
#     wanted phase          0.10   0.25   0.50   0.60   0.75   0.90
#     uncorrected          0.093  0.045  0.020  0.010  0.005  0.099
#     corrected with this  0.004  0.011  0.039  0.046  0.059  0.067
#
# So it rescues the ends, where nearly all of the damage is and where a slot
# straddling a source frame lands, and it makes the middle worse, by up to about
# six hundredths of a gap. That is the trade, and it is why this stays on by
# default rather than being either trusted or dropped: the ends are the fault
# the correction was built for and they reproduced on both shots.
#
# `cgtimestep.py` measures the shot's own curve in about a minute. Run it before
# any fractional phase rebuild that has to be exact, and pass what it prints as
# `timestep_curve=`. It prints what each of the three choices would cost on that
# shot as well, so the decision is measured rather than argued.
TIMESTEP_CURVE = (
    (0.0, 0.003), (0.1, 0.003), (0.2, 0.147), (0.3, 0.275), (0.4, 0.403),
    (0.5, 0.520), (0.6, 0.639), (0.7, 0.760), (0.8, 0.903), (0.9, 1.000),
    (1.0, 1.000),
)


def solve_timestep(t, curve=TIMESTEP_CURVE):
    """The timestep to ASK for so that RIFE DELIVERS phase `t`.

    Inverts the measured curve over its strictly increasing part. Outside that
    part the curve is flat and carries no information, so the nearest end of the
    usable range is returned: asking harder than 0.9 buys nothing at all.

    THE EXACT ENDPOINTS ARE PINNED and pass through untouched. Phase 0 asks for
    0 and phase 1 asks for 1, because a slot that lands exactly on a source
    frame should be conditioned the way the network conditions its own
    endpoints. This costs nothing in delivered motion, which is the whole
    reason it is safe: the curve gives 0.003 for both 0.0 and 0.1, and 1.000 for
    both 0.9 and 1.0, so the phase that comes back is the same either way.
    Measured on a real pair, dropping the pin cost 0.0070 to 0.0172 code levels
    against the source frame at t=1. Small, and there is no reason to pay it.
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    asked = [a for a, _ in curve]
    got = [g for _, g in curve]
    lo = 0
    while lo + 1 < len(got) and got[lo + 1] <= got[lo]:
        lo += 1
    hi = len(got) - 1
    while hi - 1 > lo and got[hi - 1] >= got[hi]:
        hi -= 1
    xs, ys = got[lo:hi + 1], asked[lo:hi + 1]
    if t <= xs[0]:
        return ys[0]
    if t >= xs[-1]:
        return ys[-1]
    return float(np.interp(t, xs, ys))


def flow_mask(i0, i1, t, scale=SCALE, correct_timestep=True,
              timestep_curve=TIMESTEP_CURVE):
    """RIFE's own flow and fusion mask, at full resolution.

    `t` is the phase WANTED, not the number handed to the network. With
    `correct_timestep` the measured curve above is inverted first, which is what
    makes a fractional phase rebuild land where it was asked to. Pass
    `correct_timestep=False` to reproduce a build made before 31 Aug 2026 byte
    for byte.
    """
    import torch
    net = _load()
    F = _F
    h, w = i0.shape[-2:]
    unit = int(64 / scale)
    ph, pw = ((h - 1) // unit + 1) * unit, ((w - 1) // unit + 1) * unit
    pad = lambda x: F.pad(x, (0, pw - w, 0, ph - h), mode="replicate")  # noqa: E731
    x = torch.cat((pad(i0), pad(i1)), 1).to(device())
    sl = [16 / scale, 8 / scale, 4 / scale, 2 / scale, 1 / scale]
    with torch.no_grad():
        ts = (solve_timestep(float(t), timestep_curve)
              if correct_timestep else float(t))
        flow_l, mask_t, _ = net(x, timestep=ts, scale_list=sl)
    return flow_l[4][:, :, :h, :w], torch.sigmoid(mask_t)[:, :, :h, :w]


def _warp(plane, flow, full_w, H, sample="bicubic"):
    import torch
    F = _F
    ph, pw = plane.shape[-2:]
    f = flow
    if (ph, pw) != (H, full_w):
        f = F.interpolate(flow, (ph, pw), mode="bilinear", align_corners=False)
        f = f * torch.tensor([pw / full_w, ph / H], device=flow.device).view(1, 2, 1, 1)
    hh, ww = plane.shape[-2:]
    yy, xx = torch.meshgrid(
        torch.arange(hh, device=f.device, dtype=torch.float32),
        torch.arange(ww, device=f.device, dtype=torch.float32), indexing="ij")
    gx = (xx + f[:, 0]) / (ww - 1) * 2 - 1
    gy = (yy + f[:, 1]) / (hh - 1) * 2 - 1
    return F.grid_sample(plane, torch.stack([gx, gy], -1), mode=sample,
                         padding_mode="border", align_corners=True)


def warp_planes(pa, pb, t, x0, x1, H, scale=SCALE, sample="bicubic",
                correct_timestep=True, timestep_curve=TIMESTEP_CURVE):
    """The rebuilt y, u and v for columns [x0, x1), as uint8 arrays.

    `t` is the phase WANTED between pa and pb. See `solve_timestep`.

    `x0` and `x1` are a COLUMN BAND, and that is not a convenience. A split
    screen is two unrelated pictures sharing one raster, so one flow field
    estimated across the whole frame drags one panel's motion into the other.
    Call this once per panel with that panel's own columns.
    """
    import torch
    _load()
    F = _F
    ra = _yuv_to_rgb_t(pa, x0, x1, H)
    rb = _yuv_to_rgb_t(pb, x0, x1, H)
    flow, mask = flow_mask(ra, rb, t, scale=scale,
                           correct_timestep=correct_timestep,
                           timestep_curve=timestep_curve)
    pw = x1 - x0
    out = []
    for i in range(3):
        sc = 1 if i == 0 else 2
        sl = slice(x0 // sc, x1 // sc)
        ta = torch.from_numpy(pa[i][:, sl].astype(np.float32))[None, None].to(device())
        tb = torch.from_numpy(pb[i][:, sl].astype(np.float32))[None, None].to(device())
        g0 = _warp(ta, flow[:, :2], pw, H, sample)
        g1 = _warp(tb, flow[:, 2:4], pw, H, sample)
        m = mask if sc == 1 else F.interpolate(mask, g0.shape[-2:], mode="bilinear",
                                               align_corners=False)
        r = (g0 * m + g1 * (1 - m))[0, 0].clamp(0, 255).cpu().numpy()
        out.append(np.round(r).astype(np.uint8))
    return out


if __name__ == "__main__":
    try:
        _load()
        print(f"RIFE ready at {HOME}, running on {device()}")
    except RuntimeError as exc:
        print(f"not ready: {exc}")
        raise SystemExit(1)
