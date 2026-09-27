#!/usr/bin/env python3
"""
GIMP batch processing wrapper.
For simple operations, uses ImageMagick. For complex filters, invokes GIMP.

The GIMP side detects version 2 or 3 before choosing its batch exit flags.
Scripts still need to use the procedure names of the installed version.

Every ImageMagick call starts with -auto-orient (a phone photo's EXIF
rotation is applied to the pixels; -thumbnail strips the tag, so thumbnails
came out sideways) and a JPEG output has its transparency flattened onto
white (it turned black). Linux bot review 2026-09-27.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

JPEG = {".jpg", ".jpeg"}

def _magick_binary():
    """ImageMagick's command line, under whichever name is installed.

    ImageMagick 7 renamed `convert` to `magick` and only keeps `convert` as a
    deprecated alias, so resolving it here rather than hard-coding one name.
    """
    binary = shutil.which("magick") or shutil.which("convert")
    if not binary:
        sys.exit("Error: ImageMagick is not installed (no magick or convert)")
    return binary


def _magick(input_path, operations, output_path):
    flatten = ["-background", "white", "-alpha", "remove", "-alpha", "off"] \
        if Path(output_path).suffix.lower() in JPEG else []
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_magick_binary(), str(input_path), "-auto-orient", *operations, *flatten,
                        str(output_path)], capture_output=True, text=True)
    if r.returncode != 0 or not Path(output_path).exists():
        sys.exit(f"Error: ImageMagick failed on {input_path}: {r.stderr.strip()[-400:]}")


def resize_image(input_path, output_path, width=None, height=None):
    """Resize keeping the aspect ratio: to a width, a height, or inside both.

    A width alone used to become a width x width box, so a 1000x2000
    portrait asked for 800 wide came back 400x800.
    """
    size = f"{width}x{height}" if width and height else (f"{width}" if width else f"x{height}")
    _magick(input_path, ["-resize", size], output_path)
    print(f"Resized: {output_path}")


def convert_format(input_path, output_path, quality=85):
    """Convert image format"""
    _magick(input_path, ["-quality", str(quality)], output_path)
    print(f"Converted: {output_path}")


def crop_square(input_path, output_path):
    """Crop image to square (center crop)"""
    _magick(input_path, ["-gravity", "center", "-extent", "1:1"], output_path)
    print(f"Cropped: {output_path}")


def apply_blur(input_path, output_path, radius=5):
    """Apply Gaussian blur"""
    _magick(input_path, ["-blur", f"0x{radius}"], output_path)
    print(f"Blurred: {output_path}")


def apply_sharpen(input_path, output_path, amount=1):
    """Apply sharpening"""
    _magick(input_path, ["-sharpen", f"0x{amount}"], output_path)
    print(f"Sharpened: {output_path}")


def create_thumbnail(input_path, output_path, size=256):
    """Create thumbnail preserving aspect ratio"""
    _magick(input_path, ["-thumbnail", f"{size}x{size}"], output_path)
    print(f"Thumbnail: {output_path}")


def batch_process(input_dir, output_dir, operation, **kwargs):
    """Process all images in directory"""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.tiff', '.bmp'}

    done = 0
    for img in sorted(input_path.iterdir()):
        if img.suffix.lower() in extensions:
            out_file = output_path / img.name
            done += 1

            # convert had no branch here: a batch convert wrote nothing and
            # said nothing
            if operation == 'convert':
                out_file = out_file.with_suffix('.' + kwargs.get('format', 'jpg').lstrip('.'))
                convert_format(str(img), str(out_file), kwargs.get('quality', 85))
            elif operation == 'resize':
                resize_image(str(img), str(out_file),
                           kwargs.get('width', 800), kwargs.get('height'))
            elif operation == 'thumbnail':
                create_thumbnail(str(img), str(out_file), kwargs.get('size', 256))
            elif operation == 'blur':
                apply_blur(str(img), str(out_file), kwargs.get('radius', 5))
            elif operation == 'sharpen':
                apply_sharpen(str(img), str(out_file), kwargs.get('amount', 1))
            elif operation == 'square':
                crop_square(str(img), str(out_file))
    if not done:
        sys.exit(f"Error: no images ({', '.join(sorted(extensions))}) in {input_dir}")
    print(f"{done} image(s) -> {output_dir}")


def gimp_binary():
    """The headless GIMP on this machine.

    On Linux `gimp-console` is on PATH. On macOS the Homebrew wrapper only
    exposes the GUI binary, so the console build is reached by its path inside
    the app bundle.
    """
    found = shutil.which("gimp-console")
    if found:
        return found
    bundled = "/Applications/GIMP.app/Contents/MacOS/gimp-console"
    if os.path.exists(bundled):
        return bundled
    return shutil.which("gimp") or "gimp"


def gimp_script(script, timeout=300):
    """Run one Script-Fu expression headlessly and return GIMP's output.

    GIMP 3 needs --quit even when a batch command fails. GIMP 2 rejects that
    flag and instead needs a final (gimp-quit 0). Its batch errors can return
    exit zero, so check the diagnostic as well. The timeout bounds both paths.
    """
    binary = gimp_binary()
    env = {**os.environ, "LC_ALL": "C"}
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True,
        timeout=min(timeout, 10), env=env,
    )
    reported = (version.stdout or "") + (version.stderr or "")
    match = re.search(r"\bversion\s+([23])\.", reported)
    if version.returncode != 0 or not match:
        raise RuntimeError(f"Cannot determine GIMP 2 or 3 version: {reported.strip()[-2000:]}")
    major = int(match.group(1))
    cmd = [
        binary, "-i", "--new-instance",
        "--batch-interpreter=plug-in-script-fu-eval",
    ]
    if major == 3:
        cmd += ["--quit", "-b", script]
    else:
        cmd += ["-b", script, "-b", "(gimp-quit 0)"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    output = (done.stdout or "") + (done.stderr or "")
    batch_error = re.search(r"batch command experienced (?:an execution|a calling) error", output)
    if done.returncode != 0 or batch_error:
        raise RuntimeError(
            f"GIMP batch failed (exit {done.returncode}):\n{output.strip()[-2000:]}"
        )
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='GIMP/ImageMagick batch processor')
    parser.add_argument('operation', choices=['resize', 'convert', 'thumbnail',
                                               'blur', 'sharpen', 'square'])
    parser.add_argument('input', help='Input file or directory')
    parser.add_argument('output', help='Output file or directory')
    parser.add_argument('--width', type=int, help='resize: target width (keeps the aspect ratio)')
    parser.add_argument('--height', type=int, help='resize: target height; with --width, fit inside both')
    parser.add_argument('--format', default='jpg', help='batch convert: output format (default jpg)')
    parser.add_argument('--quality', type=int, default=85)
    parser.add_argument('--size', type=int, default=256)
    parser.add_argument('--radius', type=float, default=5)
    parser.add_argument('--amount', type=float, default=1)
    parser.add_argument('--batch', action='store_true', help='Process directory')

    args = parser.parse_args()
    if args.operation == 'resize' and not (args.width or args.height):
        parser.error('resize needs --width and/or --height')

    if args.batch or os.path.isdir(args.input):
        batch_process(args.input, args.output, args.operation,
                     width=args.width, height=args.height, quality=args.quality, format=args.format,
                     size=args.size, radius=args.radius, amount=args.amount)
    else:
        if args.operation == 'resize':
            resize_image(args.input, args.output, args.width, args.height)
        elif args.operation == 'convert':
            convert_format(args.input, args.output, args.quality)
        elif args.operation == 'thumbnail':
            create_thumbnail(args.input, args.output, args.size)
        elif args.operation == 'blur':
            apply_blur(args.input, args.output, args.radius)
        elif args.operation == 'sharpen':
            apply_sharpen(args.input, args.output, args.amount)
        elif args.operation == 'square':
            crop_square(args.input, args.output)
