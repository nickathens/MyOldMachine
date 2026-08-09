"""Motion estimation for frame repair: RAFT optical flow, the warp, the blend.

This module is the only place that touches RGB. It returns a vector field and
nothing else. No output pixel is ever produced here when a repair runs for real,
because converting the picture to RGB and back is not colour exact and a
converted frame spliced among untouched frames reads as a flicker. See
`cgyuv.synth` for the path that actually builds a delivered frame, and
`reference/03_failures.md` entry 11 for what it cost the one time it was skipped.

The RGB path below (`between`) is still wanted for one job: the holdout test,
which hides a real frame, rebuilds it, and scores the result. Errors there are
compared against each other, so a shared bias cancels.

Flow is estimated small and scaled up. RAFT builds an all pairs correlation
volume that grows with the square of the pixel count, so 4K estimation does not
fit on an M series machine and is not needed for camera and body moves.
Measured against held out real frames on a 4K commercial: bilinear sampling kept
68.7 per cent of the detail of a real frame, bicubic 77.4 per cent for the same
accuracy and the same time. Lifting the flow from 1024x576 to 1280x720 took it
to 80.4 per cent. 1536x864 gave nothing back for half as much time again, so it
stops at 1280 wide.
"""

from __future__ import annotations

import numpy as np

FLOW_MAX_W = 1280
SAMPLE = "bicubic"

_model = None
_dev = None


def device():
    """Best available torch device. Apple GPU first, then CUDA, then CPU."""
    global _dev
    if _dev is None:
        import torch
        if torch.backends.mps.is_available():
            _dev = "mps"
        elif torch.cuda.is_available():
            _dev = "cuda"
        else:
            _dev = "cpu"
    return _dev


def model():
    global _model
    if _model is None:
        from torchvision.models.optical_flow import Raft_Large_Weights, raft_large
        _model = raft_large(weights=Raft_Large_Weights.C_T_SKHT_V2, progress=False)
        _model = _model.eval().to(device())
    return _model


def flow_size(width, height, max_w=FLOW_MAX_W):
    """Estimation size: capped at max_w, aspect kept, both sides a multiple of 8
    because RAFT downsamples three times."""
    w = min(int(width), int(max_w))
    h = int(round(height * w / width))
    return max(8, w // 8 * 8), max(8, h // 8 * 8)


def load_rgb(path):
    """A PNG or JPG as a float tensor on the compute device, 0 to 1."""
    import torch
    from PIL import Image
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(a).permute(2, 0, 1)[None].to(device())


def save_rgb(t, path):
    from PIL import Image
    a = (t[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255.0 + 0.5).astype(np.uint8)
    Image.fromarray(a).save(path, compress_level=1)


def to_tensor(plane):
    """One single channel plane as a 1x1xHxW float tensor on the device."""
    import torch
    return torch.from_numpy(np.ascontiguousarray(plane, dtype=np.float32))[None, None].to(device())


def flow(a, b, max_w=FLOW_MAX_W):
    """Flow from a to b, estimated small and returned at the full frame size."""
    import torch
    import torch.nn.functional as F
    with torch.no_grad():
        H, W = a.shape[-2:]
        fw, fh = flow_size(W, H, max_w)
        sa = F.interpolate(a, (fh, fw), mode="bilinear", align_corners=False)
        sb = F.interpolate(b, (fh, fw), mode="bilinear", align_corners=False)
        f = model()(sa * 2 - 1, sb * 2 - 1)[-1]
        f = F.interpolate(f, (H, W), mode="bilinear", align_corners=False)
        f[:, 0] *= W / fw
        f[:, 1] *= H / fh
        return f


def flow_pair(png_a, png_b, max_w=FLOW_MAX_W):
    """Both directions between two stills. The only entry point a yuv repair uses.

    The stills may carry a wrong colour, as long as both carry the same wrong
    colour: RAFT matches structure, and a constant shared by both frames of a
    pair moves no vector. Nothing read here reaches an output pixel.
    """
    ia, ib = load_rgb(png_a), load_rgb(png_b)
    return flow(ia, ib, max_w), flow(ib, ia, max_w)


def backwarp(img, f):
    """Sample img along the vector field f. Works on any channel count."""
    import torch
    import torch.nn.functional as F
    H, W = img.shape[-2:]
    yy, xx = torch.meshgrid(
        torch.arange(H, device=img.device, dtype=torch.float32),
        torch.arange(W, device=img.device, dtype=torch.float32),
        indexing="ij")
    gx = (xx + f[:, 0]) / (W - 1) * 2 - 1
    gy = (yy + f[:, 1]) / (H - 1) * 2 - 1
    grid = torch.stack([gx, gy], dim=-1)
    return F.grid_sample(img, grid, mode=SAMPLE, padding_mode="border", align_corners=True)


def split(f01, f10, t):
    """The two flows that carry each neighbour to the instant t, t in (0,1).

    The standard quadratic split. A frame close to i0 leans on i0 and the blend
    weights match, which keeps a mistimed flow from smearing the whole frame.
    Shared by the RGB and the yuv synthesis so the two cannot drift apart.
    """
    ft0 = -(1 - t) * t * f01 + t * t * f10
    ft1 = (1 - t) * (1 - t) * f01 - t * (1 - t) * f10
    return ft0, ft1


def between(i0, i1, t, f01=None, f10=None, max_w=FLOW_MAX_W):
    """Synthesise the frame t of the way from i0 to i1, in RGB.

    For measurement only. A delivered frame is built by `cgyuv.synth`.
    """
    import torch
    with torch.no_grad():
        if f01 is None:
            f01 = flow(i0, i1, max_w)
        if f10 is None:
            f10 = flow(i1, i0, max_w)
        ft0, ft1 = split(f01, f10, t)
        return (1 - t) * backwarp(i0, ft0) + t * backwarp(i1, ft1)


def available():
    """Whether the motion stack can run at all, and why not if it cannot."""
    try:
        import torch  # noqa: F401
        import torchvision  # noqa: F401
    except ImportError as e:
        return False, (f"{e}. Install into the colorgrade environment: "
                       "~/.venvs/colorgrade/bin/pip install torch torchvision")
    return True, device()


if __name__ == "__main__":
    ok, why = available()
    print(f"motion stack: {'ready on ' + why if ok else 'MISSING, ' + why}")
    if ok:
        for w, h in ((3840, 2160), (1920, 1080), (2560, 720)):
            print(f"  {w}x{h} estimates flow at {flow_size(w, h)[0]}x{flow_size(w, h)[1]}")
