#!/usr/bin/env python3
"""A small, dependency free reader for the font metrics supers geometry needs.

Supers geometry should be DERIVED from the font, not copied from the last job.
Copied numbers survive one change of typeface and then quietly stop describing
anything. What is needed is small: units per em, the vertical metrics, the
advance width of a string, and the ink bounding box of a string.

Reads TrueType outlines (glyf) from .ttf, .ttc and .otf containers. CFF outlines
(most .otf) carry no glyf table, so per glyph ink boxes are not available from
this reader; it says so rather than guessing, and falls back to the font's own
bounding box. Kerning from GPOS is NOT applied, which matters at display sizes:
treat advance widths as unkerned and check a real render before shipping.
"""
from __future__ import annotations

import struct

_SFNT_TRUETYPE = bytes([0x00, 0x01, 0x00, 0x00])


class FontError(RuntimeError):
    pass


class Font:
    """Enough of a font to place type on a frame."""

    def __init__(self, path, index=0):
        self.path = path
        with open(path, "rb") as fh:
            self.data = fh.read()
        self.tables = {}
        self._read_directory(index)
        self._read_head()
        self._read_hhea()
        self._read_maxp()
        self._read_os2()
        self._read_hmtx()
        self._read_cmap()
        self._read_loca()

    # -------------------------------------------------- container

    def _read_directory(self, index):
        tag = self.data[:4]
        offset = 0
        if tag == b"ttcf":
            num_fonts = struct.unpack_from(">I", self.data, 8)[0]
            if index >= num_fonts:
                raise FontError(f"Collection has {num_fonts} fonts; asked for {index}.")
            offset = struct.unpack_from(">I", self.data, 12 + 4 * index)[0]
            tag = self.data[offset:offset + 4]
        if tag not in (_SFNT_TRUETYPE, b"OTTO", b"true", b"typ1"):
            raise FontError(f"Not a font this reader understands: tag {tag!r}")
        self.flavour = "cff" if tag == b"OTTO" else "truetype"
        num_tables = struct.unpack_from(">H", self.data, offset + 4)[0]
        for i in range(num_tables):
            base = offset + 12 + 16 * i
            name, _csum, off, length = struct.unpack_from(">4sIII", self.data, base)
            self.tables[name.decode("latin-1").strip()] = (off, length)

    def _table(self, name):
        if name not in self.tables:
            return None
        off, length = self.tables[name]
        return self.data[off:off + length]

    # -------------------------------------------------- metrics

    def _read_head(self):
        head = self._table("head")
        if head is None:
            raise FontError("No head table; this file is not usable as a font.")
        self.units_per_em = struct.unpack_from(">H", head, 18)[0]
        self.font_bbox = struct.unpack_from(">hhhh", head, 36)
        self.index_to_loc_format = struct.unpack_from(">h", head, 50)[0]

    def _read_hhea(self):
        hhea = self._table("hhea")
        if hhea is None:
            raise FontError("No hhea table.")
        self.ascender, self.descender, self.line_gap = struct.unpack_from(">hhh", hhea, 4)
        self.num_h_metrics = struct.unpack_from(">H", hhea, 34)[0]

    def _read_maxp(self):
        maxp = self._table("maxp")
        self.num_glyphs = struct.unpack_from(">H", maxp, 4)[0] if maxp else 0

    def _read_os2(self):
        os2 = self._table("OS/2")
        self.cap_height = None
        self.x_height = None
        self.typo_ascender = self.typo_descender = self.typo_line_gap = None
        if not os2:
            return
        version = struct.unpack_from(">H", os2, 0)[0]
        if len(os2) >= 74:
            (self.typo_ascender, self.typo_descender,
             self.typo_line_gap) = struct.unpack_from(">hhh", os2, 68)
        if version >= 2 and len(os2) >= 90:
            self.x_height = struct.unpack_from(">h", os2, 86)[0]
            self.cap_height = struct.unpack_from(">h", os2, 88)[0]

    def _read_hmtx(self):
        hmtx = self._table("hmtx")
        self.advances = []
        if hmtx is None:
            return
        n = min(self.num_h_metrics, len(hmtx) // 4)
        for i in range(n):
            adv = struct.unpack_from(">H", hmtx, 4 * i)[0]
            self.advances.append(adv)

    def advance(self, gid):
        if not self.advances:
            return self.units_per_em // 2
        if gid < len(self.advances):
            return self.advances[gid]
        return self.advances[-1]

    # -------------------------------------------------- cmap

    def _read_cmap(self):
        self.cmap = {}
        cmap = self._table("cmap")
        if cmap is None:
            return
        n = struct.unpack_from(">H", cmap, 2)[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack_from(">HHI", cmap, 4 + 8 * i)
            rank = {(3, 10): 4, (3, 1): 3, (0, 4): 2, (0, 3): 2, (0, 6): 2,
                    (3, 0): 1, (1, 0): 0}.get((pid, eid), -1)
            if rank >= 0 and (best is None or rank > best[0]):
                best = (rank, off)
        if best is None:
            return
        sub = cmap[best[1]:]
        fmt = struct.unpack_from(">H", sub, 0)[0]
        if fmt == 4:
            self._cmap4(sub)
        elif fmt == 12:
            self._cmap12(sub)
        elif fmt == 6:
            first, count = struct.unpack_from(">HH", sub, 6)
            for i in range(count):
                self.cmap[first + i] = struct.unpack_from(">H", sub, 10 + 2 * i)[0]
        elif fmt == 0:
            for code in range(256):
                self.cmap[code] = sub[6 + code]

    def _cmap4(self, sub):
        seg_x2 = struct.unpack_from(">H", sub, 6)[0]
        seg = seg_x2 // 2
        ends = struct.unpack_from(f">{seg}H", sub, 14)
        starts = struct.unpack_from(f">{seg}H", sub, 16 + seg_x2)
        deltas = struct.unpack_from(f">{seg}h", sub, 16 + 2 * seg_x2)
        range_off_base = 16 + 3 * seg_x2
        range_offs = struct.unpack_from(f">{seg}H", sub, range_off_base)
        for i in range(seg):
            for code in range(starts[i], min(ends[i], 0xFFFF) + 1):
                if range_offs[i] == 0:
                    gid = (code + deltas[i]) & 0xFFFF
                else:
                    pos = range_off_base + 2 * i + range_offs[i] + 2 * (code - starts[i])
                    if pos + 2 > len(sub):
                        continue
                    gid = struct.unpack_from(">H", sub, pos)[0]
                    if gid:
                        gid = (gid + deltas[i]) & 0xFFFF
                if gid:
                    self.cmap[code] = gid

    def _cmap12(self, sub):
        n = struct.unpack_from(">I", sub, 12)[0]
        for i in range(n):
            start, end, gid = struct.unpack_from(">III", sub, 16 + 12 * i)
            for code in range(start, min(end, start + 0x10000) + 1):
                self.cmap[code] = gid + (code - start)

    def gid(self, char):
        return self.cmap.get(ord(char), 0)

    # -------------------------------------------------- outlines

    def _read_loca(self):
        self.loca = []
        loca = self._table("loca")
        if loca is None:
            return
        if self.index_to_loc_format == 0:
            n = len(loca) // 2
            self.loca = [2 * v for v in struct.unpack_from(f">{n}H", loca, 0)]
        else:
            n = len(loca) // 4
            self.loca = list(struct.unpack_from(f">{n}I", loca, 0))

    def glyph_bbox(self, gid):
        """Ink box of one glyph in font units, or None when unavailable."""
        glyf = self._table("glyf")
        if glyf is None or not self.loca or gid + 1 >= len(self.loca):
            return None
        start, end = self.loca[gid], self.loca[gid + 1]
        if end <= start or end > len(glyf):
            return None  # empty glyph, for instance a space
        return struct.unpack_from(">hhhh", glyf, start + 2)

    # -------------------------------------------------- strings

    def measure(self, text, size=None):
        """Advance width and ink box of a string.

        Returns font units when size is None, otherwise pixels at that em size.
        Ink coordinates are relative to the pen origin on the baseline, y up.
        """
        pen = 0
        ink_l = ink_r = ink_t = ink_b = None
        missing = []
        for ch in text:
            gid = self.gid(ch)
            if gid == 0 and ch != " ":
                missing.append(ch)
            box = self.glyph_bbox(gid)
            if box:
                x_min, y_min, x_max, y_max = box
                ink_l = pen + x_min if ink_l is None else min(ink_l, pen + x_min)
                ink_r = pen + x_max if ink_r is None else max(ink_r, pen + x_max)
                ink_b = y_min if ink_b is None else min(ink_b, y_min)
                ink_t = y_max if ink_t is None else max(ink_t, y_max)
            pen += self.advance(gid)
        out = {
            "text": text, "units_per_em": self.units_per_em,
            "advance": pen,
            "ink_left": ink_l, "ink_right": ink_r,
            "ink_top": ink_t, "ink_bottom": ink_b,
            "missing_glyphs": missing,
            "ink_available": ink_l is not None,
            "kerning_applied": False,
        }
        if size:
            k = size / self.units_per_em
            for key in ("advance", "ink_left", "ink_right", "ink_top", "ink_bottom"):
                if out[key] is not None:
                    out[key] = out[key] * k
            out["size"] = size
        return out

    def vertical(self, size=None):
        """The vertical metrics, in font units or at a given em size."""
        out = {"units_per_em": self.units_per_em,
               "ascender": self.ascender, "descender": self.descender,
               "line_gap": self.line_gap, "cap_height": self.cap_height,
               "x_height": self.x_height,
               "typo_ascender": self.typo_ascender,
               "typo_descender": self.typo_descender,
               "font_bbox": list(self.font_bbox),
               "flavour": self.flavour, "glyphs": self.num_glyphs}
        if size:
            k = size / self.units_per_em
            for key in ("ascender", "descender", "line_gap", "cap_height",
                        "x_height", "typo_ascender", "typo_descender"):
                if out[key] is not None:
                    out[key] = out[key] * k
            out["size"] = size
        return out
