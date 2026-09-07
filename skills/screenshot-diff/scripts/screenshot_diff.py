#!/usr/bin/env python3
"""Screenshot comparison tool."""
import argparse
import subprocess
import uuid
from PIL import Image


def compare_images(image1_path, image2_path, output_path=None, threshold=0.1):
    """Compare two images and generate diff."""
    # Use ImageMagick compare
    diff_path = output_path or f'/tmp/diff_{uuid.uuid4().hex[:8]}.png'

    # Both inputs must be readable images. Two missing files used to compare
    # as a match, because the parse failure became -1 and -1 is under any
    # threshold (audit F20, 2026-09-06).
    for path in (image1_path, image2_path):
        try:
            with Image.open(path) as probe:
                probe.verify()
        except (OSError, ValueError) as e:
            raise SystemExit(f"Error: cannot read image {path}: {e}")

    # Get difference metric. compare exits 0 for identical, 1 for different,
    # 2 for a tool error: only 2 is an error.
    result = subprocess.run(
        ['compare', '-metric', 'RMSE', image1_path, image2_path, 'null:'],
        capture_output=True,
        text=True
    )
    if result.returncode not in (0, 1):
        raise SystemExit(f"Error: compare failed (exit {result.returncode}): "
                         f"{result.stderr.strip()[:300]}")

    # Parse result (format: "12345 (0.123)")
    error_output = result.stderr.strip()
    try:
        # Extract percentage from output
        if '(' in error_output:
            diff_value = float(error_output.split('(')[1].rstrip(')'))
        else:
            diff_value = float(error_output.split()[0])
    except (ValueError, IndexError):
        raise SystemExit(f"Error: could not parse compare output: {error_output[:300]}")

    # Generate visual diff if images differ
    if diff_value > threshold:
        viz = subprocess.run([
            'compare',
            image1_path, image2_path,
            '-compose', 'src',
            '-highlight-color', 'red',
            diff_path
        ], capture_output=True, text=True)
        if viz.returncode not in (0, 1):
            raise SystemExit(f"Error: diff image failed (exit {viz.returncode}): "
                             f"{viz.stderr.strip()[:300]}")
        print(f"Difference: {diff_value:.4f} ({diff_value*100:.2f}%)")
        print(f"Diff image saved: {diff_path}")
        return False
    else:
        print(f"Images match within threshold ({threshold})")
        print(f"Actual difference: {diff_value:.4f}")
        return True


def composite_diff(image1_path, image2_path, output_path):
    """Create side-by-side comparison."""
    img1 = Image.open(image1_path)
    img2 = Image.open(image2_path)

    # Ensure same size
    width = max(img1.width, img2.width)
    height = max(img1.height, img2.height)

    # Create composite
    composite = Image.new('RGB', (width * 2 + 10, height), (128, 128, 128))
    composite.paste(img1, (0, 0))
    composite.paste(img2, (width + 10, 0))
    composite.save(output_path)

    print(f"Side-by-side comparison saved: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Screenshot Diff')
    parser.add_argument('command', choices=['compare', 'composite'])
    parser.add_argument('image1', help='First image')
    parser.add_argument('image2', help='Second image')
    parser.add_argument('--output', '-o', help='Output diff image')
    parser.add_argument('--threshold', '-t', type=float, default=0.1)

    args = parser.parse_args()

    if args.command == 'compare':
        # Exit 1 on a difference so a pipeline can gate on it.
        if not compare_images(args.image1, args.image2, args.output, args.threshold):
            raise SystemExit(1)
    elif args.command == 'composite':
        composite_diff(args.image1, args.image2, args.output or f'/tmp/composite_{uuid.uuid4().hex[:8]}.png')
