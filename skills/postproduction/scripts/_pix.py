#!/usr/bin/env python3
"""Pixels for the compositing engine: read honestly, blend in light, remember.

Three rules live here, because these are the three that get broken silently and
are then invisible to every check that gets run afterwards.

1. **Blending happens in LIGHT, not in code values.** An encoded value is a
   perceptual code, and averaging two codes is not averaging two quantities of
   light. The visible cost is a dark seam wherever two different hues meet
   across an edge. So `over`, every blur, every resample and every mix in this
   module converts to linear first and says which transfer function it used.
   Two arrays carrying different transfers are refused, the same way
   `_common.require_same_path` refuses two decode paths.

2. **Alpha is premultiplied while it is being filtered, and unpremultiplied
   while it is being colour corrected.** Blurring straight RGBA drags whatever
   colour is sitting in the transparent pixels into the edge. Grading
   premultiplied RGB multiplies the correction by the alpha, so the edge gets a
   different correction from the core.

3. **Pad before you blur.** A blur with nowhere to fall off to keeps a hard
   edge, and where the artwork fills its own canvas the kernel reads the pad
   instead. That is the four notch signature: top, bottom, left and right, at
   exactly the points where the artwork touches its canvas.

Everything is float32 RGB in 0..1 with the transfer named. Nothing here is BGR.
Channel order is converted once, at the door, because a red and blue swap is a
no op on every neutral and survives every histogram.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402


# ---------------------------------------------------------------- transfers
#
# A transfer function is a claim about what the numbers mean. Naming the wrong
# one is a real error with a small signature: an sRGB decode of BT.1886 material
# lifts the low end by about 1.5 per cent of range, which reads as a milky edge
# on a dark composite and as nothing at all on a mid grey chart.

TRANSFERS = ("srgb", "bt1886", "gamma22", "linear")


def _srgb_to_linear(a):
    a = np.asarray(a, dtype=np.float32)
    return np.where(a <= 0.04045, a / 12.92,
                    np.power((a + 0.055) / 1.055, 2.4)).astype(np.float32)


def _linear_to_srgb(a):
    a = np.clip(np.asarray(a, dtype=np.float32), 0.0, None)
    return np.where(a <= 0.0031308, a * 12.92,
                    1.055 * np.power(a, 1.0 / 2.4) - 0.055).astype(np.float32)


def to_linear(arr, transfer):
    """Encoded values to light. `transfer` must be named, never guessed."""
    if transfer not in TRANSFERS:
        raise ValueError(f"unknown transfer {transfer!r}; one of {TRANSFERS}")
    a = np.asarray(arr, dtype=np.float32)
    if transfer == "linear":
        return a
    if transfer == "srgb":
        return _srgb_to_linear(a)
    gamma = 2.4 if transfer == "bt1886" else 2.2
    return np.power(np.clip(a, 0.0, None), gamma).astype(np.float32)


def to_display(arr, transfer):
    """Light back to encoded values."""
    if transfer not in TRANSFERS:
        raise ValueError(f"unknown transfer {transfer!r}; one of {TRANSFERS}")
    a = np.asarray(arr, dtype=np.float32)
    if transfer == "linear":
        return a
    if transfer == "srgb":
        return _linear_to_srgb(a)
    gamma = 2.4 if transfer == "bt1886" else 2.2
    return np.power(np.clip(a, 0.0, None), 1.0 / gamma).astype(np.float32)


# Rec.709 luma weights. Used for LIGHT, so the array must already be linear.
LUMA_709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def linear_luma(rgb_linear):
    """Relative luminance of linear RGB. Not a luma code, a quantity of light."""
    return np.tensordot(np.asarray(rgb_linear, dtype=np.float32), LUMA_709,
                        axes=([-1], [0])).astype(np.float32)


# Vectorised CIE Lab, for maps rather than single colours. `_colour.py` has the
# scalar versions and delta E 2000 for a pair of swatches; this is the same
# science over a whole frame, and it takes LINEAR RGB, never code values.

_M_RGB_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_D65 = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)


def linear_rgb_to_lab(rgb_linear):
    """CIE Lab from LINEAR sRGB primaries under D65."""
    xyz = np.tensordot(np.clip(np.asarray(rgb_linear, dtype=np.float32), 0, None),
                       _M_RGB_XYZ.T, axes=([-1], [0])) / _D65
    e, k = 216.0 / 24389.0, 24389.0 / 27.0
    f = np.where(xyz > e, np.cbrt(xyz), (k * xyz + 16.0) / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1).astype(np.float32)


def lab_hue_shift(lab_a, lab_b):
    """Hue rotation in degrees between two Lab maps, and the plain CIE76 distance.

    Hue is the axis that names an artefact: a yellow that goes orange has
    rotated, and saying "delta E 6" does not tell anyone which way. Pixels with
    almost no chroma have no meaningful hue, so they come back as zero rather
    than as noise.
    """
    a1, b1 = lab_a[..., 1], lab_a[..., 2]
    a2, b2 = lab_b[..., 1], lab_b[..., 2]
    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    h = np.degrees(np.arctan2(b2, a2) - np.arctan2(b1, a1))
    h = (h + 180.0) % 360.0 - 180.0
    h = np.where((c1 < 2.0) | (c2 < 2.0), 0.0, h)
    de76 = np.linalg.norm(lab_a - lab_b, axis=-1)
    return h.astype(np.float32), de76.astype(np.float32)


# ---------------------------------------------------------------- image class


class Image:
    """An array that remembers its file, its decode path and its transfer.

    `rgb` is float32 HxWx3 in 0..1. `alpha` is float32 HxW or None. `premul`
    says whether rgb has already been multiplied by alpha; every operation that
    cares checks it rather than assuming.
    """

    __slots__ = ("rgb", "alpha", "transfer", "premul", "source", "decode_path",
                 "bits")

    def __init__(self, rgb, transfer, source, decode_path, alpha=None,
                 premul=False, bits=8):
        self.rgb = np.asarray(rgb, dtype=np.float32)
        self.alpha = None if alpha is None else np.asarray(alpha, dtype=np.float32)
        self.transfer = transfer
        self.premul = bool(premul)
        self.source = str(source)
        self.decode_path = str(decode_path)
        self.bits = int(bits)

    # -- shape

    @property
    def height(self):
        return self.rgb.shape[0]

    @property
    def width(self):
        return self.rgb.shape[1]

    def copy(self):
        return Image(self.rgb.copy(), self.transfer, self.source,
                     self.decode_path,
                     None if self.alpha is None else self.alpha.copy(),
                     self.premul, self.bits)

    # -- transfer

    def as_linear(self):
        """A copy in linear light. Premultiplication is preserved correctly."""
        if self.transfer == "linear":
            return self.copy()
        out = self.copy()
        if out.premul and out.alpha is not None:
            # Undo premultiplication BEFORE the transfer: a premultiplied value
            # is alpha times a colour, and a transfer function is not linear, so
            # applying it to the product is not applying it to the colour.
            straight = unpremultiply(out.rgb, out.alpha)
            straight = to_linear(straight, self.transfer)
            out.rgb = premultiply(straight, out.alpha)
        else:
            out.rgb = to_linear(out.rgb, self.transfer)
        out.transfer = "linear"
        return out

    def as_transfer(self, transfer):
        """A copy encoded with the named transfer."""
        lin = self.as_linear()
        if transfer == "linear":
            return lin
        if lin.premul and lin.alpha is not None:
            straight = unpremultiply(lin.rgb, lin.alpha)
            lin.rgb = premultiply(to_display(straight, transfer), lin.alpha)
        else:
            lin.rgb = to_display(lin.rgb, transfer)
        lin.transfer = transfer
        return lin

    # -- alpha association

    def to_premultiplied(self):
        if self.premul or self.alpha is None:
            return self
        self.rgb = premultiply(self.rgb, self.alpha)
        self.premul = True
        return self

    def to_straight(self):
        if not self.premul or self.alpha is None:
            return self
        self.rgb = unpremultiply(self.rgb, self.alpha)
        self.premul = False
        return self

    def provenance(self):
        return {"source": self.source, "decode_path": self.decode_path,
                "transfer": self.transfer, "premultiplied": self.premul,
                "bits": self.bits, "raster": f"{self.width}x{self.height}"}


def require_same_transfer(*images):
    """Refuse to blend two arrays that do not agree about what a number means."""
    kinds = {im.transfer for im in images}
    if len(kinds) > 1:
        raise ValueError(
            "These arrays carry different transfer functions "
            f"({', '.join(sorted(kinds))}) and cannot be blended. "
            "Convert both to linear first, or name the transfer you meant."
        )
    return True


# ---------------------------------------------------------------- alpha


def premultiply(rgb, alpha):
    return (np.asarray(rgb, dtype=np.float32) *
            np.asarray(alpha, dtype=np.float32)[..., None]).astype(np.float32)


def unpremultiply(rgb, alpha, floor=1e-4):
    """Divide the alpha back out. Below `floor` there is no colour to recover."""
    a = np.asarray(alpha, dtype=np.float32)[..., None]
    return np.where(a > floor,
                    np.asarray(rgb, dtype=np.float32) / np.maximum(a, floor),
                    0.0).astype(np.float32)


def over(fg, alpha, bg, premul=False):
    """Porter and Duff `over`, in whatever space the caller has already put the arrays in.

    Callers must have converted to linear already; `composite_over` below is the
    one that enforces it. Kept separate so the arithmetic can be unit tested
    without any colour policy attached to it.
    """
    a = np.asarray(alpha, dtype=np.float32)[..., None]
    f = np.asarray(fg, dtype=np.float32)
    b = np.asarray(bg, dtype=np.float32)
    if not premul:
        f = f * a
    return (f + b * (1.0 - a)).astype(np.float32)


def composite_over(fg_img, bg_img, alpha=None):
    """`over` done properly: linear light, premultiplied, transfers checked.

    Returns an Image in linear light. Encode it yourself, deliberately.
    """
    fg = fg_img.as_linear()
    bg = bg_img.as_linear()
    require_same_transfer(fg, bg)
    a = fg.alpha if alpha is None else np.asarray(alpha, dtype=np.float32)
    if a is None:
        raise ValueError("composite_over needs an alpha, on the foreground or "
                         "passed in")
    rgb = over(fg.rgb, a, bg.rgb, premul=fg.premul)
    out = Image(rgb, "linear", fg.source, fg.decode_path, alpha=None,
                premul=False, bits=max(fg.bits, bg.bits))
    return out


# ---------------------------------------------------------------- filtering


def pad_reflect(arr, pad):
    if pad <= 0:
        return arr
    mode = "reflect" if min(arr.shape[:2]) > pad else "edge"
    if arr.ndim == 2:
        return np.pad(arr, ((pad, pad), (pad, pad)), mode=mode)
    return np.pad(arr, ((pad, pad), (pad, pad), (0, 0)), mode=mode)


def blur_rgba(rgb, alpha, sigma, premul=False):
    """Blur an RGBA pair the only way that does not stain the edge.

    Blur `rgb*a` and `a` separately, then divide back out. Straight blurring
    mixes the colour that happens to be sitting in the transparent pixels into
    the visible edge, and if that colour is the pad it reads as a dark notch at
    exactly the four points where the artwork meets its own canvas.

    The working copy is padded by three sigma first, so the blur has somewhere
    to fall off to.
    """
    import cv2

    if sigma <= 0:
        return np.asarray(rgb, dtype=np.float32), np.asarray(alpha, dtype=np.float32)
    a = np.asarray(alpha, dtype=np.float32)
    c = np.asarray(rgb, dtype=np.float32)
    pm = c if premul else premultiply(c, a)

    pad = max(1, int(np.ceil(sigma * 3)))
    pm_p = np.pad(pm, ((pad, pad), (pad, pad), (0, 0)), mode="constant")
    a_p = np.pad(a, ((pad, pad), (pad, pad)), mode="constant")

    pm_b = cv2.GaussianBlur(pm_p, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)
    a_b = cv2.GaussianBlur(a_p, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)

    pm_b = pm_b[pad:pad + a.shape[0], pad:pad + a.shape[1]]
    a_b = a_b[pad:pad + a.shape[0], pad:pad + a.shape[1]]
    return unpremultiply(pm_b, a_b), a_b.astype(np.float32)


def blur_naive(rgb, alpha, sigma):
    """The wrong way, kept so the fault can be MEASURED rather than asserted."""
    import cv2

    if sigma <= 0:
        return np.asarray(rgb, dtype=np.float32), np.asarray(alpha, dtype=np.float32)
    c = cv2.GaussianBlur(np.asarray(rgb, dtype=np.float32), (0, 0), sigma)
    a = cv2.GaussianBlur(np.asarray(alpha, dtype=np.float32), (0, 0), sigma)
    return c, a


# ---------------------------------------------------------------- file io


def _ext(path):
    return os.path.splitext(str(path))[1].lower()


def read_image(path, transfer="srgb"):
    """Read a still. RGB float32 0..1, alpha kept STRAIGHT, transfer named.

    PNG and TIFF are read at their real bit depth. A 16 bit file read down to 8
    is a silent downscale of the deliverable, which this skill refuses to do.
    """
    import cv2

    path = str(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"no such image: {path}")
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise RuntimeError(f"could not read {path} as an image")
    if raw.dtype == np.uint16:
        bits, scale = 16, 65535.0
    elif raw.dtype == np.uint8:
        bits, scale = 8, 255.0
    else:
        bits, scale = 32, 1.0
    arr = raw.astype(np.float32) / scale

    alpha = None
    if arr.ndim == 2:
        rgb = np.repeat(arr[..., None], 3, axis=2)
    elif arr.shape[2] == 4:
        # OpenCV hands back BGRA. Convert ONCE, here, at the door.
        rgb = arr[..., 2::-1].copy()
        alpha = arr[..., 3].copy()
    else:
        rgb = arr[..., 2::-1].copy()
    return Image(rgb, transfer, path, f"cv2:imread:unchanged:{bits}bit->rgb",
                 alpha=alpha, premul=False, bits=bits)


def write_image(path, img, bits=None, transfer=None):
    """Write a still. The transfer is applied on the way out and reported."""
    import cv2

    out = img if transfer is None else img.as_transfer(transfer)
    if out.premul and out.alpha is not None:
        out = out.copy()
        out.to_straight()
    bits = bits or (16 if _ext(path) in (".png", ".tif", ".tiff") and out.bits >= 16 else 8)
    scale = 65535.0 if bits == 16 else 255.0
    dtype = np.uint16 if bits == 16 else np.uint8
    rgb = np.clip(out.rgb, 0.0, 1.0)
    if out.alpha is not None:
        bgra = np.concatenate([rgb[..., 2::-1],
                               np.clip(out.alpha, 0.0, 1.0)[..., None]], axis=2)
        data = np.rint(bgra * scale).astype(dtype)
    else:
        data = np.rint(rgb[..., 2::-1] * scale).astype(dtype)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if not cv2.imwrite(str(path), data):
        raise RuntimeError(f"could not write {path}")
    return {"path": os.path.abspath(str(path)), "bits": bits,
            "transfer": out.transfer, "raster": f"{out.width}x{out.height}",
            "alpha": out.alpha is not None}


# ---------------------------------------------------------------- clip io


def clip_info(path):
    """Raster, rate, frame count and pixel format, read off the FILE."""
    data = C.ffprobe_json(path, ["-select_streams", "v:0", "-show_streams",
                                 "-show_format"])
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"{path} has no video stream")
    s = streams[0]
    r = C.rate(s.get("r_frame_rate") or s.get("avg_frame_rate") or "25")
    nb = s.get("nb_frames")
    try:
        count = int(nb)
    except (TypeError, ValueError):
        dur = float((data.get("format") or {}).get("duration") or 0.0)
        count = int(round(dur * float(r))) if dur else 0
    return {"path": os.path.abspath(str(path)),
            "width": int(s["width"]), "height": int(s["height"]),
            "rate": r, "rate_str": C.rate_str(r), "frames": count,
            "pix_fmt": s.get("pix_fmt"),
            "declared_bits": C.pix_depth(s.get("pix_fmt")),
            "colour": {"primaries": s.get("color_primaries"),
                       "transfer": s.get("color_transfer"),
                       "matrix": s.get("color_space"),
                       "range": s.get("color_range")}}


def read_frames(path, start=0, count=None, step=1, scale=None, bits=8):
    """Yield (index, Image) decoded through ONE named path.

    Frames come out through `ffmpeg -f rawvideo`, so the decode path is a fact
    that can be written down, not a library's private choice. `bits` picks
    rgb24 or rgb48le; 8 is enough to track on and never enough to deliver from.
    """
    info = clip_info(path)
    w, h = info["width"], info["height"]
    if scale:
        w, h = int(scale[0]), int(scale[1])
    pix = "rgb48le" if bits == 16 else "rgb24"
    per = w * h * 3 * (2 if bits == 16 else 1)
    dtype = np.uint16 if bits == 16 else np.uint8
    maxv = 65535.0 if bits == 16 else 255.0

    C.need("ffmpeg")
    # Deliberately NO seek. Frames are walked from the head of the file and
    # counted, because on a spliced master a nominal timestamp seek lands on the
    # wrong picture: the joins break the timeline and the decoder's own frame
    # counter restarts where the stream parameters change. `prove.py seek` is
    # the tool for reaching a frame by its packet timestamps when that is really
    # what is wanted.
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path)]
    vf = []
    if scale:
        vf.append(f"scale={w}:{h}:flags=lanczos")
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-f", "rawvideo", "-pix_fmt", pix, "-"]
    decode_path = f"ffmpeg:rawvideo:{pix}"

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=per * 2)
    idx = 0
    emitted = 0
    try:
        while True:
            buf = proc.stdout.read(per)
            if not buf or len(buf) < per:
                break
            if idx >= start and (idx - start) % step == 0:
                arr = np.frombuffer(buf, dtype=dtype)
                if dtype is np.uint16:
                    arr = arr.astype(np.float32) / maxv
                else:
                    arr = arr.astype(np.float32) / maxv
                yield idx, Image(arr.reshape(h, w, 3), "srgb", path,
                                 decode_path, bits=bits)
                emitted += 1
                if count is not None and emitted >= count:
                    break
            idx += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait(timeout=30)


def frame_at(path, index, bits=8):
    """One frame, through the same path as `read_frames`, by walking to it.

    Deliberately not a timestamp seek. On a spliced master a nominal seek lands
    on the wrong frame; `prove.py seek` is the tool that reads the packet
    timestamps when a seek is really needed.
    """
    for _, img in read_frames(path, start=index, count=1, bits=bits):
        return img
    raise RuntimeError(f"{path} has no frame {index}")


def write_clip(path, frames, rate, source_audio=None, crf=16, transfer="srgb",
               pix_fmt="yuv420p"):
    """Encode an iterable of Images. Nothing is downscaled, ever."""
    C.need("ffmpeg")
    first = next(iter(frames), None)
    if first is None:
        raise ValueError("nothing to write")

    def _gen():
        yield first
        for f in frames:
            yield f

    w, h = first.width, first.height
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", f"{rate.numerator}/{rate.denominator}", "-i", "-"]
    if source_audio:
        cmd += ["-i", str(source_audio), "-map", "0:v", "-map", "1:a?",
                "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
            "-pix_fmt", pix_fmt, "-colorspace", "bt709", "-color_primaries",
            "bt709", "-color_trc", "bt709", "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    for img in _gen():
        enc = img.as_transfer(transfer)
        data = np.rint(np.clip(enc.rgb, 0, 1) * 255.0).astype(np.uint8)
        proc.stdin.write(data.tobytes())
        n += 1
    proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.read().decode()[:400]}")
    return {"path": os.path.abspath(str(path)), "frames": n,
            "raster": f"{w}x{h}", "transfer_written": transfer}


# ---------------------------------------------------------------- grain


def grain_extract(img, sigma=1.6):
    """The plate's own grain: the plate minus a denoised copy of it.

    Returned in LINEAR light as a signed residual, plus the per channel standard
    deviation and the luminance response, because grain on a real plate is
    density dependent: the residual in a deep shadow is not the same size as the
    residual in a highlight, and a flat grain plate laid over an insert reads as
    video noise rather than as the plate.
    """
    import cv2

    lin = img.as_linear()
    base = cv2.GaussianBlur(lin.rgb, (0, 0), sigma)
    resid = (lin.rgb - base).astype(np.float32)
    luma = linear_luma(base)
    bands = []
    edges = [0.0, 0.05, 0.15, 0.35, 0.7, 1.01]
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (luma >= lo) & (luma < hi)
        if sel.sum() < 64:
            continue
        bands.append({"luma_lo": lo, "luma_hi": hi, "pixels": int(sel.sum()),
                      "sigma_rgb": [float(resid[..., c][sel].std()) for c in range(3)]})
    return {"residual": resid,
            "sigma_rgb": [float(resid[..., c].std()) for c in range(3)],
            "bands": bands, "denoise_sigma": sigma,
            "source": img.source, "decode_path": img.decode_path}


def grain_apply(img_linear_rgb, residual, mask=None, gain=1.0):
    """Add a measured residual back, optionally only where the insert is.

    Added, not multiplied: the residual was measured as a difference and it goes
    back as one. Multiplying it would scale the grain by the insert's own level
    a second time, and the insert has already been levelled to the plate.
    """
    r = np.asarray(residual, dtype=np.float32) * float(gain)
    if r.shape[:2] != img_linear_rgb.shape[:2]:
        import cv2
        r = cv2.resize(r, (img_linear_rgb.shape[1], img_linear_rgb.shape[0]),
                       interpolation=cv2.INTER_LINEAR)
    if mask is not None:
        r = r * np.asarray(mask, dtype=np.float32)[..., None]
    return (np.asarray(img_linear_rgb, dtype=np.float32) + r).astype(np.float32)


# ---------------------------------------------------------------- misc


def resize(arr, size, interp=None):
    import cv2
    if interp is None:
        cur = arr.shape[1] * arr.shape[0]
        interp = cv2.INTER_AREA if size[0] * size[1] < cur else cv2.INTER_LANCZOS4
    return cv2.resize(np.asarray(arr, dtype=np.float32), (int(size[0]), int(size[1])),
                      interpolation=interp)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=str)
    return os.path.abspath(path)
