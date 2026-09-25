#!/usr/bin/env python3
"""Convert an SVG (a logo, an icon, a traced drawing) into RML shapes.

The Rive editor imports SVG; the CLI does not (SVGAsset is editor-only and
stripped on export), so a vector has to become native shapes before a
project can use it. This writes each SVG element as a Shape holding one
PointsPath per subpath, with its fill and stroke:

  * paths (every command, arcs and quadratics converted to cubics exactly
    or by the standard arc decomposition), rect (with rx/ry), circle,
    ellipse, line, polyline, polygon, and <use> of any of them
  * transforms (matrix, translate, scale, rotate, skewX/Y) baked into the
    points, so the output has no transforms of its own
  * fill/stroke colours with fill-opacity, stroke-opacity and opacity,
    fill-rule, stroke width/caps/joins/dashes, linear and radial gradients
    (objectBoundingBox or userSpaceOnUse), presentation attributes, style=
    and simple <style> rules (element, .class, #id)
  * SVG paints later elements on top and Rive paints the FIRST sibling on
    top, so the order is reversed on the way out

Not converted (reported): text (outline it first), embedded images, masks,
clip paths, filters, gradient focal points and skewed gradients.

  rive_svg.py logo.svg -o logo.rml                         a fragment to paste
  rive_svg.py logo.svg --project ~/work/logo --size 1920x1080 --fit 900
  rive_svg.py logo.svg -o logo.rml --fit 600 --center 960,540 --name Mark
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

KAPPA = 0.5522847498307936  # cubic approximation of a quarter circle
EPS = 1e-6

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

Matrix = tuple  # (a, b, c, d, e, f): x' = a*x + c*y + e ; y' = b*x + d*y + f
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m: Matrix, n: Matrix) -> Matrix:
    """m then... no: returns m * n, i.e. apply n first, then m."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def apply(m: Matrix, p: tuple[float, float]) -> tuple[float, float]:
    a, b, c, d, e, f = m
    x, y = p
    return (a * x + c * y + e, b * x + d * y + f)


def scale_of(m: Matrix) -> float:
    a, b, c, d, _, _ = m
    return math.sqrt(abs(a * d - b * c)) or 1.0


_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"


def numbers(text: str) -> list[float]:
    return [float(v) for v in re.findall(_NUM, text or "")]


def parse_transform(text: str | None) -> Matrix:
    m = IDENTITY
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", text or ""):
        v = numbers(args)
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0] if v else 0, v[1] if len(v) > 1 else 0)
        elif name == "scale":
            sx = v[0] if v else 1
            t = (sx, 0, 0, v[1] if len(v) > 1 else sx, 0, 0)
        elif name == "rotate":
            a = math.radians(v[0] if v else 0)
            r = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) >= 3:
                t = mul(mul((1, 0, 0, 1, v[1], v[2]), r), (1, 0, 0, 1, -v[1], -v[2]))
            else:
                t = r
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(v[0] if v else 0)), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(v[0] if v else 0)), 0, 1, 0, 0)
        else:
            continue
        m = mul(m, t)
    return m


@dataclass
class Subpath:
    start: tuple[float, float]
    segments: list = field(default_factory=list)  # ("L", p) or ("C", c1, c2, p)
    closed: bool = False

    def end(self) -> tuple[float, float]:
        return self.segments[-1][-1] if self.segments else self.start

    def transformed(self, m: Matrix) -> "Subpath":
        segs = [(s[0], *[apply(m, p) for p in s[1:]]) for s in self.segments]
        return Subpath(apply(m, self.start), segs, self.closed)

    def points(self) -> list[tuple[float, float]]:
        pts = [self.start]
        for s in self.segments:
            pts.extend(s[1:])
        return pts


def _arc_to_cubics(p0, rx, ry, phi_deg, large, sweep, p1):
    """SVG endpoint arc to cubic segments (SVG 1.1 implementation notes F.6)."""
    x0, y0 = p0
    x1, y1 = p1
    if (abs(x0 - x1) < EPS and abs(y0 - y1) < EPS):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < EPS or ry < EPS:
        return [("L", p1)]
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x0 - x1) / 2, (y0 - y1) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if bool(large) == bool(sweep):
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x0 + x1) / 2
    cy = sp * cxp + cp * cyp + (y0 + y1) / 2

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2) - 1e-9)))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    out = []
    for i in range(n):
        a0 = t1 + i * step
        a1 = a0 + step
        e0 = (math.cos(a0), math.sin(a0))
        e1 = (math.cos(a1), math.sin(a1))
        q0 = (e0[0] - k * e0[1], e0[1] + k * e0[0])
        q1 = (e1[0] + k * e1[1], e1[1] - k * e1[0])

        def to_world(u):
            ux, uy = u[0] * rx, u[1] * ry
            return (cp * ux - sp * uy + cx, sp * ux + cp * uy + cy)

        end = to_world(e1) if i < n - 1 else (x1, y1)
        out.append(("C", to_world(q0), to_world(q1), end))
    return out


def parse_path(d: str) -> list[Subpath]:
    tokens = re.findall(r"[MmZzLlHhVvCcSsQqTtAa]|" + _NUM, d or "")
    subs: list[Subpath] = []
    cur: Subpath | None = None
    pen = (0.0, 0.0)
    start = (0.0, 0.0)
    last_c2 = None   # reflection for S
    last_q = None    # reflection for T
    i = 0
    cmd = None

    def take(n):
        nonlocal i
        if i + n > len(tokens) or any(re.fullmatch(r"[A-Za-z]", tokens[i + j]) for j in range(n)):
            raise ValueError(f"path command {cmd} is missing numbers")
        vals = [float(tokens[i + j]) for j in range(n)]
        i += n
        return vals

    while i < len(tokens):
        if re.fullmatch(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur is not None:
                    if abs(cur.end()[0] - cur.start[0]) > EPS or abs(cur.end()[1] - cur.start[1]) > EPS:
                        cur.segments.append(("L", cur.start))
                    cur.closed = True
                    pen = start
                    cur = None
                last_c2 = last_q = None
                continue
        elif cmd is None:
            raise ValueError("path data starts with a number")
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = take(2)
            pen = (pen[0] + x, pen[1] + y) if rel else (x, y)
            start = pen
            cur = Subpath(pen)
            subs.append(cur)
            cmd = "l" if rel else "L"  # subsequent pairs are lines
            last_c2 = last_q = None
            continue
        if cur is None:  # drawing after a Z starts a new subpath at the pen
            cur = Subpath(pen)
            subs.append(cur)
            start = pen
        if c in "LHV":
            if c == "L":
                x, y = take(2)
                p = (pen[0] + x, pen[1] + y) if rel else (x, y)
            elif c == "H":
                (x,) = take(1)
                p = (pen[0] + x if rel else x, pen[1])
            else:
                (y,) = take(1)
                p = (pen[0], pen[1] + y if rel else y)
            cur.segments.append(("L", p))
            pen = p
            last_c2 = last_q = None
        elif c == "C":
            v = take(6)
            if rel:
                v = [v[0] + pen[0], v[1] + pen[1], v[2] + pen[0], v[3] + pen[1], v[4] + pen[0], v[5] + pen[1]]
            c1, c2, p = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
            cur.segments.append(("C", c1, c2, p))
            pen, last_c2, last_q = p, c2, None
        elif c == "S":
            v = take(4)
            if rel:
                v = [v[0] + pen[0], v[1] + pen[1], v[2] + pen[0], v[3] + pen[1]]
            c1 = (2 * pen[0] - last_c2[0], 2 * pen[1] - last_c2[1]) if last_c2 else pen
            c2, p = (v[0], v[1]), (v[2], v[3])
            cur.segments.append(("C", c1, c2, p))
            pen, last_c2, last_q = p, c2, None
        elif c in "QT":
            if c == "Q":
                v = take(4)
                if rel:
                    v = [v[0] + pen[0], v[1] + pen[1], v[2] + pen[0], v[3] + pen[1]]
                q, p = (v[0], v[1]), (v[2], v[3])
            else:
                v = take(2)
                p = (v[0] + pen[0], v[1] + pen[1]) if rel else (v[0], v[1])
                q = (2 * pen[0] - last_q[0], 2 * pen[1] - last_q[1]) if last_q else pen
            c1 = (pen[0] + 2 / 3 * (q[0] - pen[0]), pen[1] + 2 / 3 * (q[1] - pen[1]))
            c2 = (p[0] + 2 / 3 * (q[0] - p[0]), p[1] + 2 / 3 * (q[1] - p[1]))
            cur.segments.append(("C", c1, c2, p))
            pen, last_q, last_c2 = p, q, None
        elif c == "A":
            rx, ry, phi, large, sweep, x, y = take(7)
            p = (pen[0] + x, pen[1] + y) if rel else (x, y)
            cur.segments.extend(_arc_to_cubics(pen, rx, ry, phi, large, sweep, p))
            pen = p
            last_c2 = last_q = None
        else:
            raise ValueError(f"unknown path command {cmd}")
    return [s for s in subs if s.segments]


def ellipse_path(cx, cy, rx, ry) -> Subpath:
    kx, ky = rx * KAPPA, ry * KAPPA
    s = Subpath((cx + rx, cy))
    s.segments = [
        ("C", (cx + rx, cy + ky), (cx + kx, cy + ry), (cx, cy + ry)),
        ("C", (cx - kx, cy + ry), (cx - rx, cy + ky), (cx - rx, cy)),
        ("C", (cx - rx, cy - ky), (cx - kx, cy - ry), (cx, cy - ry)),
        ("C", (cx + kx, cy - ry), (cx + rx, cy - ky), (cx + rx, cy)),
    ]
    s.closed = True
    return s


def rect_path(x, y, w, h, rx=0.0, ry=0.0) -> Subpath:
    if rx <= 0 and ry <= 0:
        s = Subpath((x, y), [("L", (x + w, y)), ("L", (x + w, y + h)), ("L", (x, y + h)), ("L", (x, y))], True)
        return s
    rx = rx if rx > 0 else ry
    ry = ry if ry > 0 else rx
    rx, ry = min(rx, w / 2), min(ry, h / 2)
    kx, ky = rx * KAPPA, ry * KAPPA
    s = Subpath((x + rx, y))
    s.segments = [
        ("L", (x + w - rx, y)),
        ("C", (x + w - rx + kx, y), (x + w, y + ry - ky), (x + w, y + ry)),
        ("L", (x + w, y + h - ry)),
        ("C", (x + w, y + h - ry + ky), (x + w - rx + kx, y + h), (x + w - rx, y + h)),
        ("L", (x + rx, y + h)),
        ("C", (x + rx - kx, y + h), (x, y + h - ry + ky), (x, y + h - ry)),
        ("L", (x, y + ry)),
        ("C", (x, y + ry - ky), (x + rx - kx, y), (x + rx, y)),
    ]
    s.closed = True
    return s


# --------------------------------------------------------------------------
# paint
# --------------------------------------------------------------------------

NAMED = {
    "black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000", "blue": "0000ff",
    "yellow": "ffff00", "cyan": "00ffff", "aqua": "00ffff", "magenta": "ff00ff", "fuchsia": "ff00ff",
    "gray": "808080", "grey": "808080", "silver": "c0c0c0", "maroon": "800000", "olive": "808000",
    "lime": "00ff00", "teal": "008080", "navy": "000080", "purple": "800080", "orange": "ffa500",
    "gold": "ffd700", "pink": "ffc0cb", "brown": "a52a2a", "coral": "ff7f50", "crimson": "dc143c",
    "darkgray": "a9a9a9", "darkgrey": "a9a9a9", "lightgray": "d3d3d3", "lightgrey": "d3d3d3",
    "dimgray": "696969", "dimgrey": "696969", "gainsboro": "dcdcdc", "whitesmoke": "f5f5f5",
    "tomato": "ff6347", "salmon": "fa8072", "orangered": "ff4500", "indigo": "4b0082", "violet": "ee82ee",
    "turquoise": "40e0d0", "skyblue": "87ceeb", "steelblue": "4682b4", "royalblue": "4169e1",
    "slategray": "708090", "darkblue": "00008b", "darkgreen": "006400", "darkred": "8b0000",
    "beige": "f5f5dc", "ivory": "fffff0", "khaki": "f0e68c", "tan": "d2b48c", "chocolate": "d2691e",
    "firebrick": "b22222", "forestgreen": "228b22", "seagreen": "2e8b57", "midnightblue": "191970",
}


def parse_colour(text: str | None, current: str = "000000") -> tuple[str, float] | None:
    """Return (rrggbb, alpha 0-1), or None for none/transparent/url()."""
    if text is None:
        return None
    t = text.strip().lower()
    if t in ("none", "transparent", "") or t.startswith("url("):
        return None
    if t == "currentcolor":
        return current, 1.0
    if t.startswith("#"):
        h = t[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6:
            return h, 1.0
        if len(h) == 8:
            return h[:6], int(h[6:], 16) / 255
        return None
    m = re.fullmatch(r"rgba?\(([^)]*)\)", t)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        vals = []
        for p in parts[:3]:
            vals.append(round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)))
        a = 1.0
        if len(parts) > 3:
            a = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
        return "".join(f"{max(0, min(255, v)):02x}" for v in vals), a
    if t in NAMED:
        return NAMED[t], 1.0
    return None


def argb(rgb: str, alpha: float) -> str:
    return f"{max(0, min(255, round(alpha * 255))):02X}{rgb.upper()}"


INHERITED = {"fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity", "fill-rule",
             "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "color",
             "visibility", "stroke-miterlimit"}
STYLE_PROPS = INHERITED | {"opacity", "display"}


def parse_css(text: str) -> list[tuple[str, dict]]:
    rules = []
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", text, flags=re.S)):
        decls = {}
        for item in body.split(";"):
            if ":" in item:
                k, v = item.split(":", 1)
                decls[k.strip()] = v.strip()
        for s in sel.split(","):
            rules.append((s.strip(), decls))
    return rules


def css_matches(selector: str, tag: str, el_id: str | None, classes: set[str]) -> bool:
    if selector.startswith("."):
        return selector[1:] in classes
    if selector.startswith("#"):
        return selector[1:] == el_id
    m = re.fullmatch(r"(\w+)\.([\w-]+)", selector)
    if m:
        return m.group(1) == tag and m.group(2) in classes
    return selector == tag


# --------------------------------------------------------------------------
# the converter
# --------------------------------------------------------------------------


def local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def fmt(v: float) -> str:
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


@dataclass
class Item:
    kind: str  # "shape" or "group"
    name: str
    subpaths: list = field(default_factory=list)
    fill: dict | None = None
    stroke: dict | None = None
    opacity: float = 1.0
    children: list = field(default_factory=list)


class Converter:
    def __init__(self, root: ET.Element):
        self.root = root
        self.warnings: list[str] = []
        self.rules: list[tuple[str, dict]] = []
        self.by_id: dict[str, ET.Element] = {}
        for el in root.iter():
            if el.get("id"):
                self.by_id[el.get("id")] = el
            if local(el.tag) == "style" and el.text:
                self.rules += parse_css(el.text)
        self.count = 0
        # elements on the current walk, <use> targets included: a <use> that
        # points at one of them is a cycle (an error in SVG), cut with a warning
        self._active: list[ET.Element] = []

    def style_of(self, el: ET.Element, inherited: dict) -> dict:
        style = {k: v for k, v in inherited.items() if k in INHERITED}
        classes = set((el.get("class") or "").split())
        tag = local(el.tag)
        for sel, decls in self.rules:
            if css_matches(sel, tag, el.get("id"), classes):
                style.update({k: v for k, v in decls.items() if k in STYLE_PROPS})
        for k in STYLE_PROPS:
            if el.get(k) is not None:
                style[k] = el.get(k)
        for item in (el.get("style") or "").split(";"):
            if ":" in item:
                k, v = item.split(":", 1)
                if k.strip() in STYLE_PROPS:
                    style[k.strip()] = v.strip()
        return style  # opacity and display are not inherited: they only come from this element

    def geometry(self, el: ET.Element) -> list[Subpath]:
        tag = local(el.tag)

        def f(key: str, default: float = 0.0) -> float:
            vals = numbers(el.get(key) or "")
            return float(vals[0]) if vals else default

        if tag == "path":
            return parse_path(el.get("d") or "")
        if tag == "rect":
            w, h = f("width"), f("height")
            if w <= 0 or h <= 0:
                return []
            return [rect_path(f("x"), f("y"), w, h, f("rx"), f("ry"))]
        if tag == "circle":
            r = f("r")
            return [ellipse_path(f("cx"), f("cy"), r, r)] if r > 0 else []
        if tag == "ellipse":
            rx, ry = f("rx"), f("ry")
            return [ellipse_path(f("cx"), f("cy"), rx, ry)] if rx > 0 and ry > 0 else []
        if tag == "line":
            return [Subpath((f("x1"), f("y1")), [("L", (f("x2"), f("y2")))])]
        if tag in ("polyline", "polygon"):
            v = numbers(el.get("points") or "")
            pts = list(zip(v[0::2], v[1::2]))
            if len(pts) < 2:
                return []
            s = Subpath(pts[0], [("L", p) for p in pts[1:]], tag == "polygon")
            if tag == "polygon" and pts[-1] != pts[0]:
                s.segments.append(("L", pts[0]))
            return [s]
        return []

    def paint(self, el: ET.Element, style: dict, which: str, subpaths: list[Subpath], ctm: Matrix) -> dict | None:
        raw = style.get(which, "black" if which == "fill" else None)
        opacity = float(style.get(f"{which}-opacity", 1) or 1)
        colour_text = style.get("color")
        current = (parse_colour(colour_text) or ("000000", 1.0))[0] if colour_text else "000000"
        if raw and raw.strip().startswith("url("):
            ref = re.search(r"#([^)'\"]+)", raw)
            grad = self.by_id.get(ref.group(1)) if ref else None
            if grad is None:
                self.warnings.append(f"{which} {raw} points at nothing; skipped")
                return None
            return self.gradient(grad, opacity, subpaths, ctm)
        colour = parse_colour(raw, current)
        if colour is None:
            if raw and raw.strip().lower() not in ("none", "transparent"):
                self.warnings.append(f"unrecognised colour {raw!r}; drawn black")
                colour = ("000000", 1.0)
            else:
                return None
        return {"type": "solid", "argb": argb(colour[0], colour[1] * opacity)}

    def gradient(self, grad: ET.Element, opacity: float, subpaths: list[Subpath], ctm: Matrix) -> dict | None:
        chain = [grad]
        seen = {id(grad)}
        while True:
            href = chain[-1].get("{http://www.w3.org/1999/xlink}href") or chain[-1].get("href")
            if not href or not href.startswith("#") or href[1:] not in self.by_id:
                break
            nxt = self.by_id[href[1:]]
            if id(nxt) in seen:
                break
            seen.add(id(nxt))
            chain.append(nxt)

        def attr(k, d=None):
            for g in chain:
                if g.get(k) is not None:
                    return g.get(k)
            return d

        stops_el = next((list(g) for g in chain if any(local(s.tag) == "stop" for s in g)), [])
        stops = []
        for s in stops_el:
            if local(s.tag) != "stop":
                continue
            st = {}
            for item in (s.get("style") or "").split(";"):
                if ":" in item:
                    k, v = item.split(":", 1)
                    st[k.strip()] = v.strip()
            off = s.get("offset", "0")
            pos = float(off[:-1]) / 100 if off.strip().endswith("%") else float(numbers(off)[0] if numbers(off) else 0)
            col = parse_colour(st.get("stop-color", s.get("stop-color", "black"))) or ("000000", 1.0)
            so = float(st.get("stop-opacity", s.get("stop-opacity", 1)) or 1)
            stops.append((max(0.0, min(1.0, pos)), argb(col[0], col[1] * so * opacity)))
        if not stops:
            return None
        units = attr("gradientUnits", "objectBoundingBox")
        gt = parse_transform(attr("gradientTransform"))
        if gt != IDENTITY:
            a, b, c, d, _, _ = gt
            if abs(a * c + b * d) > 1e-6 or abs(math.hypot(a, b) - math.hypot(c, d)) > 1e-6:
                self.warnings.append("a gradientTransform with skew or non-uniform scale is approximated")
        pts = [p for s in subpaths for p in s.points()]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        bbox = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)) if pts else (0, 0, 1, 1)

        def pct(v, full):
            v = str(v).strip()
            return float(v[:-1]) / 100 * full if v.endswith("%") else float(v)

        # Coordinates are already in world space (the subpaths were
        # transformed), so a bounding-box gradient maps onto that box, and a
        # user-space one only needs the element's own transform.
        if units == "objectBoundingBox":
            m = mul((bbox[2], 0, 0, bbox[3], bbox[0], bbox[1]), gt)
        else:
            m = mul(ctm, gt)

        def conv(v, axis):
            return pct(v, 1.0)

        if local(grad.tag) == "linearGradient":
            p1 = apply(m, (conv(attr("x1", "0%"), 0), conv(attr("y1", "0%"), 1)))
            p2 = apply(m, (conv(attr("x2", "100%"), 0), conv(attr("y2", "0%"), 1)))
            return {"type": "linear", "start": p1, "end": p2, "stops": stops}
        cx, cy, r = conv(attr("cx", "50%"), 0), conv(attr("cy", "50%"), 1), conv(attr("r", "50%"), 0)
        if attr("fx") or attr("fy"):
            self.warnings.append("a radial gradient focal point (fx/fy) is ignored")
        centre = apply(m, (cx, cy))
        edge = apply(m, (cx + r, cy))
        return {"type": "radial", "start": centre, "end": edge, "stops": stops}

    def stroke_of(self, style: dict, subpaths, ctm: Matrix, el) -> dict | None:
        paint = self.paint(el, style, "stroke", subpaths, ctm)
        if not paint:
            return None
        width = float(numbers(style.get("stroke-width", "1"))[0] if numbers(style.get("stroke-width", "1")) else 1)
        if width <= 0:
            return None
        s = scale_of(ctm)
        dash = numbers(style.get("stroke-dasharray", "")) if style.get("stroke-dasharray", "none") != "none" else []
        if dash and len(dash) % 2:
            dash = dash * 2
        return {"paint": paint, "thickness": width * s,
                "cap": {"round": "round", "square": "square"}.get(style.get("stroke-linecap", "butt"), "butt"),
                "join": {"round": "round", "bevel": "bevel"}.get(style.get("stroke-linejoin", "miter"), "miter"),
                "dash": [d * s for d in dash],
                "dash_offset": float(numbers(style.get("stroke-dashoffset", "0"))[0]
                                     if numbers(style.get("stroke-dashoffset", "0")) else 0) * s}

    def walk(self, el: ET.Element, ctm: Matrix, inherited: dict, depth: int = 0) -> list[Item]:
        self._active.append(el)
        try:
            return self._walk(el, ctm, inherited, depth)
        finally:
            self._active.pop()

    def _walk(self, el: ET.Element, ctm: Matrix, inherited: dict, depth: int = 0) -> list[Item]:
        tag = local(el.tag)
        if tag in ("defs", "style", "title", "desc", "metadata", "clipPath", "mask", "symbol",
                   "linearGradient", "radialGradient", "pattern", "filter", "marker"):
            return []
        style = self.style_of(el, inherited)
        if style.get("display") == "none" or style.get("visibility") == "hidden":
            return []
        m = mul(ctm, parse_transform(el.get("transform")))
        if el.get("clip-path") or el.get("mask") or el.get("filter"):
            self.warnings.append(f"<{tag} id={el.get('id')!r}> uses clip-path/mask/filter, which is not converted")
        opacity = float(style.get("opacity", 1) or 1)
        name = el.get("id") or el.get("{http://www.inkscape.org/namespaces/inkscape}label") or tag
        if tag in ("svg", "g", "a", "switch"):
            kids = []
            for child in el:
                kids += self.walk(child, m, style, depth + 1)
            if not kids:
                return []
            if tag == "svg" and depth == 0:
                return kids
            return [Item("group", name, opacity=opacity, children=kids)]
        if tag == "use":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            target = self.by_id.get(href[1:]) if href.startswith("#") else None
            if target is None:
                self.warnings.append(f"<use href={href!r}> points at nothing")
                return []
            if any(target is a for a in self._active):
                self.warnings.append(f"<use href={href!r}> refers back to an element that contains it; skipped")
                return []
            num = lambda k: numbers(el.get(k) or "0")[0] if numbers(el.get(k) or "0") else 0.0  # noqa: E731
            m2 = mul(m, (1, 0, 0, 1, num("x"), num("y")))
            self._active.append(target)
            try:
                if local(target.tag) == "symbol":
                    kids = []
                    for child in target:
                        kids += self.walk(child, m2, style, depth + 1)
                    return [Item("group", name, opacity=opacity, children=kids)] if kids else []
                return self.walk(target, m2, style, depth + 1)
            finally:
                self._active.pop()
        if tag in ("text", "tspan"):
            self.warnings.append("text is not converted: outline it in the design tool, or rebuild it as Rive Text")
            return []
        if tag == "image":
            self.warnings.append("an embedded <image> is not converted; add it as an ImageAsset instead")
            return []
        subpaths = self.geometry(el)
        if not subpaths:
            return []
        world = [s.transformed(m) for s in subpaths]
        fill = None if tag == "line" else self.paint(el, style, "fill", world, m)
        stroke = self.stroke_of(style, world, m, el)
        if not fill and not stroke:
            return []
        if fill is not None:
            fill["rule"] = "evenOdd" if style.get("fill-rule") == "evenodd" else None
        self.count += 1
        return [Item("shape", name, subpaths=world, fill=fill, stroke=stroke, opacity=opacity)]


def viewbox_matrix(root: ET.Element) -> Matrix:
    vb = numbers(root.get("viewBox") or "")
    width = numbers(root.get("width") or "")
    height = numbers(root.get("height") or "")
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        w = width[0] if width and not (root.get("width") or "").endswith("%") else vb[2]
        h = height[0] if height and not (root.get("height") or "").endswith("%") else vb[3]
        s = min(w / vb[2], h / vb[3])
        return (s, 0, 0, s, -vb[0] * s, -vb[1] * s)
    return IDENTITY


def items_bbox(items: list[Item]) -> tuple[float, float, float, float] | None:
    pts = []

    def collect(it: Item):
        for s in it.subpaths:
            pts.extend(s.points())
        for c in it.children:
            collect(c)

    for it in items:
        collect(it)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def transform_items(items: list[Item], m: Matrix) -> None:
    s = scale_of(m)
    for it in items:
        it.subpaths = [sp.transformed(m) for sp in it.subpaths]
        for paint in (it.fill, (it.stroke or {}).get("paint")):
            if paint and paint.get("type") in ("linear", "radial"):
                paint["start"] = apply(m, paint["start"])
                paint["end"] = apply(m, paint["end"])
        if it.stroke:
            it.stroke["thickness"] *= s
            it.stroke["dash"] = [d * s for d in it.stroke["dash"]]
            it.stroke["dash_offset"] *= s
        transform_items(it.children, m)


# --------------------------------------------------------------------------
# writing RML
# --------------------------------------------------------------------------


def vertices(sp: Subpath) -> list[str]:
    segs = list(sp.segments)
    closed = sp.closed
    pts = [sp.start] + [s[-1] for s in segs]
    if closed and len(pts) > 1 and abs(pts[-1][0] - pts[0][0]) < EPS and abs(pts[-1][1] - pts[0][1]) < EPS:
        pts = pts[:-1]
    out = []
    for i, p in enumerate(pts):
        incoming = segs[i - 1] if i > 0 else (segs[-1] if closed and segs else None)
        outgoing = segs[i] if i < len(segs) else None
        hin = incoming[2] if incoming and incoming[0] == "C" else None
        hout = outgoing[1] if outgoing and outgoing[0] == "C" else None
        if hin is not None and math.hypot(hin[0] - p[0], hin[1] - p[1]) < EPS:
            hin = None
        if hout is not None and math.hypot(hout[0] - p[0], hout[1] - p[1]) < EPS:
            hout = None
        x, y = fmt(p[0]), fmt(p[1])
        if hin is None and hout is None:
            out.append(f'<StraightVertex x="{x}" y="{y}"/>')
            continue
        ri = math.atan2(hin[1] - p[1], hin[0] - p[0]) if hin else None
        di = math.hypot(hin[0] - p[0], hin[1] - p[1]) if hin else 0.0
        ro = math.atan2(hout[1] - p[1], hout[0] - p[0]) if hout else None
        do = math.hypot(hout[0] - p[0], hout[1] - p[1]) if hout else 0.0
        if ri is None:
            ri = ro + math.pi
        if ro is None:
            ro = ri + math.pi
        diff = abs(((ro - ri) % (2 * math.pi)) - math.pi)
        if hin and hout and diff < 1e-4 and abs(di - do) < 1e-3:
            out.append(f'<CubicMirroredVertex x="{x}" y="{y}" rotation="{fmt(ro)}" distance="{fmt(do)}"/>')
        elif hin and hout and diff < 1e-4:
            out.append(f'<CubicAsymmetricVertex x="{x}" y="{y}" rotation="{fmt(ro)}" '
                       f'inDistance="{fmt(di)}" outDistance="{fmt(do)}"/>')
        else:
            out.append(f'<CubicDetachedVertex x="{x}" y="{y}" inRotation="{fmt(ri)}" inDistance="{fmt(di)}" '
                       f'outRotation="{fmt(ro)}" outDistance="{fmt(do)}"/>')
    return out


def xml_name(text: str) -> str:
    return re.sub(r'[<>&"]', "_", text)[:60] or "Shape"


def paint_xml(paint: dict, indent: str) -> list[str]:
    if paint["type"] == "solid":
        return [f'{indent}<SolidColor colorValue="{paint["argb"]}" name="Color"/>']
    tag = "LinearGradient" if paint["type"] == "linear" else "RadialGradient"
    (sx, sy), (ex, ey) = paint["start"], paint["end"]
    lines = [f'{indent}<{tag} startX="{fmt(sx)}" startY="{fmt(sy)}" endX="{fmt(ex)}" endY="{fmt(ey)}" name="Gradient">']
    for pos, col in paint["stops"]:
        lines.append(f'{indent}    <GradientStop colorValue="{col}" position="{fmt(pos)}"/>')
    lines.append(f"{indent}</{tag}>")
    return lines


class Writer:
    def __init__(self, client: int, base: int, effect_id: str | None = None, ink_path: str | None = None):
        self.client = client
        self.next = base
        self.effect_id = effect_id  # a GroupEffect every flagged stroke points at
        self.ink_path = ink_path    # view model path bound onto flagged stroke colours

    def new_id(self) -> str:
        self.next += 1
        return f"{self.client}:{self.next}"

    def item(self, it: Item, indent: str) -> list[str]:
        name = xml_name(it.name)
        op = f' opacity="{fmt(it.opacity)}"' if abs(it.opacity - 1) > 1e-6 else ""
        if it.kind == "group":
            lines = [f'{indent}<Node{op} name="{name}" id="{self.new_id()}">']
            for child in reversed(it.children):  # Rive: first sibling on top
                lines += self.item(child, indent + "    ")
            lines.append(f"{indent}</Node>")
            return lines
        lines = [f'{indent}<Shape{op} name="{name}" id="{self.new_id()}">']
        for sp in it.subpaths:
            closed = ' isClosed="true"' if sp.closed else ""
            lines.append(f'{indent}    <PointsPath{closed} name="Path">')
            lines += [f"{indent}        {v}" for v in vertices(sp)]
            lines.append(f"{indent}    </PointsPath>")
        if it.fill:
            rule = f' fillRule="{it.fill["rule"]}"' if it.fill.get("rule") else ""
            lines.append(f'{indent}    <Fill{rule} name="Fill">')
            lines += paint_xml(it.fill, indent + "        ")
            lines.append(f"{indent}    </Fill>")
        if it.stroke:
            st = it.stroke
            lines.append(f'{indent}    <Stroke thickness="{fmt(st["thickness"])}" cap="{st["cap"]}" '
                         f'join="{st["join"]}" name="{st.get("name", "Stroke")}">')
            if st.get("bind_ink") and self.ink_path and st["paint"]["type"] == "solid":
                lines.append(f'{indent}        <SolidColor colorValue="{st["paint"]["argb"]}" name="Ink">')
                lines.append(f'{indent}            <DataBindContext sourcePathIds="{self.ink_path}" propertyKey="37"/>')
                lines.append(f"{indent}        </SolidColor>")
            else:
                lines += paint_xml(st["paint"], indent + "        ")
            if st.get("feather"):
                lines.append(f'{indent}        <Feather strength="{fmt(st["feather"])}" name="Soft"/>')
            if st.get("effect") and self.effect_id:
                lines.append(f'{indent}        <TargetEffect targetId="{self.effect_id}" name="Draw On"/>')
            if st["dash"]:
                off = f' offset="{fmt(st["dash_offset"])}"' if st["dash_offset"] else ""
                lines.append(f'{indent}        <DashPath{off} name="Dashes">')
                for k, d in enumerate(st["dash"]):
                    lines.append(f'{indent}            <Dash length="{fmt(d)}" name="{"On" if k % 2 == 0 else "Off"}"/>')
                lines.append(f"{indent}        </DashPath>")
            lines.append(f"{indent}    </Stroke>")
        lines.append(f"{indent}</Shape>")
        return lines


def convert(svg_text: str, *, fit: float | None = None, centre: tuple[float, float] | None = None,
            name: str = "Logo", client: int = 0, base: int = 1000, indent: str = "") -> tuple[str, dict]:
    root = ET.fromstring(svg_text)
    conv = Converter(root)
    items = conv.walk(root, viewbox_matrix(root), {})
    bbox = items_bbox(items)
    info = {"shapes": conv.count, "warnings": sorted(set(conv.warnings)), "bbox": bbox}
    if bbox is None:
        raise ValueError("the SVG has nothing drawable")
    x0, y0, x1, y1 = bbox
    if fit or centre:
        w, h = max(x1 - x0, EPS), max(y1 - y0, EPS)
        s = fit / max(w, h) if fit else 1.0
        cx, cy = centre if centre else ((x0 + x1) / 2 * s, (y0 + y1) / 2 * s)
        m = (s, 0, 0, s, cx - (x0 + x1) / 2 * s, cy - (y0 + y1) / 2 * s)
        transform_items(items, m)
        info["bbox"] = items_bbox(items)
    writer = Writer(client, base)
    group = Item("group", name, children=items)
    lines = writer.item(group, indent)
    info["root_id"] = f"{client}:{base + 1}"
    info["next_id"] = writer.next + 1
    return "\n".join(lines) + "\n", info


def outline_items(items: list[Item], ink: str, thickness: float, feather: float = 0.0,
                  suffix: str = "") -> list[Item]:
    """A stroke-only copy of the drawing: the line that draws itself on."""
    out = []
    for it in items:
        if it.kind == "group":
            out.append(Item("group", it.name + suffix, opacity=it.opacity,
                            children=outline_items(it.children, ink, thickness, feather, suffix)))
            continue
        stroke = {"paint": {"type": "solid", "argb": ink}, "thickness": thickness, "cap": "round",
                  "join": "round", "dash": [], "dash_offset": 0.0, "effect": True, "bind_ink": True,
                  "name": "Glow" if feather else "Line"}
        if feather:
            stroke["feather"] = feather
        out.append(Item("shape", it.name + suffix, subpaths=it.subpaths, stroke=stroke, opacity=it.opacity))
    return out


def reveal_scene(svg_text: str, width: int, height: int, background: str | None, ink: str = "FFFFFFFF",
                 fit: float | None = None) -> tuple[str, dict]:
    """A whole project scene: the logo draws itself on, fills in, and settles.

    Three copies of the drawing share one centre: a stroke-only Line and a
    feathered Glow, both trimmed by one GroupEffect so every contour draws
    on together, and the original Fill. Timeline Reveal, 3 s at 60 fps:
    draw 0-1.2 s, fill 0.9-1.6 s with a 4% scale settle, line out 1.4-2 s.
    """
    fit = fit or min(width, height) * 0.6
    root = ET.fromstring(svg_text)
    conv = Converter(root)
    items = conv.walk(root, viewbox_matrix(root), {})
    bbox = items_bbox(items)
    if bbox is None:
        raise ValueError("the SVG has nothing drawable")
    x0, y0, x1, y1 = bbox
    scale = fit / max(x1 - x0, y1 - y0, EPS)
    transform_items(items, (scale, 0, 0, scale, -(x0 + x1) / 2 * scale, -(y0 + y1) / 2 * scale))
    line_w = max(1.5, fit * 0.006)
    effect_id, trim_id = "0:81", "0:82"
    ink_path = "0:60-0:62"
    writer = Writer(0, 1000, effect_id=effect_id, ink_path=ink_path)
    outline = writer.item(Item("group", "Line", children=outline_items(items, ink, line_w)), "            ")
    glow = writer.item(Item("group", "Glow", opacity=0.0,
                            children=outline_items(items, ink, line_w * 3, feather=fit * 0.02, suffix=" glow")),
                       "            ")
    fill = writer.item(Item("group", "Fill", opacity=0.0, children=items), "            ")
    # the three copies need ids the animation can key: patch them onto the group nodes
    ids = {"Line": "0:21", "Glow": "0:22", "Fill": "0:23"}
    blocks = []
    for name, block in (("Line", outline), ("Fill", fill), ("Glow", glow)):
        first = re.sub(r'id="[^"]+"', f'id="{ids[name]}"', block[0], count=1)
        if name == "Fill":
            first = first.replace("<Node", '<Node scaleX="0.96" scaleY="0.96"', 1)
        blocks.append("\n".join([first] + block[1:]))
    bg = (f'        <Fill name="Background"><SolidColor colorValue="FF{background.upper()}" name="Color"/></Fill>\n'
          if background else "")

    def key(obj, prop, frames):
        rows = []
        for frame, value, ease in frames:
            if ease == "hold":
                rows.append(f'                    <KeyFrameDouble value="{fmt(value)}" frame="{frame}" '
                            f'interpolationType="hold"/>')
            else:
                x1_, y1_, x2_, y2_ = ease
                rows.append(f'                    <KeyFrameDouble value="{fmt(value)}" frame="{frame}" '
                            f'interpolationType="cubic">\n                        <CubicEaseInterpolator '
                            f'x1="{x1_}" y1="{y1_}" x2="{x2_}" y2="{y2_}"/>\n                    </KeyFrameDouble>')
        return (f'            <KeyedObject objectId="{obj}">\n                <KeyedProperty propertyKey="{prop}">\n'
                + "\n".join(rows) + "\n                </KeyedProperty>\n            </KeyedObject>")

    inout = (0.65, 0, 0.35, 1)
    out_ = (0.16, 1, 0.3, 1)
    keys = "\n".join([
        key(trim_id, 115, [(0, 0, inout), (72, 1, "hold")]),
        key("0:23", 18, [(54, 0, out_), (96, 1, "hold")]),
        key("0:23", 16, [(54, 0.96, out_), (108, 1, "hold")]),
        key("0:23", 17, [(54, 0.96, out_), (108, 1, "hold")]),
        key("0:21", 18, [(84, 1, inout), (120, 0, "hold")]),
        key("0:22", 18, [(60, 0, inout), (84, 0.7, inout), (150, 0, "hold")]),
    ])
    rml = f"""<Rive version="1" kind="fragment">
    <!--
      Logo reveal made by rive_svg.py with its reveal option. The drawing appears three
      times at one centre (Line, Fill, Glow; the first sibling draws on top).
      The GroupEffect "Draw" trims every Line and Glow stroke together, so
      keying its TrimPath end (0:82, key 115) draws the whole mark on.
      Re-time the Reveal keys to taste; the ink colour is on the view model.
    -->
    <Artboard defaultStateMachineId="0:7" viewModelId="0:60" viewModelInstanceId="0:61" styleId="0:5"
              width="{width}" height="{height}" name="Main" id="0:2">
        <LayoutComponentStyle name="Artboard Style" id="0:5"/>
{bg}        <GroupEffect name="Draw" id="{effect_id}">
            <TrimPath start="0" end="0" name="Draw Trim" id="{trim_id}"/>
        </GroupEffect>
        <Node x="{fmt(width / 2)}" y="{fmt(height / 2)}" name="Logo" id="0:20">
{chr(10).join(blocks)}
        </Node>
        <LinearAnimation fps="60" duration="180" name="Reveal" id="0:6">
{keys}
        </LinearAnimation>
        <StateMachine name="Reveal Machine" id="0:7">
            <StateMachineLayer name="Play" id="0:8">
                <AnyState x="400" y="0"/>
                <ExitState x="560" y="0"/>
                <EntryState x="0" y="0"><StateTransition stateToId="0:9"/></EntryState>
                <AnimationState x="200" y="0" animationId="0:6" id="0:9"/>
            </StateMachineLayer>
        </StateMachine>
    </Artboard>

    <ViewModel defaultInstanceId="0:61" name="LogoReveal" id="0:60">
        <ViewModelPropertyColor name="ink" id="0:62"/>
        <ViewModelInstance exports="true" name="Default" id="0:61">
            <ViewModelInstanceColor propertyValue="{ink}" viewModelPropertyId="0:62"/>
        </ViewModelInstance>
    </ViewModel>
</Rive>
"""
    return rml, {"shapes": conv.count, "warnings": sorted(set(conv.warnings))}


def scene(fragment: str, width: int, height: int, background: str | None) -> str:
    bg = (f'        <Fill name="Background"><SolidColor colorValue="FF{background.upper()}" name="Color"/></Fill>\n'
          if background else "")
    body = "\n".join("        " + line if line else line for line in fragment.rstrip("\n").split("\n"))
    return f"""<Rive version="1" kind="fragment">
    <Artboard defaultStateMachineId="0:7" styleId="0:5" width="{width}" height="{height}" name="Main" id="0:2">
        <LayoutComponentStyle name="Artboard Style" id="0:5"/>
{bg}{body}
        <LinearAnimation loopValue="loop" duration="60" name="Idle" id="0:6"/>
        <StateMachine name="State Machine" id="0:7">
            <StateMachineLayer name="Layer" id="0:8">
                <AnyState x="400" y="0"/>
                <ExitState x="560" y="0"/>
                <EntryState x="0" y="0"><StateTransition stateToId="0:9"/></EntryState>
                <AnimationState x="200" y="0" animationId="0:6" id="0:9"/>
            </StateMachineLayer>
        </StateMachine>
    </Artboard>
</Rive>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("svg")
    ap.add_argument("-o", "--output", help="write the RML fragment here (default: stdout)")
    ap.add_argument("--project", help="write a whole project (rive.yaml + scene.rml) to this new folder")
    ap.add_argument("--size", default="1920x1080", help="artboard size for --project")
    ap.add_argument("--background", help="RRGGBB artboard background for --project (default: none)")
    ap.add_argument("--fit", type=float, help="scale so the longest side is this many units")
    ap.add_argument("--center", help="X,Y to centre the drawing on (default: the artboard centre with --project)")
    ap.add_argument("--name", default="Logo", help="name of the wrapping Node")
    ap.add_argument("--id-base", type=int, default=1000, help="ids are 0:<base+1>... (keep clear of your own)")
    ap.add_argument("--reveal", action="store_true",
                    help="with --project: a draw-on, fill-in, settle animation of the logo (3 s)")
    ap.add_argument("--ink", default="FFFFFFFF", help="AARRGGBB of the draw-on line for --reveal")
    args = ap.parse_args(argv)
    svg_text = Path(args.svg).read_text(encoding="utf-8")
    centre = tuple(float(v) for v in args.center.split(",")) if args.center else None
    width = height = None
    if args.project:
        width, height = (int(v) for v in args.size.lower().split("x"))
        if centre is None:
            centre = (width / 2, height / 2)
        if args.fit is None:
            args.fit = min(width, height) * 0.6
    try:
        fragment, info = convert(svg_text, fit=args.fit, centre=centre, name=args.name, base=args.id_base)
    except (ValueError, ET.ParseError) as exc:
        print(f"rive_svg: {exc}", file=sys.stderr)
        return 1
    if args.project:
        proj = Path(args.project)
        if proj.exists() and any(proj.iterdir()):
            print(f"rive_svg: {proj} exists and is not empty", file=sys.stderr)
            return 1
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "rive.yaml").write_text(f"name: {re.sub(r'[^A-Za-z0-9_]', '_', proj.name) or 'logo'}\n",
                                        encoding="utf-8")
        if args.reveal:
            text, info = reveal_scene(svg_text, width, height, args.background, args.ink.upper(), args.fit)
            (proj / "scene.rml").write_text(text, encoding="utf-8")
            print(f"wrote {proj}/scene.rml: a {info['shapes']}-shape logo reveal (timeline Reveal, 3 s)")
        else:
            (proj / "scene.rml").write_text(scene(fragment, width, height, args.background), encoding="utf-8")
            print(f"wrote {proj}/scene.rml: {info['shapes']} shapes, root {info['root_id']}")
    elif args.output:
        Path(args.output).write_text(fragment, encoding="utf-8")
        print(f"wrote {args.output}: {info['shapes']} shapes, root {info['root_id']}, next free id {info['next_id']}")
    else:
        sys.stdout.write(fragment)
    for w in info["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
