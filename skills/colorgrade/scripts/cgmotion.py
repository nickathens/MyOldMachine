"""Retime by measured travel, not by counting slots.

The cadence retime in `cgframes.py plan` can only decide that a pair of real
pictures spans one slot or two (`survivor_positions` states gaps as 1.0 or
2.0). That is enough when a generator holds a frame and then carries on. It is
not enough on material that also TELEPORTS: measured against its own pace, some
generated clips now and then advance three or four frames of ground in a single
step, and a pair that needs four slots can only ever be given two. The leftover
is seen as the picture jumping further every second or so. Counting duplicate
frames can never find this fault; measuring travel does.

So place output frames where the ground says they belong. Measure the real
distance between consecutive surviving pictures, accumulate it, smooth that
curve over a window wider than the artefact's own period, and sample it evenly.
Long arcs in the shot's speed survive the smoothing; a once a second spike does
not.

    python cgmotion.py WORK [WINDOW] [--report]

Run it after `cgframes.py census --work WORK`; it writes the same jobs.json
shape that `build`, `gate` and `render` already consume, so it replaces the
planner step only. WINDOW must be wider than both artefact periods; sweep it
with --report first and read the printed speed profile: the shot's own
acceleration arc must survive. If the profile comes out flat, the window is too
wide and the shot has been ironed. 73 frames, about 3 seconds at 24, was right
on the material this was tuned on; 25 and 49 left spikes in.

KNOWN LIMIT, stated so nobody re-earns it: this planner rebuilds nearly every
frame, and the frame builder does not land a requested fraction t exactly t of
the way across (measured 0.43 px rms on a 12.5 px step). Each placement error
is invisible as a still and audible as pace, because the step a viewer sees is
the DIFFERENCE of two errors. Clips repaired with this planner measured 4 to 5
times a real camera's jerk floor on `cgfidget.py`, against wrecked sources 40
to 60 times that floor. `cglocal.py` carries the closed loop that corrects the
placement (build, track where each frame landed, push t by the shortfall,
build again) and reached the camera floor on a 14 frame window; that loop has
not been ported into this planner yet. Measure the result with `cgfidget.py`
against a real camera clip and say the number out loud in the delivery.
"""
import json
import os
import subprocess
import sys

import cv2
import numpy as np

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def gray_frames(path, w=480):
    p = subprocess.run([FFMPEG, "-v", "error", "-i", path,
                        "-vf", f"scale={w}:-2", "-pix_fmt", "gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                           capture_output=True, text=True).stdout.strip().split(",")
    W, H = int(probe[0]), int(probe[1])
    h = int(round(H * w / W)) // 2 * 2
    n = w * h
    cnt = len(p.stdout) // n
    return [np.frombuffer(p.stdout[i * n:(i + 1) * n], np.uint8).reshape(h, w) for i in range(cnt)]


def travel(a, b):
    f = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 4, 21, 3, 7, 1.5, 0)
    return float(np.percentile(np.linalg.norm(f, axis=2), 75))


def plan(work, window=73, report_only=False):
    st = json.load(open(f"{work}/state.json"))
    census = json.load(open(f"{work}/census.json"))
    frozen = set()
    for c in census.values():
        frozen |= set(c["repeats"])
    n_out = int(st["nb_frames"])
    survivors = [f for f in range(n_out) if f not in frozen]
    if os.path.exists(f"{work}/travel.npz"):
        v = np.load(f"{work}/travel.npz")["v"]
    else:
        fr = gray_frames(st["input"])
        if len(fr) < n_out:
            sys.exit(f"decoded {len(fr)} of {n_out} frames")
        v = np.array([travel(fr[survivors[j]], fr[survivors[j + 1]])
                      for j in range(len(survivors) - 1)])
        np.savez(f"{work}/travel.npz", v=v, survivors=np.array(survivors))
    Cs = np.concatenate([[0.0], np.cumsum(v)])                 # travel at each survivor

    # travel as the ORIGINAL timeline shows it: a held frame advances nothing
    Corig = np.zeros(n_out)
    j = 0
    for i in range(n_out):
        while j + 1 < len(survivors) and survivors[j + 1] <= i:
            j += 1
        Corig[i] = Cs[j]

    k = window // 2
    pad = np.pad(Corig, k, mode="edge")
    D = np.array([pad[i:i + window].mean() for i in range(n_out)])
    D = np.maximum.accumulate(D)
    D = (D - D[0]) / (D[-1] - D[0]) * Cs[-1]                   # keep both ends pinned

    step_before = np.diff(Corig)
    step_after = np.diff(D)
    pace = np.median(v)
    print(f"\n{work}: {len(survivors)} real pictures over {n_out} slots, "
          f"{Cs[-1]/pace:.0f} frames of ground")
    print(f"  before: step per slot  min {step_before.min()/pace:.2f}  "
          f"med {np.median(step_before)/pace:.2f}  max {step_before.max()/pace:.2f}"
          f"   slots over 1.6: {int((step_before/pace>1.6).sum())}")
    print(f"  after : step per slot  min {step_after.min()/pace:.2f}  "
          f"med {np.median(step_after)/pace:.2f}  max {step_after.max()/pace:.2f}"
          f"   slots over 1.6: {int((step_after/pace>1.6).sum())}")
    sm = step_after / max(np.median(step_after), 1e-9)
    print(f"  after : slowest passage {sm.min():.2f}x of its own average, "
          f"fastest {sm.max():.2f}x  (the shot's real arc, kept on purpose)")

    fpos = np.interp(D, Cs, np.arange(len(survivors)))
    jobs, copies, spans = [], 0, []
    for slot in range(n_out):
        i = int(np.clip(np.floor(fpos[slot]), 0, len(survivors) - 2))
        t = float(fpos[slot] - i)
        if t < 1e-3 or t > 1 - 1e-3:
            src = survivors[i] if t < 1e-3 else survivors[i + 1]
            if src != slot:
                jobs.append([slot, int(src), int(src), 0.0, "copy"])
                copies += 1
            continue
        jobs.append([slot, int(survivors[i]), int(survivors[i + 1]), t, "rebuild"])
        spans.append(survivors[i + 1] - survivors[i])
    reb = sum(1 for j in jobs if j[4] == "rebuild")
    print(f"  plan: {reb} frames to build, {copies} real frames moved, "
          f"{n_out - len(jobs)} untouched")
    if spans:
        u, c = np.unique(spans, return_counts=True)
        print("  how far apart the two real frames are: " +
              "  ".join(f"{int(a)} apart x{int(b)}" for a, b in zip(u, c)))
    if not report_only:
        json.dump(jobs, open(f"{work}/jobs.json", "w"))
        print(f"  wrote {work}/jobs.json")
    return step_after / pace


if __name__ == "__main__":
    w = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 73
    plan(sys.argv[1], window=w, report_only="--report" in sys.argv)
