#!/usr/bin/env python3
"""Screenshot comparison tool."""
import argparse
import uuid

import numpy as np
from PIL import Image


def _load(path):
    """RGB pixels of an image (transparency over white), or exit if unreadable."""
    try:
        with Image.open(path) as probe:
            probe.verify()
        img = Image.open(path).convert("RGBA")
    except (OSError, ValueError) as e:
        raise SystemExit(f"Error: cannot read image {path}: {e}")
    flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return np.asarray(Image.alpha_composite(flat, img).convert("RGB"), dtype=np.int16)


def compare_images(image1_path, image2_path, output_path=None, threshold=0.0005, fuzz=0.02):
    """Compare two screenshots; True when they match.

    Counts the pixels whose colour moved by more than fuzz (2 percent of the
    range, which absorbs PNG and font rendering noise) and calls the pair
    different when that share of the frame is above threshold (0.05 percent,
    about a thousand pixels on 1920x1080). A size change is always a
    difference. Linux bot review 2026-09-27: the old whole-image RMSE under 0.1
    passed a button that turned red (0.0318), and ImageMagick's compare
    searched a smaller image inside a taller one and reported a page that
    grew 220 px as 0.0000, a match.
    """
    diff_path = output_path or f'/tmp/diff_{uuid.uuid4().hex[:8]}.png'
    a, b = _load(image1_path), _load(image2_path)
    size_changed = a.shape != b.shape
    height, width = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])

    def pad(img):
        out = np.full((height, width, 3), (255, 0, 255), dtype=np.int16)   # magenta: area only one has
        out[:img.shape[0], :img.shape[1]] = img
        return out

    a, b = pad(a), pad(b)
    changed = np.abs(a - b).max(axis=2) > fuzz * 255
    share = float(changed.mean())

    if size_changed:
        print(f"Sizes differ: {Image.open(image1_path).size} vs {Image.open(image2_path).size}")
    if not size_changed and share <= threshold:
        print(f"Images match within threshold ({threshold * 100:.3f}% of pixels)")
        print(f"Changed pixels: {int(changed.sum())} ({share * 100:.4f}%)")
        return True

    ys, xs = np.nonzero(changed)
    print(f"Changed pixels: {int(changed.sum())} ({share * 100:.3f}%), "
          f"inside x {xs.min()}-{xs.max()}, y {ys.min()}-{ys.max()}")
    try:
        from scipy import ndimage
        labels, regions = ndimage.label(ndimage.binary_dilation(changed, iterations=4))
        # grouped with a little dilation, measured on the changed pixels themselves
        boxes = sorted((sl for sl in ndimage.find_objects(labels * changed) if sl), key=lambda sl: -(sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start))
        print(f"Changed regions: {regions}")
        for sl in boxes[:5]:
            print(f"  x {sl[1].start}-{sl[1].stop - 1}, y {sl[0].start}-{sl[0].stop - 1}")
    except ImportError:
        pass

    # The second image faded, with every changed pixel in red
    viz = (b * 0.35 + 255 * 0.65).astype(np.uint8)
    viz[changed] = (255, 0, 0)
    Image.fromarray(viz).save(diff_path)
    print(f"Diff image saved: {diff_path}")
    return False


def composite_diff(image1_path, image2_path, output_path):
    """Create side-by-side comparison."""
    img1 = Image.open(image1_path)
    img2 = Image.open(image2_path)

    # Ensure same size
    width = max(img1.width, img2.width)
    height = max(img1.height, img2.height)

    # Create composite
    composite = Image.new('RGB', (width * 2 + 10, height), (128, 128, 128))
    composite.paste(img1.convert('RGB'), (0, 0))
    composite.paste(img2.convert('RGB'), (width + 10, 0))
    composite.save(output_path)

    print(f"Side-by-side comparison saved: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Screenshot Diff')
    parser.add_argument('command', choices=['compare', 'composite'])
    parser.add_argument('image1', help='First image')
    parser.add_argument('image2', help='Second image')
    parser.add_argument('--output', '-o', help='Output diff image')
    parser.add_argument('--threshold', '-t', type=float, default=0.0005,
                        help='Share of pixels allowed to change (default 0.0005 = 0.05%%)')
    parser.add_argument('--fuzz', type=float, default=0.02,
                        help='Colour change per pixel that counts, as a share of the range (default 0.02)')

    args = parser.parse_args()

    if args.command == 'compare':
        # Exit 1 on a difference so a pipeline can gate on it.
        if not compare_images(args.image1, args.image2, args.output, args.threshold, args.fuzz):
            raise SystemExit(1)
    elif args.command == 'composite':
        composite_diff(args.image1, args.image2, args.output or f'/tmp/composite_{uuid.uuid4().hex[:8]}.png')
