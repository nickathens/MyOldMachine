#!/usr/bin/env python
"""Verify a delivered master against its source, frame by frame, in native 10 bit.

Answers three questions on the DELIVERED file, not on an intermediate:
  1. which frames are bit exact against the source (untouched originals)
  2. how far the rebuilt frames depart, in 10 bit code values
  3. whether the rebuilt frames carry genuine 10 bit granularity, or are
     8 bit values shifted up (which shows as every code value being a
     multiple of 4)

The third question exists because the pixel format tag cannot answer it: an
8 bit rebuild written into a 10 bit container still reports `yuv420p10le`.
Decode the pictures and read the bottom two bits instead. Genuine 10 bit
spreads them near 0.25 in each of the four buckets; 8 bit shifted up reads
1.000 0.000 0.000 0.000.

Usage: cgverify10.py SRC MASTER WIDTH HEIGHT [NFRAMES]
"""
import subprocess
import sys

import numpy as np


def pipe(path, w, h):
    return subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
         "-pix_fmt", "yuv420p10le", "-"],
        stdout=subprocess.PIPE, bufsize=10 ** 8)


def main():
    src, mst, w, h = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    n = int(sys.argv[5]) if len(sys.argv) > 5 else 10 ** 6
    ysz = w * h
    fsz = (ysz + 2 * ((w // 2) * (h // 2))) * 2

    a, b = pipe(src, w, h), pipe(mst, w, h)
    exact, rebuilt = [], []
    for i in range(n):
        fa = a.stdout.read(fsz)
        fb = b.stdout.read(fsz)
        if len(fa) < fsz or len(fb) < fsz:
            break
        ya = np.frombuffer(fa, np.uint16, count=ysz)
        yb = np.frombuffer(fb, np.uint16, count=ysz)
        if fa == fb:
            exact.append(i)
        else:
            d = np.abs(ya.astype(np.int32) - yb.astype(np.int32))
            low = np.bincount(yb & 3, minlength=4) / yb.size
            rebuilt.append((i, int(d.max()), float(d.mean()),
                            int(yb.min()), int(yb.max()), low))
    a.kill()
    b.kill()

    print(f"frames compared      : {len(exact) + len(rebuilt)}")
    print(f"bit exact vs source  : {len(exact)}")
    print(f"rebuilt (differ)     : {len(rebuilt)}"
          f"  -> {[r[0] for r in rebuilt]}")
    print()
    print("rebuilt frame detail (luma, 10 bit code values 0..1023)")
    print("  frame   maxdiff  meandiff   min   max   low-2-bit spread")
    for i, mx, mn, lo, hi, low in rebuilt:
        spread = " ".join(f"{v:.3f}" for v in low)
        print(f"  {i:5d}   {mx:7d}  {mn:8.2f}  {lo:4d}  {hi:4d}   {spread}")
    if rebuilt:
        flat = np.mean([r[5] for r in rebuilt], axis=0)
        print()
        print("  a genuine 10 bit frame spreads the bottom two bits near 0.25 each;")
        print("  an 8 bit frame shifted up reads 1.000 0.000 0.000 0.000")
        print("  measured mean over rebuilt frames: "
              + " ".join(f"{v:.3f}" for v in flat))


if __name__ == "__main__":
    main()
