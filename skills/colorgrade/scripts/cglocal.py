"""Surgical motion proportional retime, in 10 bit, for a clip with ONE bad gap.

`cgmotion.py` rebuilds every frame, which is the right trade when a clip is
faulty on a beat. It is the wrong trade when a clip is clean apart from one
event: the clip this was built on had 119 of 120 gaps sitting on a smooth
acceleration and exactly one travelling 25.7 px where its neighbours travelled
12.5.

So move only the frames that have to move. Pin the window's two end frames, put
the spike's excess back into the neighbouring gaps, and reslot the interior by
distance. Everything outside the window is passed through untouched and stays
bit exact; only the interior is rebuilt, and it is rebuilt in 10 bit rather than
through the tool's 8 bit plane path, so nothing anywhere is requantised.

CLOSED LOOP. Asking RIFE for a frame at fraction t does not put the picture
exactly t of the way across: measured on that clip, the placement lands within
0.42 px rms of where the plan asked, worst 0.88. That sounds negligible against
a 12.5 px step, and as a still it is. It is not negligible as motion, because
the gap a viewer sees between two rebuilt frames is the DIFFERENCE of two of
those errors, and they are uncorrelated, so a 3 percent placement error becomes
a 6 percent wobble in pace. Against a real camera's 2.8 percent floor that reads
as fidget: the shot stops sitting on its own acceleration for the half second
the window covers.

The error is measurable, so correct it. Build the window, track where each frame
actually landed, push t by the shortfall, build again. Two rounds are enough;
the response is near enough linear that the first correction takes most of it.
Two rounds here went 0.427 px rms to 0.180, and the window's jerk on
`cgfidget.py` went from 12.8 percent of a step to 2.8, the real camera's own
figure. Two details that cost time if rediscovered: source and built frames
must be reduced for tracking by the SAME code path (`small()` below), because
an ffmpeg downscale and a cv2 downscale disagree by more than the tenth of a
pixel the loop is chasing; and slots at the very ends of the window, where t is
under about 0.1, refuse to move off the original frame and keep a ~0.65 px
error however hard they are pushed. Not worth chasing: the correction they were
asked for was under a pixel.

    python cglocal.py CLIP.mp4 WORKDIR LO HI [ITERS] [TRACK.npz]
    ffmpeg -f rawvideo -pix_fmt yuv420p10le -s WxH -r FPS -i WORKDIR/fixed10.yuv \
        -c:v libx264 -qp 0 -preset slow -pix_fmt yuv420p10le OUT.mp4

LO and HI are frame indices; both stay original and everything between them is
reslotted. Needs a track for the clip, written by `cgtrack.py` (default
`CLIP.mp4.track.npz`), and it must be a track OF THE SOURCE, not of an earlier
repair of it: a plan built from a track of a repair is a plan built from the
repair's own errors. Verify the result with `cgverify10.py` (bit exact frames
counted, genuine 10 bit proven off the pictures, not the tag) and with
`cgfidget.py` against a real camera clip.
"""
import os
import subprocess
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CG_RIFE_SCALE", "1.0")

if len(sys.argv) < 5:
    sys.exit("usage: cglocal.py CLIP WORKDIR LO HI [ITERS] [TRACK.npz]")

SRC = sys.argv[1]
WORK = sys.argv[2]
_p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-count_frames", "-show_entries",
                     "stream=width,height,nb_read_frames", "-of", "csv=p=0", SRC],
                    capture_output=True, text=True).stdout.strip().split(",")
W, H, N = int(_p[0]), int(_p[1]), int(_p[2])
CW, CH = W // 2, H // 2
YSZ, CSZ = W * H, CW * CH
FRAME_BYTES = (YSZ + 2 * CSZ) * 2          # 10 bit, two bytes a sample
RAW = f"{WORK}/src10.yuv"

SC = 3                                     # tracking scale, native px restored after
LK = dict(winSize=(31, 31), maxLevel=4,
          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01))


def decode():
    os.makedirs(WORK, exist_ok=True)
    if os.path.exists(RAW) and os.path.getsize(RAW) == N * FRAME_BYTES:
        print(f"raw 10 bit source already present, {os.path.getsize(RAW)/1e9:.2f} GB")
        return
    print("decoding source to raw 10 bit planes")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SRC,
                    "-pix_fmt", "yuv420p10le", "-f", "rawvideo", RAW], check=True)
    got = os.path.getsize(RAW)
    if got != N * FRAME_BYTES:
        sys.exit(f"expected {N*FRAME_BYTES} bytes, got {got}")


def read(fh, i):
    fh.seek(i * FRAME_BYTES)
    b = np.frombuffer(fh.read(FRAME_BYTES), np.uint16)
    return (b[:YSZ].reshape(H, W),
            b[YSZ:YSZ + CSZ].reshape(CH, CW),
            b[YSZ + CSZ:].reshape(CH, CW))


def small(y):
    """Y plane, 10 bit, down to the 8 bit reduced gray the tracker works on.

    Source and built frames must go through THIS, not through ffmpeg for one and
    this for the other: a bicubic downscale and an area downscale disagree by
    enough to swamp the tenth of a pixel the loop is chasing.
    """
    return cv2.resize((y >> 2).astype(np.uint8), (W // SC, H // SC),
                      interpolation=cv2.INTER_AREA)


def shift(a, b):
    """Median displacement of forward backward checked corners, native pixels."""
    p0 = cv2.goodFeaturesToTrack(a, maxCorners=1200, qualityLevel=0.01,
                                 minDistance=8, blockSize=7)
    p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, **LK)
    p0b, st2, _ = cv2.calcOpticalFlowPyrLK(b, a, p1, None, **LK)
    good = (st.ravel() == 1) & (st2.ravel() == 1)
    good &= np.linalg.norm((p0 - p0b).reshape(-1, 2), axis=1) < 1.0
    d = (p1 - p0).reshape(-1, 2)[good]
    return np.array([np.median(d[:, 0]), np.median(d[:, 1])]) * SC


def plan(v, window_lo, window_hi):
    """window_lo/hi are FRAME indices; both are pinned and stay original."""
    C = np.concatenate([[0.0], np.cumsum(v)])           # ground at each frame
    g = np.arange(window_lo, window_hi)                 # gaps inside the window
    vt = v[g].copy()
    spike = int(g[np.argmax(v[g] / np.median(v[g]))])
    vt[spike - window_lo] = float(np.median(v[spike - 2:spike + 3]))
    vt *= v[g].sum() / vt.sum()                         # both ends stay pinned
    T = C.copy()
    T[window_lo + 1:window_hi + 1] = C[window_lo] + np.cumsum(vt)
    pos = np.interp(T, C, np.arange(N))
    print(f"  worst gap is {spike} -> {spike+1}: {v[spike]:.2f} px against a "
          f"local {np.median(v[spike-2:spike+3]):.2f}")
    print(f"  window frames {window_lo}..{window_hi}, "
          f"pace inside now {vt.min():.2f} to {vt.max():.2f} px")
    return pos


def warp10(cgrife, torch, pa, pb, t):
    """RIFE's flow and fusion, but the pixels warped at 10 bit precision."""
    dev = cgrife.device()

    def rgb(p):
        y = p[0].astype(np.float32)
        u = np.repeat(np.repeat(p[1], 2, 0), 2, 1)[:H, :W].astype(np.float32)
        vv = np.repeat(np.repeat(p[2], 2, 0), 2, 1)[:H, :W].astype(np.float32)
        yl = (y - 64.0) / 876.0
        cb, cr = (u - 512.0) / 896.0, (vv - 512.0) / 896.0
        s = np.clip(np.stack([yl + 1.5748 * cr,
                              yl - 0.1873 * cb - 0.4681 * cr,
                              yl + 1.8556 * cb], -1), 0, 1)
        return torch.from_numpy(s).permute(2, 0, 1)[None].to(dev)

    flow, mask = cgrife.flow_mask(rgb(pa), rgb(pb), t)
    F = torch.nn.functional
    res = []
    for i in range(3):
        ta = torch.from_numpy(pa[i].astype(np.float32))[None, None].to(dev)
        tb = torch.from_numpy(pb[i].astype(np.float32))[None, None].to(dev)
        g0 = cgrife._warp(ta, flow[:, :2], W, H)
        g1 = cgrife._warp(tb, flow[:, 2:4], W, H)
        m = mask if i == 0 else F.interpolate(mask, g0.shape[-2:], mode="bilinear",
                                              align_corners=False)
        r = (g0 * m + g1 * (1 - m))[0, 0].clamp(0, 1023).cpu().numpy()
        res.append(np.round(r).astype(np.uint16))
    return res


def solve(jobs, iters):
    """Build the window, measure where it landed, correct t, build again."""
    import torch
    import cgrife

    fh = open(RAW, "rb")
    src = {}
    for _, i, _ in jobs:
        for k in (i, i + 1):
            if k not in src:
                src[k] = read(fh, k)
    ref = {k: small(p[0]) for k, p in src.items()}
    full = {i: shift(ref[i], ref[i + 1]) for _, i, _ in jobs}

    ask = {s: t for s, _, t in jobs}                    # what we keep asking for
    use = dict(ask)                                     # what we actually request
    out = {}
    for it in range(iters + 1):
        print(f"\n  round {it}: building {len(jobs)} frames")
        for s, i, _ in jobs:
            out[s] = warp10(cgrife, torch, src[i], src[i + 1], use[s])
        if it == iters:
            break
        err = []
        for s, i, _ in jobs:
            f = full[i]
            n = max(np.linalg.norm(f), 1e-9)
            got = float(shift(ref[i], small(out[s][0])) @ (f / n)) / n
            e = ask[s] - got
            err.append(e * n)
            use[s] = float(np.clip(use[s] + e, 0.002, 0.998))
        err = np.array(err)
        print(f"  round {it} placement error: rms {err.std():.3f} px  "
              f"worst {np.abs(err).max():.3f} px")
    fh.close()
    return out


def measure_final(out, jobs):
    fh = open(RAW, "rb")
    src = {}
    for _, i, _ in jobs:
        for k in (i, i + 1):
            if k not in src:
                src[k] = small(read(fh, k)[0])
    fh.close()
    err = []
    print(f"\n  slot   from   asked   landed     error px")
    for s, i, t in jobs:
        f = shift(src[i], src[i + 1])
        n = max(np.linalg.norm(f), 1e-9)
        got = float(shift(src[i], small(out[s][0])) @ (f / n)) / n
        err.append((t - got) * n)
        print(f"  {s:4d}  {i:4d}   {t:.3f}   {got:.3f}   {err[-1]:+8.3f}")
    err = np.array(err)
    print(f"\n  final placement error: rms {err.std():.3f} px  "
          f"worst {np.abs(err).max():.3f} px")


def write(out, path):
    fh = open(RAW, "rb")
    with open(path, "wb") as o:
        for f in range(N):
            if f in out:
                y, u, v = out[f]
                o.write(y.tobytes()); o.write(u.tobytes()); o.write(v.tobytes())
            else:
                fh.seek(f * FRAME_BYTES)
                o.write(fh.read(FRAME_BYTES))
    fh.close()


if __name__ == "__main__":
    lo, hi = int(sys.argv[3]), int(sys.argv[4])
    iters = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    track = sys.argv[6] if len(sys.argv) > 6 else f"{SRC}.track.npz"
    decode()
    pos = plan(np.load(track)["mag"], lo, hi)
    SNAP = 0.02
    jobs = []
    for slot in range(lo + 1, hi):
        p = pos[slot]
        if abs(p - round(p)) < SNAP:
            print(f"  slot {slot}: lands on original frame {int(round(p))}, left alone")
            continue
        jobs.append((slot, int(np.floor(p)), float(p - np.floor(p))))
    print(f"\n{len(jobs)} frames to rebuild, {N - len(jobs)} passed through untouched")
    out = solve(jobs, iters)
    measure_final(out, jobs)
    write(out, f"{WORK}/fixed10.yuv")
    print(f"wrote {WORK}/fixed10.yuv")
