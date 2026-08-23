#!/usr/bin/env python3
"""Colour difference maths, in the standard library.

Only what type on picture needs: sRGB to CIE Lab, and CIEDE2000 between two Lab
values. No numpy, so the legibility rule can be checked with nothing installed.

Why Lab and not luma. What hides a glyph is ground the same COLOUR as the ink,
not ground the same brightness. A luma only check cannot choose between two
inks: on a real pale film it called white and turquoise both failures, while
the measurement that matched what the eye saw put white at a loss of 17 to 98
per cent of its glyph surround and turquoise never below dE 39.

The CIEDE2000 implementation is the Sharma, Wu and Dalal 2005 formulation, and
selftest.py checks it against the twelve rows of the published reference table
that the colorgrade skill on this machine is already checked against.
"""
from __future__ import annotations

import math

# sRGB primaries to CIE XYZ, D65 white, IEC 61966-2-1.
_M = ((0.4124564, 0.3575761, 0.1804375),
      (0.2126729, 0.7151522, 0.0721750),
      (0.0193339, 0.1191920, 0.9503041))
_WHITE = (0.95047, 1.00000, 1.08883)


def hex_to_rgb(value):
    """'#27E2CC' or '27e2cc' to three floats in 0..1."""
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"Not a colour: {value}. Use #RRGGBB.")
    return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_xyz(rgb):
    r, g, b = (srgb_to_linear(c) for c in rgb)
    return tuple(row[0] * r + row[1] * g + row[2] * b for row in _M)


def xyz_to_lab(xyz):
    def f(t):
        return t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (841.0 / 108.0) * t + 4.0 / 29.0
    fx, fy, fz = (f(v / w) for v, w in zip(xyz, _WHITE))
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def rgb_to_lab(rgb):
    """rgb in 0..1, sRGB encoded."""
    return xyz_to_lab(rgb_to_xyz(rgb))


def hex_to_lab(value):
    return rgb_to_lab(hex_to_rgb(value))


def relative_luminance(rgb):
    """WCAG relative luminance, for the luma only comparison."""
    r, g, b = (srgb_to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb_a, rgb_b):
    """WCAG contrast ratio. Kept only to SHOW what a luma check misses."""
    la, lb = relative_luminance(rgb_a), relative_luminance(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def delta_e_2000(lab1, lab2, kL=1.0, kC=1.0, kH=1.0):
    """CIEDE2000, Sharma, Wu and Dalal 2005."""
    L1, a1, b1 = (float(x) for x in lab1)
    L2, a2, b2 = (float(x) for x in lab2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2.0
    G = 0.5 * (1.0 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25.0 ** 7))) if Cb > 0 else 0.5
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360.0 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360.0 if (a2p or b2) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0
    dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2.0)
    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360.0) / 2.0
    else:
        hbp = (h1p + h2p - 360.0) / 2.0
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dTheta = 30.0 * math.exp(-(((hbp - 275.0) / 25.0) ** 2))
    Rc = 2.0 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7)) if Cbp > 0 else 0.0
    Sl = 1.0 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1.0 + 0.045 * Cbp
    Sh = 1.0 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dTheta)) * Rc
    return math.sqrt((dLp / (kL * Sl)) ** 2 + (dCp / (kC * Sc)) ** 2
                     + (dHp / (kH * Sh)) ** 2
                     + Rt * (dCp / (kC * Sc)) * (dHp / (kH * Sh)))
