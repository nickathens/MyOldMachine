#!/usr/bin/env python3
"""Sprite sheet generation and manipulation."""
import argparse
import glob
import math
import os
import re
import sys

from PIL import Image


def natural_key(path):
    """frame_2 before frame_10: a plain sort put frame_10 between frame_1 and
    frame_2 and scrambled every animation numbered without leading zeros."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))]


def create_sprite_sheet(image_paths, output_path, cols=4, padding=0):
    """Combine images into a sprite sheet, one cell per frame in natural order."""
    images = [Image.open(p) for p in sorted(image_paths, key=natural_key)]

    if not images:
        sys.exit("Error: no images found")

    # Cells are as big as the biggest frame; smaller frames sit centred in
    # theirs (the first frame's size used to be taken for all, so bigger ones
    # spilled into their neighbours)
    width = max(img.width for img in images)
    height = max(img.height for img in images)
    if any(img.size != (width, height) for img in images):
        print(f"note: frames differ in size; cells are {width}x{height} with smaller frames centred",
              file=sys.stderr)
    rows = math.ceil(len(images) / cols)

    # Create sheet
    sheet_width = cols * (width + padding) - padding
    sheet_height = rows * (height + padding) - padding
    sheet = Image.new('RGBA', (sheet_width, sheet_height), (0, 0, 0, 0))

    for i, img in enumerate(images):
        x = (i % cols) * (width + padding) + (width - img.width) // 2
        y = (i // cols) * (height + padding) + (height - img.height) // 2
        sheet.paste(img.convert('RGBA'), (x, y))

    sheet.save(output_path)
    print(f"Created sprite sheet: {output_path} ({cols}x{rows}, {len(images)} frames)")


def split_sprite_sheet(sheet_path, output_dir, cols, rows, padding=0):
    """Split sprite sheet into individual frames, named by their cell.

    Frames are numbered by grid position (row * cols + col), so an empty cell
    leaves a gap instead of shifting every later frame down by one, which is
    what happened when empties were skipped and the rest renumbered.
    """
    os.makedirs(output_dir, exist_ok=True)

    sheet = Image.open(sheet_path)
    width, height = sheet.size

    if (width + padding) % cols or (height + padding) % rows:
        print(f"note: {width}x{height} does not divide into {cols}x{rows} cells"
              f"{f' with {padding}px padding' if padding else ''}; the remainder is left out", file=sys.stderr)
    frame_width = (width + padding) // cols - padding
    frame_height = (height + padding) // rows - padding

    saved = 0
    for row in range(rows):
        for col in range(cols):
            x = col * (frame_width + padding)
            y = row * (frame_height + padding)
            frame = sheet.crop((x, y, x + frame_width, y + frame_height))

            # Skip empty cells (a part filled last row), keeping the numbering
            if frame.getbbox():
                output_path = os.path.join(output_dir, f"frame_{row * cols + col:04d}.png")
                frame.save(output_path)
                saved += 1

    print(f"Extracted {saved} frames of {frame_width}x{frame_height} to {output_dir}")


def resize_sprite(input_path, output_path, scale=2):
    """Resize sprite using nearest-neighbor (pixel-perfect)."""
    img = Image.open(input_path)
    new_size = (img.width * scale, img.height * scale)
    resized = img.resize(new_size, Image.Resampling.NEAREST)
    resized.save(output_path)
    print(f"Resized: {input_path} -> {output_path} ({scale}x)")


def pixelate(input_path, output_path, pixel_size=8):
    """Pixelate an image: each block takes the mean colour of its pixels."""
    img = Image.open(input_path)
    if pixel_size < 1:
        sys.exit("Error: --pixel-size must be 1 or more")

    # Shrink with a box average (NEAREST took one pixel per block, so a fine
    # pattern came out speckled instead of its mean)
    small = img.resize(
        (max(1, img.width // pixel_size), max(1, img.height // pixel_size)),
        Image.Resampling.BOX
    )

    # Enlarge back
    result = small.resize(img.size, Image.Resampling.NEAREST)
    result.save(output_path)
    print(f"Pixelated: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sprite Tools')
    parser.add_argument('command', choices=['sheet', 'split', 'resize', 'pixelate'])
    parser.add_argument('input', nargs='+', help='Input file(s) or pattern')
    parser.add_argument('--output', '-o', required=True, help='Output file or directory')
    parser.add_argument('--cols', type=int, default=4, help='Columns in sheet')
    parser.add_argument('--rows', type=int, default=4, help='Rows (for split)')
    parser.add_argument('--scale', type=int, default=2, help='Scale factor')
    parser.add_argument('--padding', type=int, default=0, help='Padding between sprites')
    parser.add_argument('--pixel-size', type=int, default=8, help='Pixel size for pixelate')

    args = parser.parse_args()

    # Expand globs (the shell may already have)
    files = []
    for pattern in args.input:
        files.extend(glob.glob(pattern) or ([pattern] if os.path.isfile(pattern) else []))
    if not files:
        sys.exit(f"Error: nothing matches {' '.join(args.input)}")

    if args.command == 'sheet':
        create_sprite_sheet(files, args.output, args.cols, args.padding)
    elif args.command == 'split':
        split_sprite_sheet(files[0], args.output, args.cols, args.rows, args.padding)
    elif args.command == 'resize':
        resize_sprite(files[0], args.output, args.scale)
    elif args.command == 'pixelate':
        pixelate(files[0], args.output, args.pixel_size)
