"""Colour maths, the grade model, and LUT baking.

Everything the grader does to a pixel lives in apply_grade(). It is a pure
function of RGB, which is what makes the whole grade bakeable into a .cube
LUT that ffmpeg and Resolve apply identically.

Working spaces:
  code   Rec.709 display code value, 0..1. What comes out of the file.
  lin    scene linear, obtained with the BT.1886 (gamma 2.4) inverse EOTF.
  log    Cineon log, where contrast and tone shaping behave like film.

Order of the chain is deliberate and matches how a colourist works:
  normalise (exposure, white balance, black/white point) happens in linear,
  then the look (contrast, tone, colour) happens mostly in log, then the
  picture is encoded back to code values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

import numpy as np

EPS = 1e-6

# ---------------------------------------------------------------- transfer

GAMMA = 2.4


def code_to_lin(v):
    """Rec.709 code value to scene linear (BT.1886 pure 2.4 gamma)."""
    v = np.asarray(v, dtype=np.float32)
    return np.sign(v) * np.power(np.abs(v), GAMMA, dtype=np.float32)


def lin_to_code(lin):
    """Scene linear back to Rec.709 code value."""
    lin = np.asarray(lin, dtype=np.float32)
    return np.sign(lin) * np.power(np.abs(lin), 1.0 / GAMMA, dtype=np.float32)


# Cineon log. Mid grey 0.18 linear lands on 0.4573, black on 0.0928.
_CIN_OFF = 0.0108
_CIN_GAIN = 1.0 - _CIN_OFF


def lin_to_log(lin):
    lin = np.maximum(np.asarray(lin, dtype=np.float32), 0.0)
    return ((685.0 + 300.0 * np.log10(lin * _CIN_GAIN + _CIN_OFF)) / 1023.0).astype(np.float32)


def log_to_lin(c):
    c = np.asarray(c, dtype=np.float32)
    return ((np.power(10.0, (c * 1023.0 - 685.0) / 300.0) - _CIN_OFF) / _CIN_GAIN).astype(np.float32)


# ---------------------------------------------------------------- colour spaces

RGB_TO_XYZ = np.array([
    [0.4123907992659595, 0.3575843393838780, 0.1804807884018343],
    [0.2126390058715104, 0.7151686787677559, 0.0721923153607337],
    [0.0193308187155918, 0.1191947797946259, 0.9505321522496608],
], dtype=np.float32)

XYZ_TO_RGB = np.linalg.inv(RGB_TO_XYZ).astype(np.float32)

# Rec.709 luma weights, used for luma preserving saturation.
LUMA_W = np.array([0.2126390058715104, 0.7151686787677559, 0.0721923153607337], dtype=np.float32)

D65 = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)


def luma(lin):
    return np.tensordot(lin, LUMA_W, axes=([-1], [0])).astype(np.float32)


def lin_to_lab(lin):
    """Scene linear Rec.709 to CIE Lab (D65). Input may exceed 1."""
    xyz = np.tensordot(np.maximum(lin, 0.0), RGB_TO_XYZ.T, axes=([-1], [0]))
    t = xyz / D65
    d = 6.0 / 29.0
    f = np.where(t > d ** 3, np.cbrt(np.maximum(t, EPS)), t / (3 * d * d) + 4.0 / 29.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1).astype(np.float32)


def lab_to_lin(lab):
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    d = 6.0 / 29.0

    def finv(f):
        return np.where(f > d, f ** 3, 3 * d * d * (f - 4.0 / 29.0))

    xyz = np.stack([finv(fx), finv(fy), finv(fz)], axis=-1) * D65
    return np.tensordot(xyz, XYZ_TO_RGB.T, axes=([-1], [0])).astype(np.float32)


def rgb_to_hsv(rgb):
    """rgb in any positive range. Returns h in degrees 0..360, s and v."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.max(rgb, axis=-1)
    mn = np.min(rgb, axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > EPS
    with np.errstate(invalid="ignore", divide="ignore"):
        rm = np.where(nz & (mx == r), ((g - b) / np.maximum(d, EPS)) % 6.0, 0.0)
        gm = np.where(nz & (mx == g), ((b - r) / np.maximum(d, EPS)) + 2.0, 0.0)
        bm = np.where(nz & (mx == b), ((r - g) / np.maximum(d, EPS)) + 4.0, 0.0)
    h = (rm + gm + bm) * 60.0
    h = np.where(h < 0, h + 360.0, h)
    s = np.where(mx > EPS, d / np.maximum(mx, EPS), 0.0)
    return h.astype(np.float32), s.astype(np.float32), mx.astype(np.float32)


def hue_weight(h, centre, width):
    """Smooth 0..1 weight peaking at hue `centre`, falling to 0 at `width` away.

    Wraps correctly around 360. Raised cosine, so no hard edges anywhere.
    """
    d = np.abs(((h - centre + 180.0) % 360.0) - 180.0)
    t = np.clip(1.0 - d / max(width, EPS), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def lab_hue(lin):
    """Lab hue angle in degrees, -180..180. The space `hue_shifts` rotates in.

    This is NOT the HSV hue. The two disagree by tens of degrees and they
    disagree by a different amount for every colour, so a centre measured in
    one and gated in the other selects the wrong pixels. `hue_shifts` takes a
    `space` key precisely so the two can never be mixed up silently again.
    """
    lab = lin_to_lab(lin)
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1])).astype(np.float32)


# Skin sits on the vectorscope I axis. Keith Jack, Video Demystified, gives
# 116 to 126 degrees of phase for skin correction. In HSV hue terms that is
# the orange band around 25 degrees, which is what we actually gate on here.
SKIN_HUE = 25.0
SKIN_WIDTH = 22.0


# ---------------------------------------------------------------- parameters


@dataclass
class Balance:
    """Per shot normalisation. Derived by measurement, not by taste."""

    exposure: float = 0.0          # stops, applied in linear
    gain_r: float = 1.0            # white balance channel gains
    gain_g: float = 1.0
    gain_b: float = 1.0
    lift: float = 0.0              # linear offset, sets the black point
    white: float = 1.0             # linear gain, sets the white point
    lab_shift_a: float = 0.0       # residual shot match, applied in Lab
    lab_shift_b: float = 0.0
    lab_scale_L: float = 1.0


@dataclass
class Look:
    """The creative layer. One look for the whole video, by design."""

    name: str = "neutral"
    contrast: float = 1.0          # slope in log space around the pivot
    pivot: float = 0.4573          # Cineon mid grey
    toe: float = 0.0               # shadow softening, 0..1
    shoulder: float = 0.0          # highlight rolloff, 0..1
    saturation: float = 1.0        # global chroma scale
    sat_shadow: float = 1.0        # chroma scale at black, film desaturates
    sat_highlight: float = 1.0     # chroma scale at white
    shadow_tint: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    highlight_tint: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    tint_falloff: float = 2.0      # how fast the tints hand over
    crosstalk: float = 0.0         # film like channel bleed, 0..0.3
    # [{centre, width, shift, sat, space}]. centre and width gate in `space`,
    # "hsv" (default) or "lab". shift is ALWAYS a Lab hue rotation in degrees.
    # Gate a measured centre in the space it was measured in, or it aims at a
    # colour that is not there. See the note in apply_look.
    hue_shifts: list = field(default_factory=list)
    protect_skin: float = 1.0      # 1 fully protects skin from hue_shifts, HSV gated
    black_offset: float = 0.0      # code value lift at the very bottom
    # Soft clip near white, in code space. Real protection: 1.0 rolls off from
    # the knee, 0.4 rolls off from 40 per cent of the way up, nothing clips at
    # any setting. Below 1.0 used to leak the overshoot through and hard clip.
    highlight_rolloff: float = 0.0
    gamut_limit: float = 0.0       # 0 disables. 1.4 to 1.8 is the useful band
    gamut_threshold: float = 0.85  # below this distance nothing is touched
    gamut_power: float = 1.2       # 1 is gentle and wide, 4 is abrupt and local


@dataclass
class Grade:
    balance: Balance = field(default_factory=Balance)
    look: Look = field(default_factory=Look)

    def to_json(self):
        return json.dumps({"balance": asdict(self.balance), "look": asdict(self.look)}, indent=2)

    @staticmethod
    def from_dict(d):
        return Grade(balance=Balance(**d.get("balance", {})), look=Look(**d.get("look", {})))


# ---------------------------------------------------------------- the chain


def _soft_shoulder(x, amount, knee=0.75):
    """Compress everything above `knee` into the space up to 1, smoothly.

    `amount` moves the knee. It does not blend the result against the input,
    and that distinction is the whole point. The compressed curve is asymptotic
    to 1, so lerping it against the identity puts the identity's unbounded slope
    straight back into the output and the caller clips regardless. Measured on
    the old form at knee 0.80: amount 0.35 passed 1.0 for any input above 1.034
    and reached 2.95 at input 4.0. So `highlight_rolloff: 0.35` bought three per
    cent of headroom and then hard clipped, and only amount 1.0 was bounded at
    all. Every look in the library shipped a value between 0.2 and 0.6.

    Now amount 0 leaves the signal alone, amount 1 rolls off from `knee`, and
    anything between rolls off from a knee that fraction of the way up. Output
    stays under 1 for every finite input at every amount, the slope at the knee
    is 1 so there is no kink, and amount 1.0 reproduces the old curve exactly.
    """
    if amount <= 0:
        return x
    a = min(float(amount), 1.0)
    k = knee + (1.0 - knee) * (1.0 - a)
    head = max(1.0 - k, EPS)
    over = np.maximum(x - k, 0.0)
    return np.where(x > k, k + head * (1.0 - np.exp(-over / head)), x).astype(np.float32)


def _soft_toe(x, amount, knee=0.25):
    """Mirror of `_soft_shoulder`, holding the bottom instead of the top.

    Same defect, same fix: the old form let `amount` blend against the identity,
    so at amount 0.15 the output went negative for any input under -0.016 and
    reached -1.70 at input -2.0. A toe that crushes is not a toe.
    """
    if amount <= 0:
        return x
    a = min(float(amount), 1.0)
    k = knee * a
    head = max(k, EPS)
    under = np.maximum(k - x, 0.0)
    return np.where(x < k, k - head * (1.0 - np.exp(-under / head)), x).astype(np.float32)


def apply_balance(lin, b: Balance):
    lin = lin * np.float32(2.0 ** b.exposure)
    lin = lin * np.array([b.gain_r, b.gain_g, b.gain_b], dtype=np.float32)
    lin = (lin - np.float32(b.lift)) * np.float32(b.white)
    return lin


def apply_lab_match(lin, b: Balance):
    if b.lab_shift_a == 0.0 and b.lab_shift_b == 0.0 and b.lab_scale_L == 1.0:
        return lin
    lab = lin_to_lab(lin)
    lab[..., 0] = np.clip(lab[..., 0] * b.lab_scale_L, 0.0, 200.0)
    lab[..., 1] += b.lab_shift_a
    lab[..., 2] += b.lab_shift_b
    return lab_to_lin(lab)


def apply_look(lin, k: Look):
    # ---- tone, in log where contrast behaves ----
    lg = lin_to_log(np.maximum(lin, 0.0))
    if k.contrast != 1.0:
        lg = (lg - k.pivot) * k.contrast + k.pivot
    if k.toe > 0:
        lg = _soft_toe(lg, k.toe)
    if k.shoulder > 0:
        lg = _soft_shoulder(lg, k.shoulder)
    lin = log_to_lin(lg)
    lin = np.maximum(lin, 0.0)

    # ---- film like channel bleed ----
    if k.crosstalk > 0:
        c = k.crosstalk
        m = np.array([
            [1.0 - 2 * c, c, c],
            [c, 1.0 - 2 * c, c],
            [c, c, 1.0 - 2 * c],
        ], dtype=np.float32)
        lin = np.tensordot(lin, m.T, axes=([-1], [0]))
        lin = np.maximum(lin, 0.0)

    # ---- split tone ----
    st = np.asarray(k.shadow_tint, dtype=np.float32)
    ht = np.asarray(k.highlight_tint, dtype=np.float32)
    if np.any(st != 0) or np.any(ht != 0):
        y = np.clip(lin_to_log(lin.mean(axis=-1, keepdims=True)), 0.0, 1.0)
        w_hi = np.power(y, k.tint_falloff, dtype=np.float32)
        w_lo = np.power(1.0 - y, k.tint_falloff, dtype=np.float32)
        lin = lin + st * w_lo * 0.1 + ht * w_hi * 0.1
        lin = np.maximum(lin, 0.0)

    # ---- saturation, luma preserving, with a shadow/highlight ramp ----
    if k.saturation != 1.0 or k.sat_shadow != 1.0 or k.sat_highlight != 1.0:
        y = luma(lin)[..., None]
        ylog = np.clip(lin_to_log(y), 0.0, 1.0)
        ramp = np.where(
            ylog < 0.4573,
            k.sat_shadow + (1.0 - k.sat_shadow) * (ylog / 0.4573),
            1.0 + (k.sat_highlight - 1.0) * ((ylog - 0.4573) / (1.0 - 0.4573)),
        ).astype(np.float32)
        s = np.float32(k.saturation) * ramp
        lin = y + (lin - y) * s
        lin = np.maximum(lin, 0.0)

    # ---- targeted hue work ----
    #
    # Two hue conventions meet here and they must not be confused. The gate
    # ("which pixels") defaults to the HSV hue, because that is what the
    # library looks were authored against. The rotation ("by how much") is
    # always the Lab hue angle, because Lab is where a rotation is perceptually
    # even. They are different numbers for the same colour: teal reads 192 in
    # HSV and -151 in Lab, lime 104 and +106, skin 25 and +54.
    #
    # A centre measured in Lab and gated in HSV therefore selects a colour that
    # is not in the picture, and the shift silently does nothing at all. That
    # happened on a real job: four holds, all inert, and the graded film printed
    # numbers identical to no holds. Set `"space": "lab"` on an entry to gate in
    # the same space it rotates in, which is what a measured centre wants.
    if k.hue_shifts:
        h, s, v = rgb_to_hsv(lin)
        lab = lin_to_lab(lin)
        a, bb = lab[..., 1], lab[..., 2]
        chroma = np.hypot(a, bb)
        angle = np.degrees(np.arctan2(bb, a))
        skin_guard = 1.0
        if k.protect_skin > 0:
            skin_guard = 1.0 - k.protect_skin * hue_weight(h, SKIN_HUE, SKIN_WIDTH)
        total_rot = np.zeros_like(angle)
        total_sat = np.ones_like(angle)
        for hs in k.hue_shifts:
            space = str(hs.get("space", "hsv")).lower()
            if space not in ("hsv", "lab"):
                raise ValueError(f"hue_shift space must be 'hsv' or 'lab', got {space!r}")
            gate = angle if space == "lab" else h
            w = hue_weight(gate, float(hs.get("centre", 0.0)), float(hs.get("width", 30.0)))
            w = w * skin_guard
            total_rot = total_rot + w * float(hs.get("shift", 0.0))
            total_sat = total_sat * (1.0 + w * (float(hs.get("sat", 1.0)) - 1.0))
        ang2 = np.radians(angle + total_rot)
        chroma = chroma * total_sat
        lab[..., 1] = np.cos(ang2) * chroma
        lab[..., 2] = np.sin(ang2) * chroma
        lin = np.maximum(lab_to_lin(lab), 0.0)

    return lin


def gamut_compress(lin, limit=0.0, power=1.2, threshold=0.85):
    """Fold out of gamut colour back inside, smoothly, keeping hue.

    Why this exists, measured rather than assumed: a strong look pushes
    saturated colour past the edge of Rec.709, which sends a channel negative.
    Clamping that at zero puts a crease in the transfer, and a 3D LUT cannot
    represent a crease. Baking teal_orange to a 33 cube with a hard clamp gave
    errors up to 39 code levels of 255 on saturated purples. Compressing
    instead of clamping removes the crease, so the LUT matches the maths, and
    it is also what a film print does rather than clipping.

    Method is the ACES gamut compression shape: distance from the achromatic
    axis, per channel, compressed from [threshold, limit] onto [threshold, 1]
    with a C1 join at the threshold. Distance 1 means a channel sitting exactly
    on zero, so anything above 1 is out of gamut.

    Off by default (limit 0), because it necessarily touches very saturated
    colours that were still legal, and a neutral grade must stay bit exact.
    Looks that push chroma switch it on.
    """
    if limit <= threshold or threshold >= 1.0:
        return np.maximum(lin, 0.0)
    ach = np.max(lin, axis=-1, keepdims=True)
    safe = np.where(np.abs(ach) < EPS, EPS, ach)
    dist = np.where(np.abs(ach) < EPS, 0.0, (ach - lin) / np.abs(safe))

    p = float(power)
    scl = (limit - threshold) / np.power(
        np.power((1.0 - threshold) / (limit - threshold), -p) - 1.0, 1.0 / p)
    nd = np.maximum(dist - threshold, 0.0) / scl
    comp = threshold + scl * nd / np.power(1.0 + np.power(nd, p), 1.0 / p)
    dist = np.where(dist < threshold, dist, comp)

    out = ach - dist * np.abs(safe)
    return np.maximum(np.where(np.abs(ach) < EPS, lin, out), 0.0).astype(np.float32)


def apply_display(lin, k: Look):
    lin = gamut_compress(lin, limit=k.gamut_limit, power=k.gamut_power,
                         threshold=k.gamut_threshold)
    code = lin_to_code(np.maximum(lin, 0.0))
    if k.black_offset != 0.0:
        code = code * (1.0 - k.black_offset) + k.black_offset
    if k.highlight_rolloff > 0:
        code = _soft_shoulder(code, k.highlight_rolloff, knee=0.80)
    return np.clip(code, 0.0, 1.0)


def apply_grade(code, g: Grade):
    """Rec.709 code value in, Rec.709 code value out. Pure per pixel."""
    code = np.asarray(code, dtype=np.float32)
    lin = code_to_lin(np.clip(code, 0.0, 1.0))
    lin = apply_balance(lin, g.balance)
    lin = apply_lab_match(lin, g.balance)
    lin = apply_look(lin, g.look)
    return apply_display(lin, g.look)


# ---------------------------------------------------------------- LUT


def identity_lattice(size: int):
    """Shape (size**3, 3), ordered the way a .cube file wants: R fastest."""
    ax = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, g, r = np.meshgrid(ax, ax, ax, indexing="ij")
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=-1)


def bake_lut(g: Grade, size: int = 33):
    return apply_grade(identity_lattice(size), g)


def write_cube(path, lut, size, title="colorgrade"):
    lut = np.clip(np.asarray(lut, dtype=np.float32), 0.0, 1.0)
    with open(path, "w") as f:
        f.write(f'TITLE "{title}"\n')
        f.write(f"LUT_3D_SIZE {size}\n")
        f.write("DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n\n")
        for row in lut:
            f.write(f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n")


def read_cube(path):
    size = None
    vals = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.upper().startswith("LUT_3D_SIZE"):
                size = int(line.split()[-1])
                continue
            if line.upper().startswith(("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE")):
                continue
            parts = line.split()
            if len(parts) == 3:
                try:
                    vals.append([float(x) for x in parts])
                except ValueError:
                    continue
    return np.asarray(vals, dtype=np.float32), size


def apply_lut_trilinear(code, lut, size):
    """Reference application of a baked LUT, used to measure bake error."""
    code = np.clip(np.asarray(code, dtype=np.float32), 0.0, 1.0)
    grid = lut.reshape(size, size, size, 3)   # [b, g, r, ch]
    pos = code * (size - 1)
    i0 = np.floor(pos).astype(np.int32)
    i0 = np.clip(i0, 0, size - 2)
    fr = pos - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fx, fy, fz = fr[..., 0:1], fr[..., 1:2], fr[..., 2:3]
    out = np.zeros(code.shape, dtype=np.float32)
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                w = ((fx if dr else 1 - fx) * (fy if dg else 1 - fy) * (fz if db else 1 - fz))
                out += w * grid[b0 + db, g0 + dg, r0 + dr]
    return out


# ---------------------------------------------------------------- looks


def load_look(path_or_name, looks_dir):
    import os
    p = path_or_name
    if not os.path.isfile(p):
        p = os.path.join(looks_dir, f"{path_or_name}.json")
    with open(p) as f:
        d = json.load(f)
    d.pop("description", None)
    d.pop("reference", None)
    return Look(**d)
