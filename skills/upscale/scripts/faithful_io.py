"""Reading and writing images without losing what the pixels mean.

Three losses the upscalers had, all measured in the 2026-09-27 review:
Pillow opens a 16-bit RGB PNG or TIFF as 8-bit RGB without a word (so a deep
master came back 8-bit even on the "depth keeping" route); neither script
applied EXIF orientation (a portrait phone photo came back sideways, and the
PNG it was written to has no orientation tag to rescue it); and every output
dropped the ICC profile (an Adobe RGB master came back untagged, so it shows
as sRGB). cv2 holds 16 bits but never writes a profile, so PNGs get their iCCP
chunk inserted here.
"""

import io
import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def exif_orientation(path):
    """The EXIF orientation tag (1 to 8), 1 when there is none."""
    try:
        with Image.open(path) as im:
            value = int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1
    return value if 1 <= value <= 8 else 1


def orient(arr, orientation):
    """Apply an EXIF orientation to an HxW(xC) array, as ImageOps.exif_transpose does."""
    ops = {
        2: lambda a: a[:, ::-1],
        3: lambda a: a[::-1, ::-1],
        4: lambda a: a[::-1, :],
        5: lambda a: np.swapaxes(a, 0, 1),
        6: lambda a: np.rot90(a, -1),
        7: lambda a: np.rot90(np.swapaxes(a, 0, 1), 2),
        8: lambda a: np.rot90(a, 1),
    }
    return np.ascontiguousarray(ops[orientation](arr)) if orientation in ops else arr


def icc_profile(path):
    try:
        with Image.open(path) as im:
            return im.info.get("icc_profile")
    except Exception:
        return None


def icc_for(icc, channels):
    """The profile when its colour space fits the output (RGB for 3 or 4 channels, GRAY for 1 or 2)."""
    if not icc:
        return None
    try:
        from PIL import ImageCms

        space = ImageCms.ImageCmsProfile(io.BytesIO(icc)).profile.xcolor_space.strip()
    except Exception:
        return None
    return icc if space == ("RGB" if channels >= 3 else "GRAY") else None


def read_deep_colour(path):
    """A 16-bit colour image as cv2 holds it (BGR or BGRA, uint16), or None.

    Pillow's mode cannot tell a deep colour master from an 8-bit one: it
    reports "RGB" for both.
    """
    import cv2

    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.ndim != 3 or raw.dtype != np.uint16:
        return None
    return raw


def png_with_icc(png, icc):
    """PNG bytes with an iCCP chunk right after IHDR."""
    if not icc or png[:8] != PNG_SIGNATURE:
        return png
    data = b"ICC profile\x00\x00" + zlib.compress(icc)
    chunk = (struct.pack(">I", len(data)) + b"iCCP" + data
             + struct.pack(">I", zlib.crc32(b"iCCP" + data) & 0xFFFFFFFF))
    ihdr_end = 8 + 4 + 4 + struct.unpack(">I", png[8:12])[0] + 4
    return png[:ihdr_end] + chunk + png[ihdr_end:]


def write_cv2(path, arr, icc=None):
    """Write a cv2 layout array (BGR or BGRA, 8 or 16 bit), keeping the profile.

    Raises RuntimeError when nothing was written: cv2.imwrite only returns
    False, which is how a failed write used to print "Saved".
    """
    import cv2

    path = Path(path)
    channels = arr.shape[2] if arr.ndim == 3 else 1
    icc = icc_for(icc, channels)
    try:
        if path.suffix.lower() == ".png":
            ok, buf = cv2.imencode(".png", arr)
            if ok:
                path.write_bytes(png_with_icc(buf.tobytes(), icc))
                return
        elif icc and arr.dtype == np.uint8 and channels == 3:
            Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)).save(path, icc_profile=icc, quality=95)
            return
        elif cv2.imwrite(str(path), arr):
            return
    except (cv2.error, OSError, ValueError) as exc:
        raise RuntimeError(f"could not write {path}: {exc}") from None
    raise RuntimeError(f"could not write {path}")
