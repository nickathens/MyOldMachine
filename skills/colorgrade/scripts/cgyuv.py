"""Build and measure frames without ever leaving the video's own colour space.

The rule this module exists to enforce: a frame that will be spliced back into a
film is never converted to RGB. Going out and back is not colour exact. Measured
on a real 4K frame, an OpenCV round trip shifted luma by 0.84 of a code level
and U by 0.53 across the whole picture, and when the file is tagged bt709 while
the reader assumes bt601 it reaches 2.79 levels mean and 26 at worst, mostly
green. That is a bias, not noise. Every rebuilt frame then sits off its untouched
neighbours by the same amount, and in a shot where rebuilt and untouched frames
alternate it reads as a flicker at the repair rate. On one delivery that was
8 Hz, and the client rejected it.

So: motion is estimated in RGB, which yields a vector field and touches no
output pixel, and the warp and the blend then run on the native yuv420p planes,
luma at full size and the two chroma planes at half size with the flow halved to
match. A rebuilt frame is spliced as raw yuv, byte for byte in the same space as
the frames either side, so a correct rebuild is arithmetically incapable of
shifting the colour.

The second thing here is the ruler. Any measurement of a rebuilt frame has to
reach the measuring point down the identical path an untouched frame takes, or
the reading is about the path and not about the frame. `Ruler.small` is that
path, and `Ruler.check` proves it reproduces a known answer before anything is
measured with it.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import numpy as np

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


# ---------------------------------------------------------------- raw planes


@dataclass
class Spec:
    """Geometry and tags of one yuv420p frame, taken from the source file."""
    width: int
    height: int
    color_range: str = "tv"
    colorspace: str = "bt709"

    def __post_init__(self):
        if self.width % 2 or self.height % 2:
            raise ValueError(f"yuv420p needs even dimensions, got {self.width}x{self.height}")

    @property
    def ysize(self):
        return self.width * self.height

    @property
    def csize(self):
        return self.ysize // 4

    @property
    def frame_bytes(self):
        return self.ysize * 3 // 2

    @property
    def tags(self):
        return ["-color_range", self.color_range, "-colorspace", self.colorspace]


def spec_from_media(media, color_range=None) -> Spec:
    """Build a Spec from a cgvideo.Media, carrying the file's own tags over.

    Range is rarely written into the container. Limited is the correct default
    for delivery footage and is what ffmpeg itself assumes, so a missing tag is
    read as limited rather than guessed at.
    """
    cs = getattr(media, "color_space", "") or "bt709"
    if cs in ("unknown", "reserved"):
        cs = "bt709"
    return Spec(media.width, media.height, color_range or "tv", cs)


def planes(buf, spec: Spec):
    """The three planes of one raw frame, as views, no copy."""
    y = np.frombuffer(buf[:spec.ysize], np.uint8).reshape(spec.height, spec.width)
    o = spec.ysize
    u = np.frombuffer(buf[o:o + spec.csize], np.uint8).reshape(spec.height // 2, spec.width // 2)
    v = np.frombuffer(buf[o + spec.csize:o + 2 * spec.csize], np.uint8).reshape(
        spec.height // 2, spec.width // 2)
    return y, u, v


def luma(buf, spec: Spec):
    return np.frombuffer(buf[:spec.ysize], np.uint8).reshape(spec.height, spec.width)


def decode(path, spec: Spec, start=0, count=None):
    """Yield (index, raw frame) in one sequential decode.

    One pass, always. Per range decodes over a 4K file are dozens of passes, and
    a select expression with a few hundred terms overflows ffmpeg's parser.

    The caller normally stops early, which breaks the pipe and makes ffmpeg
    complain, so its stderr is dropped. Every caller therefore has to count the
    frames it received and fail on a short read of its own accord.
    """
    p = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
    try:
        i = 0
        last = None if count is None else start + count - 1
        while last is None or i <= last:
            buf = p.stdout.read(spec.frame_bytes)
            if len(buf) < spec.frame_bytes:
                break
            if i >= start:
                yield i, buf
            i += 1
    finally:
        p.stdout.close()
        p.terminate()
        p.wait()


def extract(path, wanted, out_dir, spec: Spec, verbose=True):
    """Write the frames in `wanted` to out_dir as raw .yuv, in one decode."""
    os.makedirs(out_dir, exist_ok=True)
    want = {int(f) for f in wanted}
    todo = {f for f in want
            if not (os.path.exists(f"{out_dir}/{f:06d}.yuv")
                    and os.path.getsize(f"{out_dir}/{f:06d}.yuv") == spec.frame_bytes)}
    if not todo:
        if verbose:
            print(f"  source planes already present: {len(want)}")
        return 0
    n = 0
    last = max(todo)
    for i, buf in decode(path, spec):
        if i in todo:
            with open(f"{out_dir}/{i:06d}.yuv", "wb") as fh:
                fh.write(buf)
            n += 1
            if verbose and n % 100 == 0:
                print(f"  extracted {n}/{len(todo)}", flush=True)
        if i >= last:
            break
    missing = [f for f in todo if not os.path.exists(f"{out_dir}/{f:06d}.yuv")]
    if missing:
        raise RuntimeError(f"decode ended early: {len(missing)} of {len(todo)} frames never "
                           f"arrived, first missing {missing[0]}. The file is shorter than "
                           f"the frame count says, or it is damaged.")
    if verbose:
        print(f"  extracted {n}/{len(todo)}")
    return n


def read_yuv(path, spec: Spec):
    with open(path, "rb") as fh:
        buf = fh.read()
    if len(buf) != spec.frame_bytes:
        raise ValueError(f"{path}: {len(buf)} bytes, expected {spec.frame_bytes}")
    return buf


# ---------------------------------------------------------------- synthesis


def warp_plane(plane_t, flow_luma, spec: Spec):
    """Warp one plane. The flow arrives at luma scale and is rescaled to fit."""
    import torch
    import torch.nn.functional as F
    import cgflow
    ph, pw = plane_t.shape[-2:]
    f = flow_luma
    if (ph, pw) != (spec.height, spec.width):
        f = F.interpolate(flow_luma, (ph, pw), mode="bilinear", align_corners=False)
        f = f * torch.tensor([pw / spec.width, ph / spec.height],
                             device=f.device).view(1, 2, 1, 1)
    return cgflow.backwarp(plane_t, f)


def engine():
    """Which synthesiser will build the frames: 'rife' where set up, else 'raft'.

    RIFE is preferred, and the reason is measured rather than fashionable.
    Warping BOTH neighbours and averaging them, which is what the raft path
    below does, is itself a source of softness: two warps that disagree by a
    pixel average into a two tap blur, and no measurement of motion can see it
    because nothing moved. RIFE returns a per pixel fusion mask instead of a
    fixed blend weight, so where the two warps disagree it chooses one rather
    than averaging them. Scored on frames hidden from both, on real footage:

        error 2.078 against 2.306, detail held 99.9 per cent against 95.2,
        edge placement 0.7883 against 0.7665, and about five times faster.

    The raft path stays because it needs only torchvision, which the colour
    tools already pull, while RIFE needs a checkout and weights. It is a real
    fallback and not a stub, but it IS materially worse, and on a 4 second cut
    of real graded footage broken by a 24 to 16 to 24 round trip it did not
    merely score lower, it failed the delivery verdict outright:

        rife : 32 of 32 rebuilds kept, sharpness 1.053 of the neighbours,
               colour offset 1.01x the real median, stutter 32 frames to 0
        raft : 32 of 32 kept only after sharpening, colour offset 1.58x,
               VERDICT FAIL, the rebuilds carry a cast

    The cast is not caused by the sharpening that compensates for the blur. With
    sharpening off, raft is worse on every count: 18 of 32 rebuilds reverted as
    too soft, colour offset 5.01x, and 27 of the 32 frozen frames still frozen
    at the end. The softness and the cast are the same fault, which is the two
    tap blur, and sharpening only trades one for less of the other.

    So when this returns 'raft', expect the gate to reject work, and read that
    as the fallback being honest rather than as the footage being unrepairable.

    Set CG_FRAME_ENGINE to 'raft' or 'rife' to force one. Forcing 'rife' when it
    is not installed is an error rather than a silent downgrade, because a
    delivery built by the fallback should never be mistaken for the better one.
    """
    want = os.environ.get("CG_FRAME_ENGINE", "").strip().lower()
    if want not in ("", "rife", "raft"):
        raise ValueError(f"CG_FRAME_ENGINE must be 'rife' or 'raft', got {want!r}")
    if want == "raft":
        return "raft"
    try:
        import cgrife
        cgrife._load()
        return "rife"
    except Exception as exc:
        if want == "rife":
            raise RuntimeError(f"CG_FRAME_ENGINE=rife but RIFE is not ready: {exc}")
        return "raft"


def synth(buf_a, buf_b, t, f01=None, f10=None, spec: Spec = None, how=None):
    """The frame t of the way from A to B, built plane by plane in yuv420p.

    Never in RGB. The motion estimate may look at RGB; the delivered pixels are
    built from the file's own planes and spliced back as raw yuv.

    `f01`/`f10` are the raft path's precomputed flows and are ignored by rife,
    which derives its own. Pass `how` to override the engine for one call.
    """
    if spec is None:
        raise TypeError("synth needs a Spec")
    how = how or engine()
    if how == "rife":
        import cgrife
        out = cgrife.warp_planes(list(planes(buf_a, spec)), list(planes(buf_b, spec)),
                                 t, 0, spec.width, spec.height)
        return b"".join(p.tobytes() for p in out)

    import torch
    import cgflow
    if f01 is None or f10 is None:
        raise TypeError("the raft path needs f01 and f10")
    ft0, ft1 = cgflow.split(f01, f10, t)
    out = []
    with torch.no_grad():
        for pa, pb in zip(planes(buf_a, spec), planes(buf_b, spec)):
            ta, tb = cgflow.to_tensor(pa), cgflow.to_tensor(pb)
            g = (1 - t) * warp_plane(ta, ft0, spec) + t * warp_plane(tb, ft1, spec)
            out.append(np.clip(g[0, 0].cpu().numpy() + 0.5, 0, 255).astype(np.uint8))
    return b"".join(p.tobytes() for p in out)


def paste_panel(base_buf, new_buf, x0, x1, spec: Spec):
    """Take the column range x0 to x1 from new_buf, everything else from base_buf.

    For split screens, where the two halves carry different cadence and only one
    of them is being repaired. Both x are rounded to even so the chroma planes
    cut on the same pixel as the luma. Every measurement of a split screen has to
    be scoped this way too: a reading taken across the divider is a reading of
    two different pictures averaged together.
    """
    x0 = max(0, int(x0) // 2 * 2)
    x1 = min(spec.width, (int(x1) + 1) // 2 * 2)
    out = []
    for pb, pn in zip(planes(base_buf, spec), planes(new_buf, spec)):
        merged = pb.copy()
        s = 1 if pb.shape[1] == spec.width else 2
        merged[:, x0 // s:x1 // s] = pn[:, x0 // s:x1 // s]
        out.append(merged)
    return b"".join(p.tobytes() for p in out)


# ---------------------------------------------------------------- the ruler


class Ruler:
    """Measure a frame at viewing scale, down one path, and prove the path first.

    Two failures live behind this class, both real and both expensive.

    Sharpness measured at native 4K is dominated by grain, and a resampled frame
    keeps grain, so the reading passed frames that are visibly soft once the
    picture is scaled to the size it is watched at. Measure at viewing scale.

    Handing ffmpeg a bare gray plane declares it full range, which skips the
    16 to 235 expansion the tagged path applies, and the variance reads 1.355
    times low. Every rebuilt frame then looks far softer than it is and the
    sharpening drives to its cap. The frame goes in as a whole tagged yuv420p
    frame, never as a lone luma plane.
    """

    def __init__(self, spec: Spec, view_w=1920):
        self.spec = spec
        self.view_w = min(int(view_w), spec.width)
        self.view_h = int(round(spec.height * self.view_w / spec.width)) // 2 * 2
        self.checked = False

    def scan(self, path, verbose=True):
        """Sharpness, motion and span for every frame of a file, in one decode.

        lap   variance of the Laplacian          how sharp the frame is
        diff  mean |frame minus the one before|  how far it moved
        span  mean |next minus previous|         how far the pair around it moved

        span is measured for every frame, not only the repaired ones. Without it
        there is no floor from the film's own frames to judge a rebuild against,
        and the cut would have to be picked by taste.
        """
        import cv2
        p = subprocess.Popen(
            [FFMPEG, "-v", "error", "-i", path,
             "-vf", f"scale={self.view_w}:{self.view_h}",
             "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, bufsize=10 ** 7)
        n = self.view_w * self.view_h
        lap, diff, span, win = [], [], [], []
        try:
            while True:
                b = p.stdout.read(n)
                if len(b) < n:
                    break
                g = np.frombuffer(b, np.uint8).reshape(self.view_h, self.view_w)
                lap.append(float(cv2.Laplacian(g, cv2.CV_32F).var()))
                win.append(g.astype(np.float32))
                diff.append(0.0 if len(win) < 2 else float(np.abs(win[-1] - win[-2]).mean()))
                span.append(0.0)
                if len(win) >= 3:
                    span[-2] = float(np.abs(win[-1] - win[-3]).mean())
                    win.pop(0)
                if verbose and len(lap) % 500 == 0:
                    print(f"  scanned {len(lap)}", flush=True)
        finally:
            p.stdout.close()
            p.terminate()
            p.wait()
        return np.array(lap), np.array(diff), np.array(span)

    def small(self, buf, y_override=None):
        """One raw frame at viewing scale, as gray, down the tagged path."""
        payload = buf
        if y_override is not None:
            payload = (np.clip(y_override + 0.5, 0, 255).astype(np.uint8).tobytes()
                       + bytes(buf[self.spec.ysize:]))
        p = subprocess.run(
            [FFMPEG, "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
             "-s", f"{self.spec.width}x{self.spec.height}", *self.spec.tags,
             "-i", "-", "-vf", f"scale={self.view_w}:{self.view_h}",
             "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            input=payload, capture_output=True)
        n = self.view_w * self.view_h
        if len(p.stdout) < n:
            raise RuntimeError(f"ruler produced {len(p.stdout)} bytes, expected {n}: "
                               f"{p.stderr.decode()[:200]}")
        return np.frombuffer(p.stdout[:n], np.uint8).reshape(self.view_h, self.view_w)

    def lapvar(self, gray):
        import cv2
        return float(cv2.Laplacian(gray, cv2.CV_32F).var())

    def check(self, yuv_dir, frames, scan_lap, tol=0.01):
        """Prove the per frame ruler reproduces the sequential scan.

        A real source frame put through `small` must return the value the scan
        already holds for that frame. If it does not, the two paths differ and
        every comparison between a rebuilt frame and a real one is meaningless.
        """
        worst, checked = 0.0, 0
        for f in list(frames)[:6]:
            path = f"{yuv_dir}/{int(f):06d}.yuv"
            if not os.path.exists(path):
                continue
            got = self.lapvar(self.small(read_yuv(path, self.spec)))
            want = float(scan_lap[int(f)])
            if want <= 0:
                continue
            worst = max(worst, abs(got / want - 1))
            checked += 1
        if not checked:
            raise RuntimeError("ruler check had no frames to check against")
        self.checked = worst < tol
        print(f"  ruler check on {checked} real frames: worst error {worst * 100:.2f}%"
              f"  {'OK' if self.checked else 'MISMATCH'}")
        if not self.checked:
            raise RuntimeError(
                f"the per frame ruler does not reproduce the film's own frames "
                f"({worst * 100:.2f}% off). Do not measure anything until the two "
                f"paths agree: check the range and matrix tags in the Spec.")
        return worst
