#!/usr/bin/env python3
"""
GIMP batch processing wrapper.
For simple operations, uses ImageMagick. For complex filters, invokes GIMP.
"""
import subprocess
import argparse
import os
from pathlib import Path


def resize_image(input_path, output_path, width, height=None):
    """Resize image using ImageMagick (faster than GIMP for simple ops)"""
    size = f"{width}x{height}" if height else f"{width}x{width}"
    cmd = ["convert", input_path, "-resize", size, output_path]
    subprocess.run(cmd, check=True)
    print(f"Resized: {output_path}")


def convert_format(input_path, output_path, quality=85):
    """Convert image format"""
    cmd = ["convert", input_path, "-quality", str(quality), output_path]
    subprocess.run(cmd, check=True)
    print(f"Converted: {output_path}")


def crop_square(input_path, output_path):
    """Crop image to square (center crop)"""
    cmd = ["convert", input_path, "-gravity", "center",
           "-extent", "1:1", output_path]
    subprocess.run(cmd, check=True)
    print(f"Cropped: {output_path}")


def apply_blur(input_path, output_path, radius=5):
    """Apply Gaussian blur"""
    cmd = ["convert", input_path, "-blur", f"0x{radius}", output_path]
    subprocess.run(cmd, check=True)
    print(f"Blurred: {output_path}")


def apply_sharpen(input_path, output_path, amount=1):
    """Apply sharpening"""
    cmd = ["convert", input_path, "-sharpen", f"0x{amount}", output_path]
    subprocess.run(cmd, check=True)
    print(f"Sharpened: {output_path}")


def create_thumbnail(input_path, output_path, size=256):
    """Create thumbnail preserving aspect ratio"""
    cmd = ["convert", input_path, "-thumbnail", f"{size}x{size}", output_path]
    subprocess.run(cmd, check=True)
    print(f"Thumbnail: {output_path}")


def batch_process(input_dir, output_dir, operation, **kwargs):
    """Process all images in directory"""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.tiff', '.bmp'}

    for img in input_path.iterdir():
        if img.suffix.lower() in extensions:
            out_file = output_path / img.name

            if operation == 'resize':
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


def gimp_script(input_path, output_path, script):
    """Run arbitrary GIMP Script-Fu"""
    cmd = [
        "gimp", "-i", "-b", script, "-b", "(gimp-quit 0)"
    ]
    subprocess.run(cmd, check=True)
    print(f"GIMP script completed: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='GIMP/ImageMagick batch processor')
    parser.add_argument('operation', choices=['resize', 'convert', 'thumbnail',
                                               'blur', 'sharpen', 'square'])
    parser.add_argument('input', help='Input file or directory')
    parser.add_argument('output', help='Output file or directory')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int)
    parser.add_argument('--quality', type=int, default=85)
    parser.add_argument('--size', type=int, default=256)
    parser.add_argument('--radius', type=float, default=5)
    parser.add_argument('--amount', type=float, default=1)
    parser.add_argument('--batch', action='store_true', help='Process directory')

    args = parser.parse_args()

    if args.batch or os.path.isdir(args.input):
        batch_process(args.input, args.output, args.operation,
                     width=args.width, height=args.height,
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
