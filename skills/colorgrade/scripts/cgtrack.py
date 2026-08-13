"""Dominant camera translation per gap, by tracked features.

Phase correlation and Farneback can both lie about a single gap. Phase
correlation picks a wrong global peak on skies and smoke: on the clip this was
built against it invented a direction reversal and a two frame 30 px jolt that
tracked features prove never happened. Farneback's percentile summary is robust
but it is not a displacement, and its floor sits above the head of a shot that
opens at 0.04 px a frame. So settle it with the method that cannot pick a
spurious global peak: detect corners, track them with Lucas Kanade, keep only
the ones that survive the forward backward check, and take the median
displacement. Median over hundreds of real points is the camera's own move, in
native pixels, with a direction, immune to the moving subject and to smoke.

    python cgtrack.py CLIP WIDTH HEIGHT [OUT.npz]

The track is written to OUT.npz, default `CLIP.track.npz` next to the clip, so
the file name always says which file it was measured from. That default is a
rule, learned the expensive way: an earlier version hardcoded its output path,
running it on a repaired clip silently overwrote the track of the SOURCE, and a
replan built from that file was a plan built from the repair's own output. A
plan must always be built from a track OF THE SOURCE.

One caution when reading the result: on some material the median of corners
flips between feature clusters and reports a phantom near freeze, gaps reading
2 to 8 px against neighbours of 20 to 28. Before reporting any single gap
event, confirm it with the mean absolute pixel difference at that gap: if the
difference sits near the clip's median, nothing is frozen and the tracker
changed cluster.
"""
import subprocess
import sys

import cv2
import numpy as np

path = sys.argv[1]
W, H = int(sys.argv[2]), int(sys.argv[3])
SC = 3
w, h = W // SC // 2 * 2, H // SC // 2 * 2
raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                      f"scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                     capture_output=True).stdout
fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
n = len(fr)

lk = dict(winSize=(31, 31), maxLevel=4,
          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01))
dx, dy, cnt = [], [], []
for i in range(1, n):
    a, b = fr[i - 1], fr[i]
    p0 = cv2.goodFeaturesToTrack(a, maxCorners=1200, qualityLevel=0.01,
                                 minDistance=8, blockSize=7)
    if p0 is None or len(p0) < 12:
        dx.append(np.nan); dy.append(np.nan); cnt.append(0); continue
    p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, **lk)
    p0b, st2, _ = cv2.calcOpticalFlowPyrLK(b, a, p1, None, **lk)
    good = (st.ravel() == 1) & (st2.ravel() == 1)
    fb = np.linalg.norm((p0 - p0b).reshape(-1, 2), axis=1)
    good &= fb < 1.0
    if good.sum() < 12:
        dx.append(np.nan); dy.append(np.nan); cnt.append(int(good.sum())); continue
    d = (p1 - p0).reshape(-1, 2)[good]
    dx.append(float(np.median(d[:, 0])) * SC)
    dy.append(float(np.median(d[:, 1])) * SC)
    cnt.append(int(good.sum()))

dx, dy = np.array(dx), np.array(dy)
mag = np.hypot(dx, dy)
k = 15
pad = np.pad(mag, k, mode="edge")
loc = np.array([np.median(np.delete(pad[i:i + 2 * k + 1], k)) for i in range(len(mag))])
r = mag / np.maximum(loc, 1e-6)

print(f"gaps {len(mag)}   camera translation in NATIVE pixels, median of tracked points")
print(f"  min {np.nanmin(mag):.2f}  median {np.nanmedian(mag):.2f}  max {np.nanmax(mag):.2f}")
print(f"  tracked points per gap: min {min(cnt)}  median {int(np.median(cnt))}")
bad = np.where((r > 1.4) | (r < 0.65))[0]
print(f"  gaps off local pace by more than ~40 percent: {len(bad)}")
if len(bad):
    print("    " + "  ".join(f"{int(i)}:{r[i]:.2f}" for i in bad))
print("\n  per gap: index  magnitude (dx,dy)   6 per line")
for i in range(0, len(mag), 6):
    print("   " + "  ".join(f"{j:3d} {mag[j]:6.2f} ({dx[j]:+6.2f},{dy[j]:+6.2f})"
                            for j in range(i, min(i + 6, len(mag)))))
out = sys.argv[4] if len(sys.argv) > 4 else f"{path}.track.npz"
np.savez(out, dx=dx, dy=dy, mag=mag)
print(f"\nwrote {out}")
