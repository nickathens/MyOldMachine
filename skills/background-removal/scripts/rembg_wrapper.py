#!/usr/bin/env python3
"""Background removal with rembg.

Linux bot review 2026-09-27: the output format follows the output name (a .jpg
got PNG bytes; JPEG has no alpha, so it is flattened onto --background,
white by default), the colour profile is carried over (a wide-gamut photo
came back untagged, so its colours shifted), batch runs one model session
(it reloaded the model for every image), and two inputs that differ only by
extension no longer write the same output file. SKILL.md documented the
`rembg` command line tool, which does not start without rembg's [cli]
extras ("No module named 'filetype'"); this script is the tool.
"""
import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageColor, ImageOps

INPUTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
WITH_ALPHA = {'.png': 'PNG', '.webp': 'WEBP', '.tif': 'TIFF', '.tiff': 'TIFF'}
FLAT_ONLY = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.bmp': 'BMP'}

_sessions = {}

# Closed-form alpha matting builds a matrix over the edge band at full size:
# about 1.8 GB at 2 MP, 3 GB at 4.3 MP, and a 12 MP photo passed 6 GB in six
# seconds (OOM-killed in a capped scope, 2026-10-07; as a child of the bot's
# service on Linux, an OOM kill stops the whole service). Matting runs on a copy
# this big at most and its edge is scaled back up onto the full size picture
# (Linux bot sweep 2026-10-07).
MATTING_MAX_PIXELS = 2_000_000


def session(model):
    """One rembg session per model: loading the network is most of the time per image."""
    if model not in _sessions:
        from rembg import new_session
        _sessions[model] = new_session(model)
    return _sessions[model]


def icc_space(icc):
    """The colour space an ICC profile describes: 'RGB', 'CMYK', 'GRAY' (header bytes 16-20)."""
    return icc[16:20].decode('ascii', 'replace').strip() if icc and len(icc) >= 20 else None


def load(path):
    """The image upright, as RGB(A) or L, and the RGB profile to carry over (or None)."""
    img = ImageOps.exif_transpose(Image.open(path))
    icc = img.info.get('icc_profile')
    if img.mode == 'CMYK':
        if icc_space(icc) == 'CMYK':
            srgb = ImageCms.createProfile('sRGB')
            img = ImageCms.profileToProfile(img, io.BytesIO(icc), srgb, outputMode='RGB')
        else:
            img = img.convert('RGB')
        icc = None
    elif img.mode in ('I;16', 'I;16B', 'I;16L', 'I'):
        # 16 bit grey: Pillow's convert clips at 255, which turns most of the picture white
        img = Image.fromarray((np.asarray(img, dtype=np.uint32) >> 8).clip(0, 255).astype(np.uint8), 'L')
    return img, (icc if icc_space(icc) == 'RGB' else None)


def save(img, output, icc, background=None):
    output = Path(output)
    suffix = output.suffix.lower()
    fmt = WITH_ALPHA.get(suffix) or FLAT_ONLY.get(suffix)
    if fmt is None:
        sys.exit(f"Error: unsupported output type {suffix or '(none)'}; use .png, .webp, .tif or .jpg")
    if suffix in FLAT_ONLY and img.mode in ('RGBA', 'LA') and not background:
        background = 'white'
    if background and img.mode in ('RGBA', 'LA'):
        plate = Image.new('RGBA', img.size, ImageColor.getrgb(background))
        img = Image.alpha_composite(plate, img.convert('RGBA')).convert('RGB')
    options = {'icc_profile': icc} if icc and img.mode in ('RGB', 'RGBA') else {}
    if fmt == 'JPEG':
        options['quality'] = 95
    if fmt == 'WEBP':
        options['lossless'] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output, fmt, **options)


def cutout(input_path, model, alpha_matting=False, only_mask=False):
    import rembg
    img, icc = load(input_path)
    if alpha_matting and not only_mask and img.width * img.height > MATTING_MAX_PIXELS:
        scale = (MATTING_MAX_PIXELS / (img.width * img.height)) ** 0.5
        small = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                           Image.Resampling.LANCZOS)
        matted = rembg.remove(small, session=session(model), alpha_matting=True)
        result = img.convert('RGBA')
        result.putalpha(matted.getchannel('A').resize(img.size, Image.Resampling.LANCZOS))
        print(f"note: alpha matting ran at {small.width}x{small.height} to stay within memory; "
              "its edge was scaled up to the full picture", file=sys.stderr)
        return result, icc
    result = rembg.remove(img, session=session(model), alpha_matting=alpha_matting, only_mask=only_mask)
    return result, icc


def remove_background(input_path, output_path, model='u2net', background=None, alpha_matting=False):
    """Remove the background from one image."""
    result, icc = cutout(input_path, model, alpha_matting)
    save(result, output_path, icc, background)
    print(f"Processed: {input_path} -> {output_path}")


def batch_outputs(files, output_dir, ext):
    """Output path per input; inputs that share a name but not an extension keep both."""
    stems = {}
    for f in files:
        stems.setdefault(f.stem.lower(), []).append(f)
    out = {}
    for f in files:
        clash = len(stems[f.stem.lower()]) > 1
        name = f"{f.stem}_{f.suffix.lstrip('.').lower()}" if clash else f.stem
        out[f] = Path(output_dir) / f"{name}{ext}"
    return out


def batch_remove(input_dir, output_dir, model='u2net', background=None, alpha_matting=False, ext='.png'):
    """Remove the background from every image in a folder; returns the number that failed."""
    files = sorted(p for p in Path(input_dir).iterdir() if p.is_file() and p.suffix.lower() in INPUTS)
    if not files:
        sys.exit(f"Error: no images in {input_dir} ({', '.join(sorted(INPUTS))})")
    outputs = batch_outputs(files, output_dir, ext)
    clobbered = [src.name for src, dst in outputs.items() if dst.resolve() in {f.resolve() for f in files}]
    if clobbered:
        sys.exit(f"Error: the results would be written over the originals ({', '.join(clobbered[:5])}); "
                 "give a different output folder")
    failed = 0
    for src, dst in outputs.items():
        try:
            remove_background(str(src), str(dst), model, background, alpha_matting)
        except Exception as e:  # one bad file must not stop the folder
            failed += 1
            print(f"Failed: {src}: {e}", file=sys.stderr)
    print(f"{len(files) - failed} of {len(files)} done")
    return failed


def get_mask(input_path, output_path, model='u2net'):
    """Save the alpha mask only (white is kept)."""
    result, _ = cutout(input_path, model, only_mask=True)
    save(result, output_path, None)
    print(f"Mask saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Background Removal')
    parser.add_argument('command', choices=['remove', 'batch', 'mask'])
    parser.add_argument('input', help='Input image or directory')
    parser.add_argument('output', help='Output image or directory (.png, .webp, .tif keep the '
                                       'transparency; .jpg is flattened onto --background)')
    parser.add_argument('--model', default='u2net',
                        help='rembg model (default u2net, already downloaded; birefnet-general is sharper '
                             'but downloads its own large model on first use)')
    parser.add_argument('--background', help='Fill the removed area with this colour (name or #hex) '
                                             'instead of transparency')
    parser.add_argument('--alpha-matting', action='store_true', help='Finer edges (hair, fur); slower')
    parser.add_argument('--format', choices=['png', 'webp', 'tif', 'jpg'], default='png',
                        help='batch: output type (default png)')
    args = parser.parse_args()

    from rembg.sessions import sessions_names
    if args.model not in sessions_names:
        parser.error(f"unknown model {args.model}; one of: {', '.join(sessions_names)}")
    if args.background:
        try:
            ImageColor.getrgb(args.background)
        except ValueError:
            parser.error(f"not a colour: {args.background}")
    source = Path(args.input)
    if args.command == 'batch' and not source.is_dir():
        parser.error(f"batch needs a folder: {args.input}")
    if args.command != 'batch' and not source.is_file():
        parser.error(f"no such file: {args.input}")

    if args.command == 'remove':
        remove_background(args.input, args.output, args.model, args.background, args.alpha_matting)
    elif args.command == 'batch':
        sys.exit(1 if batch_remove(args.input, args.output, args.model, args.background,
                                   args.alpha_matting, '.' + args.format) else 0)
    elif args.command == 'mask':
        get_mask(args.input, args.output, args.model)


if __name__ == '__main__':
    main()
