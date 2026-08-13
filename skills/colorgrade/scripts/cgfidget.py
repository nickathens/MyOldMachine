"""Is a clip FIDGETY, as opposed to stuck or teleporting?

pace, lurch and track style instruments all ask "how far did the picture
travel". A viewer calling a shot fidgety is complaining about something else:
the move not holding a line. Two separate things read that way and neither
shows up in a magnitude series.

  1. CROSS TRACK wobble. Decompose each gap's displacement into the component
     along the shot's own dominant direction and the component across it. On a
     clean pan the cross track term is a slow drift; a fidget is a sign flipping
     term of a pixel or two, frame to frame.
  2. JERK. Second difference of the along track speed. A shot can accelerate
     hard and still feel smooth if the acceleration itself is smooth.

Both are reported as a share of the clip's own median step, and the numbers to
compare against come from a REAL CAMERA clip measured the same way, because the
honest question is never "is it zero" but "is it more than a camera". On the
material this was built against, a real camera reads about 2.8 percent jerk per
step and 1.2 percent cross track; a rebuilt window that had drifted to 12.8
percent was what the viewer had correctly called fidgety, against 2.4 percent
in the untouched frames either side. Nothing was stuck and nothing teleported,
so every distance instrument read clean; this is the instrument that saw it.

    python cgfidget.py CLIP [CLIP ...]

Pass the guide or another real camera clip alongside the deliverable and read
the side by side table. Run it on the DELIVERED file, not an intermediate.
"""
import hashlib
import subprocess
import sys
import tempfile

import cv2
import numpy as np

SC = 3
LK = dict(winSize=(31, 31), maxLevel=4,
          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01))


def dims(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0",
                        path], capture_output=True, text=True).stdout
    line = [ln for ln in p.splitlines() if ln.strip()][0]
    return [int(x) for x in line.split(",")[:2]]


def displace(path):
    W, H = dims(path)
    w, h = W // SC // 2 * 2, H // SC // 2 * 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={w}:{h}",
                          "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
    dx, dy = [], []
    for i in range(1, len(fr)):
        a, b = fr[i - 1], fr[i]
        p0 = cv2.goodFeaturesToTrack(a, maxCorners=1200, qualityLevel=0.01,
                                     minDistance=8, blockSize=7)
        p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, **LK)
        p0b, st2, _ = cv2.calcOpticalFlowPyrLK(b, a, p1, None, **LK)
        good = (st.ravel() == 1) & (st2.ravel() == 1)
        good &= np.linalg.norm((p0 - p0b).reshape(-1, 2), axis=1) < 1.0
        d = (p1 - p0).reshape(-1, 2)[good]
        dx.append(float(np.median(d[:, 0])) * SC)
        dy.append(float(np.median(d[:, 1])) * SC)
    return np.array(dx), np.array(dy)


def report(path, dx, dy):
    n = len(dx)
    v = np.stack([dx, dy], 1)
    # dominant direction of the whole move, weighted by how far each gap went
    u = v.sum(0)
    u = u / np.linalg.norm(u)
    perp = np.array([-u[1], u[0]])
    along = v @ u
    cross = v @ perp
    # cross track: what matters is the frame to frame CHANGE, not the drift
    cross_d = np.diff(cross)
    # sign flips: a drift keeps its sign, a fidget alternates
    s = np.sign(cross_d[np.abs(cross_d) > 0.3])
    flips = int((np.diff(s) != 0).sum()) if len(s) > 2 else 0
    flip_rate = flips / max(len(s) - 1, 1)
    jerk = np.diff(along, 2)
    speed = np.abs(along)
    print(f"\n{path.split('/')[-1]}    {n} gaps, native pixels")
    print(f"  dominant direction  ({u[0]:+.2f},{u[1]:+.2f})     "
          f"speed along it: median {np.median(speed):.2f}  max {speed.max():.2f}")
    print("  CROSS TRACK, frame to frame change")
    print(f"    rms {cross_d.std():.3f} px   p95 {np.percentile(np.abs(cross_d),95):.3f} px"
          f"   worst {np.abs(cross_d).max():.3f} px")
    print(f"    as a share of the median step: {100*cross_d.std()/max(np.median(speed),1e-6):.1f} percent")
    print(f"    direction flips {flips}/{max(len(s)-1,0)} = {100*flip_rate:.0f} percent of moves")
    print("  JERK along the move (second difference of speed)")
    print(f"    rms {jerk.std():.3f} px   p95 {np.percentile(np.abs(jerk),95):.3f}"
          f"   worst {np.abs(jerk).max():.3f} at gap {int(np.abs(jerk).argmax())+1}")
    print(f"    as a share of the median step: {100*jerk.std()/max(np.median(speed),1e-6):.1f} percent")
    return dict(cross_rms=cross_d.std(), jerk_rms=jerk.std(),
                med=np.median(speed), along=along, cross=cross)


if __name__ == "__main__":
    out = {}
    tmp = tempfile.gettempdir()
    for p in sys.argv[1:]:
        dx, dy = displace(p)
        out[p] = report(p, dx, dy)
        tag = hashlib.md5(p.encode()).hexdigest()[:10]
        np.savez(f"{tmp}/fidget_{tag}.npz", dx=dx, dy=dy)
        print(f"    saved {tmp}/fidget_{tag}.npz")
    if len(out) > 1:
        print("\n  side by side, normalised to each clip's own median step:")
        print(f"    {'clip':52s} {'cross/step':>11s} {'jerk/step':>10s}")
        for p, r in out.items():
            print(f"    {p.split('/')[-1][:52]:52s} "
                  f"{100*r['cross_rms']/r['med']:10.1f}% {100*r['jerk_rms']/r['med']:9.1f}%")
