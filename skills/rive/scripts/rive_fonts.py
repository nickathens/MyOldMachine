#!/usr/bin/env python3
"""Fonts for Rive projects: fetch an open font, and write its RML correctly.

Rive ships no fonts and has no system fallback, and building a .riv embeds
(redistributes) every font it uses, so a project needs a font licensed for
that. This fetches families from Google Fonts' own repository (OFL, Apache
or UFL, each with its licence file) and reads the font itself to write the
three things the markup gets wrong silently:

  * familyName / styleName: the editor labels its Font and Weight menus from
    these, not from the file; missing, every style opens as "-"
  * the variable axes: a variable font renders its DEFAULT instance unless
    a TextStyleAxis says otherwise (Montserrat's default is Thin)
  * the packed OpenType tag integers Rive wants (wght = 2003265652)

  rive_fonts.py add "Space Grotesk" --project ~/work/intro
  rive_fonts.py info ~/work/intro/SpaceGrotesk-Variable.ttf --weight 700
  rive_fonts.py snippet SpaceGrotesk-Variable.ttf --weight 700 --size 64 --id 0:30
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/google/fonts/main"
LICENCE_DIRS = ("ofl", "apache", "ufl")
LICENCE_FILES = {"ofl": "OFL.txt", "apache": "LICENSE.txt", "ufl": "UFL.txt"}


def pack_tag(tag: str) -> int:
    """Four ASCII characters, big-endian, the way TextStyleAxis/Feature want them."""
    if len(tag) != 4:
        raise ValueError(f"an OpenType tag is four characters, not {tag!r}")
    return struct.unpack(">I", tag.encode("ascii"))[0]


# --------------------------------------------------------------------------
# reading a TrueType/OpenType file
# --------------------------------------------------------------------------


def _tables(data: bytes) -> dict[str, tuple[int, int]]:
    if len(data) < 12:
        raise ValueError("not a font file")
    version = data[:4]
    if version == b"ttcf":
        raise ValueError("font collections (.ttc) are not supported; extract one face first")
    (num,) = struct.unpack(">H", data[4:6])
    tables = {}
    for i in range(num):
        rec = data[12 + 16 * i: 28 + 16 * i]
        tag = rec[:4].decode("latin-1")
        _, offset, length = struct.unpack(">III", rec[4:16])
        tables[tag] = (offset, length)
    return tables


def _names(data: bytes, tables) -> dict[int, str]:
    if "name" not in tables:
        return {}
    off, _ = tables["name"]
    _, count, string_off = struct.unpack(">HHH", data[off:off + 6])
    best: dict[int, tuple[int, str]] = {}
    for i in range(count):
        pid, eid, lid, nid, length, noff = struct.unpack(">HHHHHH", data[off + 6 + 12 * i: off + 18 + 12 * i])
        raw = data[off + string_off + noff: off + string_off + noff + length]
        if pid == 3 and eid in (0, 1, 10):
            text, rank = raw.decode("utf-16-be", "replace"), (0 if lid == 0x409 else 1)
        elif pid == 0:
            text, rank = raw.decode("utf-16-be", "replace"), 2
        elif pid == 1 and eid == 0:
            text, rank = raw.decode("mac_roman", "replace"), 3
        else:
            continue
        if nid not in best or rank < best[nid][0]:
            best[nid] = (rank, text)
    return {k: v for k, (_, v) in best.items()}


def read_font(path: str | Path) -> dict:
    data = Path(path).read_bytes()
    tables = _tables(data)
    names = _names(data, tables)
    info = {
        "file": str(path),
        "family": names.get(16) or names.get(1) or Path(path).stem,
        "style": names.get(17) or names.get(2) or "Regular",
        "full_name": names.get(4),
        "postscript": names.get(6),
        "weight_class": None,
        "axes": [],
        "instances": [],
        "variable": "fvar" in tables,
    }
    if "OS/2" in tables:
        off, _ = tables["OS/2"]
        (info["weight_class"],) = struct.unpack(">H", data[off + 4: off + 6])
    if "fvar" in tables:
        off, _ = tables["fvar"]
        (_, _, axes_off, _, axis_count, axis_size, inst_count, inst_size) = struct.unpack(
            ">HHHHHHHH", data[off:off + 16])
        for i in range(axis_count):
            a = off + axes_off + i * axis_size
            tag = data[a:a + 4].decode("latin-1")
            mn, df, mx = (v / 65536 for v in struct.unpack(">iii", data[a + 4:a + 16]))
            _, name_id = struct.unpack(">HH", data[a + 16:a + 20])
            info["axes"].append({"tag": tag, "packed": pack_tag(tag), "min": mn, "default": df, "max": mx,
                                 "name": names.get(name_id, tag)})
        inst_base = off + axes_off + axis_count * axis_size
        for i in range(inst_count):
            b = inst_base + i * inst_size
            sub_id, _ = struct.unpack(">HH", data[b:b + 4])
            coords = [v / 65536 for v in struct.unpack(f">{axis_count}i", data[b + 4:b + 4 + 4 * axis_count])]
            info["instances"].append({"name": names.get(sub_id, f"instance {i}"),
                                      "coords": {ax["tag"]: c for ax, c in zip(info["axes"], coords)}})
    return info


def pick_instance(info: dict, weight: float | None, style: str | None) -> tuple[str, dict]:
    """(styleName, {tag: value}) for a requested weight or named instance."""
    if style:
        for inst in info["instances"]:
            if inst["name"].lower() == style.lower():
                return inst["name"], inst["coords"]
        if not info["variable"]:
            return style, {}
        raise ValueError(f"no named instance {style!r}; the font has "
                         + ", ".join(i["name"] for i in info["instances"]))
    if not info["variable"]:
        return info["style"], {}
    coords = {ax["tag"]: ax["default"] for ax in info["axes"]}
    if weight is not None:
        wght = next((ax for ax in info["axes"] if ax["tag"] == "wght"), None)
        if wght is None:
            raise ValueError("this variable font has no wght axis")
        coords["wght"] = max(wght["min"], min(wght["max"], float(weight)))
    for inst in info["instances"]:
        if all(abs(inst["coords"].get(t, v) - v) < 0.5 for t, v in coords.items()):
            return inst["name"], coords
    return "Custom", coords


def snippet(info: dict, *, file_name: str, weight=None, style=None, size: float = 48,
            font_id: str = "0:30", style_id: str = "0:21", colour: str = "FFFFFFFF") -> str:
    style_name, coords = pick_instance(info, weight, style)
    family = info["family"]
    axes = "".join(
        f'\n    <TextStyleAxis tag="{pack_tag(tag)}" axisValue="{value:g}" name="{tag}"/>'
        for tag, value in coords.items())
    return (f'<FontAsset file="{file_name}" name="{family}" id="{font_id}"/>\n\n'
            f'<TextStylePaint fontSize="{size:g}" fontAssetId="{font_id}" familyName="{family}" '
            f'styleName="{style_name}" name="{family} {style_name}" id="{style_id}">\n'
            f'    <Fill name="Fill"><SolidColor colorValue="{colour}" name="Color"/></Fill>{axes}\n'
            f'</TextStylePaint>\n')


# --------------------------------------------------------------------------
# fetching from google/fonts
# --------------------------------------------------------------------------


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def find_family(family: str) -> tuple[str, str, str]:
    """Return (licence dir, family dir, METADATA.pb text) for a Google Fonts family."""
    folder = re.sub(r"[^a-z0-9]", "", family.lower())
    for lic in LICENCE_DIRS:
        meta = _get(f"{RAW}/{lic}/{folder}/METADATA.pb")
        if meta is not None:
            return lic, folder, meta.decode("utf-8", "replace")
    raise ValueError(f"{family!r} is not in google/fonts (looked for ofl|apache|ufl/{folder})")


def add_family(family: str, project: Path, italic: bool = False) -> list[Path]:
    lic, folder, meta = find_family(family)
    files = re.findall(r'filename:\s*"([^"]+)"', meta)
    styles = re.findall(r'style:\s*"([^"]+)"', meta)
    wanted = [f for f, s in zip(files, styles) if (s == "italic") == italic] or files
    project.mkdir(parents=True, exist_ok=True)
    written = []
    for name in dict.fromkeys(wanted):
        blob = _get(f"{RAW}/{lic}/{folder}/{name}")
        if blob is None:
            continue
        safe = re.sub(r"\[[^\]]*\]", "-Variable", Path(name).name).replace("--", "-")
        if not safe or safe.startswith(".") or not safe.lower().endswith((".ttf", ".otf")):
            continue
        target = project / safe
        target.write_bytes(blob)
        written.append(target)
    licence = _get(f"{RAW}/{lic}/{folder}/{LICENCE_FILES[lic]}")
    if licence:
        (project / f"{folder}-{LICENCE_FILES[lic]}").write_bytes(licence)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="download a Google Fonts family (with its licence) into a project")
    a.add_argument("family")
    a.add_argument("--project", required=True)
    a.add_argument("--italic", action="store_true")
    a.add_argument("--weight", type=float, default=None)
    i = sub.add_parser("info", help="read a font: names, axes, named instances")
    i.add_argument("font")
    i.add_argument("--json", action="store_true")
    s = sub.add_parser("snippet", help="RML for a FontAsset and a TextStylePaint")
    for p in (i, s):
        p.add_argument("--weight", type=float)
        p.add_argument("--style", help="a named instance, e.g. Bold")
    s.add_argument("font")
    s.add_argument("--size", type=float, default=48)
    s.add_argument("--id", default="0:30", help="FontAsset id")
    s.add_argument("--style-id", default="0:21")
    s.add_argument("--colour", default="FFFFFFFF", help="AARRGGBB")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            files = add_family(args.family, Path(args.project), args.italic)
            if not files:
                print("nothing downloaded", file=sys.stderr)
                return 1
            info = read_font(files[0])
            print(f"added {', '.join(f.name for f in files)} to {args.project} "
                  f"(licence file beside it; building a .riv embeds the font, which the licence allows)")
            weight = args.weight
            wght = next((ax for ax in info["axes"] if ax["tag"] == "wght"), None)
            if wght and wght["default"] < 400 and weight is None:
                weight = max(wght["min"], min(wght["max"], 400.0))
                print(f"NOTE: its default weight is {wght['default']:g}, so a style with no weight axis renders "
                      f"that light; the snippet below sets {weight:g}. Pass --weight to choose another.")
            print(snippet(info, file_name=files[0].name, weight=weight))
            return 0
        info = read_font(args.font)
        if args.cmd == "info":
            if args.json:
                print(json.dumps(info, indent=2))
            else:
                print(f"family {info['family']!r}, style {info['style']!r}, weight class {info['weight_class']}")
                for ax in info["axes"]:
                    print(f"  axis {ax['tag']} ({ax['name']}) {ax['min']:g}..{ax['max']:g}, "
                          f"default {ax['default']:g}, packed tag {ax['packed']}")
                    if ax["tag"] == "wght" and ax["default"] < 400:
                        print(f"  WARNING: with no TextStyleAxis this renders at weight {ax['default']:g}")
                for inst in info["instances"]:
                    print(f"  instance {inst['name']}: {inst['coords']}")
            return 0
        print(snippet(info, file_name=Path(args.font).name, weight=args.weight, style=args.style,
                      size=args.size, font_id=args.id, style_id=args.style_id, colour=args.colour))
        return 0
    except (ValueError, OSError, urllib.error.URLError) as exc:
        print(f"rive_fonts: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
