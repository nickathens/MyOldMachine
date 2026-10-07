#!/usr/bin/env python3
"""
Image editing operations using Pillow.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageEnhance, ImageOps

JPEG_EXTS = ('.jpg', '.jpeg')


def _bits_per_channel(path):
    """8 or 16, read with cv2: Pillow reports a 16-bit RGB PNG or TIFF as plain "RGB"."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    arr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if arr is None:
        return None
    return 16 if arr.dtype == np.uint16 else 8 if arr.dtype == np.uint8 else None


def _open(path):
    """Open an image the way it is meant to be seen.

    exif_transpose applies the camera's Orientation tag to the pixels. Without
    it a portrait phone photo was edited in sensor orientation and, since the
    tag was not written back, came out sideways (Linux bot review 2026-09-27)."""
    img = Image.open(path)
    if img.mode in ('RGB', 'RGBA') and _bits_per_channel(path) == 16:
        # Pillow has no 16-bit colour mode: it has already dropped to 8 bits
        print(f"note: {path} is 16 bits per channel; Pillow edits colour in 8 bits, so the "
              f"output is 8-bit. Keep the 16-bit master.", file=sys.stderr)
    upright = ImageOps.exif_transpose(img)
    if upright is not img:
        upright.info.update({k: v for k, v in img.info.items() if k in ('icc_profile', 'dpi')})
    return upright


def _save(img, path, source=None, quality=95):
    """Save keeping what PIL drops by default.

    The colour profile and the dpi of the source travel with the edit (a 300
    dpi key visual saved without dpi places at 72 dpi, four times too big; an
    image without its profile shifts colour), JPEG is written at `quality`
    rather than PIL's 75, and transparency is flattened onto white for JPEG
    instead of turning black or failing (mode LA)."""
    src = source if source is not None else img
    info = src.info
    ext = Path(path).suffix.lower()
    kwargs = {}
    # A profile describes one colour space: an RGB profile inside a greyscale
    # result (the grayscale filter) is invalid, so it only travels unchanged.
    if info.get('icc_profile') and _space(img.mode) == _space(src.mode):
        kwargs['icc_profile'] = info['icc_profile']
    if info.get('dpi'):
        kwargs['dpi'] = info['dpi']
    if ext in JPEG_EXTS:
        if img.mode in ('RGBA', 'LA', 'P', 'PA') or (img.mode == 'P' and 'transparency' in img.info):
            rgba = img.convert('RGBA')
            flat = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(flat, rgba).convert('RGB')
        elif img.mode not in ('RGB', 'L', 'CMYK'):
            img = img.convert('RGB')
        kwargs['quality'] = quality
    elif ext == '.webp':
        # PIL, not ImageMagick: IM 6.9 ignores -quality for WebP (notes, 2026-08-10)
        kwargs.update(quality=quality, method=6)
    img.save(path, **kwargs)
    return img


def _space(mode):
    return 'gray' if mode in ('1', 'L', 'LA', 'I', 'I;16', 'F') else 'cmyk' if mode == 'CMYK' else 'rgb'


def cmd_info(args):
    """Get image info (size as displayed, after the EXIF orientation)."""
    img = Image.open(args.input)

    shown = ImageOps.exif_transpose(img).size
    info = {
        "format": img.format,
        "mode": img.mode,
        "width": shown[0],
        "height": shown[1],
        "size": list(shown),
        "dpi": [round(float(v), 2) for v in img.info["dpi"]] if img.info.get("dpi") else None,
        "icc_profile": bool(img.info.get("icc_profile")),
        "bits_per_channel": _bits_per_channel(args.input),
    }

    if hasattr(img, 'n_frames'):
        info["frames"] = img.n_frames

    print(json.dumps(info, indent=2, ensure_ascii=False))


def cmd_resize(args):
    """Resize image."""
    img = _open(args.input)

    if args.width and args.height:
        new_size = (args.width, args.height)
    elif args.width:
        ratio = args.width / img.size[0]
        new_size = (args.width, int(img.size[1] * ratio))
    elif args.height:
        ratio = args.height / img.size[1]
        new_size = (int(img.size[0] * ratio), args.height)
    else:
        print("Error: Specify --width and/or --height", file=sys.stderr)
        sys.exit(1)

    resized = img.resize(new_size, Image.Resampling.LANCZOS)
    _save(resized, args.output, img)
    print(f"Resized to {new_size[0]}x{new_size[1]} -> {args.output}")


def cmd_crop(args):
    """Crop image."""
    img = _open(args.input)

    if args.square:
        # Crop to center square
        min_dim = min(img.size)
        left = (img.size[0] - min_dim) // 2
        top = (img.size[1] - min_dim) // 2
        box = (left, top, left + min_dim, top + min_dim)
    elif args.box:
        box = tuple(args.box)
    else:
        print("Error: Specify --box L T R B or --square", file=sys.stderr)
        sys.exit(1)

    cropped = img.crop(box)
    _save(cropped, args.output, img)
    print(f"Cropped to {cropped.size[0]}x{cropped.size[1]} -> {args.output}")


def cmd_rotate(args):
    """Rotate image."""
    img = _open(args.input)
    rotated = img.rotate(args.angle, expand=True, resample=Image.Resampling.BICUBIC)
    _save(rotated, args.output, img)
    print(f"Rotated {args.angle} degrees -> {args.output}")


def cmd_convert(args):
    """Convert image format."""
    img = _open(args.input)

    output_ext = Path(args.output).suffix.lower()
    _save(img, args.output, img, quality=args.quality)
    print(f"Converted to {output_ext} -> {args.output}")


def cmd_filter(args):
    """Apply filter to image."""
    img = _open(args.input)

    filters = {
        'blur': ImageFilter.BLUR,
        'sharpen': ImageFilter.SHARPEN,
        'contour': ImageFilter.CONTOUR,
        'detail': ImageFilter.DETAIL,
        'edge_enhance': ImageFilter.EDGE_ENHANCE,
        'emboss': ImageFilter.EMBOSS,
        'smooth': ImageFilter.SMOOTH,
    }

    if args.filter == 'grayscale':
        result = ImageOps.grayscale(img)
    elif args.filter == 'sepia':
        gray = ImageOps.grayscale(img)
        result = ImageOps.colorize(gray, '#704214', '#C0A080')
    elif args.filter == 'invert':
        if img.mode == 'RGBA':
            r, g, b, a = img.split()
            rgb = Image.merge('RGB', (r, g, b))
            inverted = ImageOps.invert(rgb)
            r, g, b = inverted.split()
            result = Image.merge('RGBA', (r, g, b, a))
        else:
            result = ImageOps.invert(img.convert('RGB'))
    elif args.filter in filters:
        result = img.filter(filters[args.filter])
    else:
        print(f"Unknown filter: {args.filter}", file=sys.stderr)
        print(f"Available: {', '.join(list(filters.keys()) + ['grayscale', 'sepia', 'invert'])}", file=sys.stderr)
        sys.exit(1)

    _save(result, args.output, img)
    print(f"Applied {args.filter} filter -> {args.output}")


def cmd_adjust(args):
    """Adjust brightness/contrast."""
    img = source = _open(args.input)

    # `is not None`, not truthiness: brightness 0 is a real request (black)
    # and was silently ignored (audit F27, 2026-09-06).
    if args.brightness is not None:
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(args.brightness)

    if args.contrast is not None:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(args.contrast)

    if args.saturation is not None:
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(args.saturation)

    _save(img, args.output, source)
    print(f"Adjusted (b={args.brightness}, c={args.contrast}, s={args.saturation}) -> {args.output}")


def cmd_thumbnail(args):
    """Create thumbnail."""
    img = source = _open(args.input)
    img.thumbnail((args.size, args.size), Image.Resampling.LANCZOS)
    _save(img, args.output, source)
    print(f"Created {img.size[0]}x{img.size[1]} thumbnail -> {args.output}")


def cmd_composite(args):
    """Overlay one image on another."""
    base_src = _open(args.base)
    base = base_src.convert('RGBA')
    overlay = _open(args.overlay).convert('RGBA')

    position = tuple(args.position) if args.position else (0, 0)

    # Real alpha compositing. paste(overlay, pos, mask) writes the overlay's
    # own alpha into the result, so a half-transparent overlay on an opaque
    # base left the output half transparent (audit F27, 2026-09-06).
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
    layer.paste(overlay, position)
    result = Image.alpha_composite(base, layer)

    _save(result, args.output, base_src)
    print(f"Composited at {position} -> {args.output}")


def cmd_border(args):
    """Add border to image."""
    img = _open(args.input)

    bordered = ImageOps.expand(img, border=args.width, fill=args.color)
    _save(bordered, args.output, img)
    print(f"Added {args.width}px {args.color} border -> {args.output}")


def main():
    parser = argparse.ArgumentParser(description="Image editing tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Info
    p = subparsers.add_parser("info", help="Get image info")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_info)

    # Resize
    p = subparsers.add_parser("resize", help="Resize image")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--width", type=int, help="Width")
    p.add_argument("--height", type=int, help="Height")
    p.set_defaults(func=cmd_resize)

    # Crop
    p = subparsers.add_parser("crop", help="Crop image")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--box", type=int, nargs=4, metavar=('L', 'T', 'R', 'B'), help="Crop box")
    p.add_argument("--square", action="store_true", help="Crop to center square")
    p.set_defaults(func=cmd_crop)

    # Rotate
    p = subparsers.add_parser("rotate", help="Rotate image")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--angle", type=float, required=True, help="Rotation angle (degrees)")
    p.set_defaults(func=cmd_rotate)

    # Convert
    p = subparsers.add_parser("convert", help="Convert format")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--quality", type=int, default=85, help="JPG quality")
    p.set_defaults(func=cmd_convert)

    # Filter
    p = subparsers.add_parser("filter", help="Apply filter")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--filter", "-f", required=True, help="Filter name")
    p.set_defaults(func=cmd_filter)

    # Adjust
    p = subparsers.add_parser("adjust", help="Adjust brightness/contrast")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--brightness", type=float, default=1.0, help="Brightness (1.0=normal)")
    p.add_argument("--contrast", type=float, default=1.0, help="Contrast (1.0=normal)")
    p.add_argument("--saturation", type=float, default=1.0, help="Saturation (1.0=normal)")
    p.set_defaults(func=cmd_adjust)

    # Thumbnail
    p = subparsers.add_parser("thumbnail", help="Create thumbnail")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--size", type=int, default=200, help="Max dimension")
    p.set_defaults(func=cmd_thumbnail)

    # Composite
    p = subparsers.add_parser("composite", help="Overlay images")
    p.add_argument("base", help="Base image")
    p.add_argument("overlay", help="Overlay image")
    p.add_argument("output", help="Output file")
    p.add_argument("--position", type=int, nargs=2, metavar=('X', 'Y'), help="Position")
    p.set_defaults(func=cmd_composite)

    # Border
    p = subparsers.add_parser("border", help="Add border")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--width", type=int, default=10, help="Border width")
    p.add_argument("--color", default="black", help="Border color")
    p.set_defaults(func=cmd_border)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
