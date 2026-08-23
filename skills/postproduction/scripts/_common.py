#!/usr/bin/env python3
"""Shared plumbing for the postproduction engines.

Nothing here decides anything about a film. It is the layer that reads a file
honestly (ffprobe), does exact rational frame rate arithmetic, hashes, and
prints either a human table or JSON. Every engine imports it so that a
measurement means the same thing in all of them.

Two rules live in this file because they are the ones that get broken:

1. A number carries the file it was measured on and the path it was decoded
   through. `Measurement` keeps both, and `require_same_path` refuses to
   compare two numbers that came through different decoders.
2. A frame rate is a RATIO, never a float. 24000/1001 is not 23.98.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction

VERIFY = "[VERIFY]"

# ---------------------------------------------------------------- process


class ToolMissing(RuntimeError):
    """A required external tool is not on PATH."""


def need(tool):
    """Return the path to an external tool or explain what to install."""
    path = shutil.which(tool)
    if not path:
        raise ToolMissing(
            f"{tool} is not on PATH. Install it (macOS: brew install ffmpeg) "
            "before running this command."
        )
    return path


def run(cmd, check=True, text=True, timeout=None):
    """Run a command and return CompletedProcess. stderr is always captured."""
    return subprocess.run(cmd, check=check, capture_output=True, text=text,
                          timeout=timeout)


def ffprobe_json(path, args):
    """Run ffprobe with -of json and return the parsed object."""
    need("ffprobe")
    cmd = ["ffprobe", "-v", "error", "-of", "json"] + list(args) + [str(path)]
    proc = run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout or "{}")


# ---------------------------------------------------------------- rates


def rate(value):
    """Parse a frame rate into an exact Fraction.

    Accepts '24', '24/1', '30000/1001', '23.976', 23.98, Fraction. The decimal
    forms that are really NTSC ratios are snapped to the exact ratio, because a
    film cut at 24000/1001 and a film cut at 23.976 drift apart by a frame every
    few hours and every timecode built on the float is wrong.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value, 1)
    s = str(value).strip().lower().rstrip("p").replace("fps", "").strip()
    if "/" in s:
        num, den = s.split("/", 1)
        f = Fraction(int(num), int(den))
    else:
        f = Fraction(s)
    return _snap_ntsc(f)


_NTSC = {
    Fraction(24000, 1001): (23.97, 23.99),
    Fraction(30000, 1001): (29.96, 29.98),
    Fraction(60000, 1001): (59.93, 59.95),
    Fraction(120000, 1001): (119.87, 119.89),
}


def _snap_ntsc(f):
    v = float(f)
    for exact, (lo, hi) in _NTSC.items():
        if lo <= v <= hi and f != exact:
            return exact
    return f


def rate_str(f):
    """Print a rate the way a delivery spec writes it."""
    f = Fraction(f)
    if f.denominator == 1:
        return str(f.numerator)
    return f"{f.numerator}/{f.denominator} ({float(f):.3f})"


# ---------------------------------------------------------------- pixels


# Bit depth and chroma read off a pixel format name. The tag is what the file
# CLAIMS; spec.py has a separate command that measures what it actually carries.
def pix_depth(pix_fmt):
    """Bit depth declared by a pixel format name, or None if unknown."""
    if not pix_fmt:
        return None
    name = pix_fmt.lower()
    for depth in (16, 14, 12, 10, 9):
        if str(depth) in name:
            # yuv420p10le, p010le, rgb48le, gray16le
            if name.startswith("p0") and depth == 10 and "p010" in name:
                return 10
            if f"p{depth}" in name or f"{depth}le" in name or f"{depth}be" in name:
                return depth
    if "rgb48" in name or "rgba64" in name:
        return 16
    if "p016" in name:
        return 16
    if "p010" in name:
        return 10
    return 8


def pix_chroma(pix_fmt):
    """Chroma subsampling family from a pixel format name."""
    if not pix_fmt:
        return None
    name = pix_fmt.lower()
    if name.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra")):
        return "rgb"
    if "gray" in name:
        return "gray"
    for tag in ("444", "440", "422", "420", "411", "410"):
        if tag in name:
            return tag
    if name.startswith("p0") or name.startswith("nv12"):
        return "420"
    if name.startswith("nv16"):
        return "422"
    return None


_ALPHA_PREFIXES = ("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya", "pal8")


def pix_alpha(pix_fmt):
    """True when the pixel format carries an alpha channel."""
    if not pix_fmt:
        return False
    return pix_fmt.lower().startswith(_ALPHA_PREFIXES)


# ---------------------------------------------------------------- hashing


def sha256_file(path, chunk=1 << 20):
    """SHA256 of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def stat_key(path):
    """The cheap identity of a file: absolute path, size, mtime.

    A framemd5 walk over 5 GB of ProRes is minutes, and that cost is exactly
    what tempts a person to patch a check and skip the re-run. Caching on this
    key makes a corrected run finish in seconds.
    """
    st = os.stat(path)
    return {"path": os.path.abspath(path), "size": st.st_size,
            "mtime_ns": st.st_mtime_ns}


def cache_dir():
    root = os.environ.get("POST_CACHE",
                          os.path.join(os.path.expanduser("~"), ".cache",
                                       "postproduction"))
    os.makedirs(root, exist_ok=True)
    return root


# ---------------------------------------------------------------- provenance


class Measurement:
    """A number that remembers where it came from.

    The decode path matters: hashing a seeked frame against the same file's own
    framemd5 does not work, because the two paths choose different pixel
    formats, and two decoders disagree by one to two code levels purely from
    YUV to RGB conversion. So a measurement carries its path and refuses to be
    compared across paths.
    """

    __slots__ = ("value", "source", "path", "unit", "note")

    def __init__(self, value, source, path, unit="", note=""):
        self.value = value
        self.source = str(source)     # the file it was measured on
        self.path = path              # the decode path, e.g. 'ffmpeg:rgb48le'
        self.unit = unit
        self.note = note

    def as_dict(self):
        return {"value": self.value, "source": self.source, "decode_path": self.path,
                "unit": self.unit, "note": self.note}

    def __repr__(self):
        return f"<{self.value}{self.unit} on {os.path.basename(self.source)} via {self.path}>"


def require_same_path(*measurements):
    """Refuse to compare measurements that came through different decoders."""
    paths = {m.path for m in measurements}
    if len(paths) > 1:
        raise ValueError(
            "These numbers came through different decode paths "
            f"({', '.join(sorted(paths))}) and cannot be compared. "
            "Re-measure both through one path."
        )
    return True


# ---------------------------------------------------------------- output


def emit(obj, as_json, printer=None):
    """Print JSON or hand off to a human printer."""
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    elif printer is not None:
        printer(obj)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    return 0


def add_json(parser):
    parser.add_argument("--json", action="store_true", help="Structured output")
    return parser


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def main_guard(fn, argv=None):
    """Turn expected errors into clean messages instead of tracebacks."""
    try:
        return fn(argv)
    except ToolMissing as exc:
        fail(str(exc))
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        fail(str(exc))
    except BrokenPipeError:
        return 0


def parser_for(doc):
    ap = argparse.ArgumentParser(description=doc.splitlines()[0])
    return ap
