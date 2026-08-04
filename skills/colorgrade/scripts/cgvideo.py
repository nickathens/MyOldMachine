"""Video in and out: probing, shot detection, frame sampling, rendering.

Everything here shells out to ffmpeg/ffprobe. Nothing here knows about colour.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

import numpy as np

FFMPEG = os.environ.get("CG_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("CG_FFPROBE", "ffprobe")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


# ---------------------------------------------------------------- probe


@dataclass
class Media:
    path: str
    width: int
    height: int
    fps: float
    nb_frames: int
    duration: float
    pix_fmt: str
    color_space: str
    color_transfer: str
    codec: str
    has_audio: bool

    @property
    def is_log_flagged(self):
        return self.color_transfer not in ("bt709", "", "unknown", "iec61966-2-1", "bt470bg", "smpte170m")


def probe(path) -> Media:
    out = run([
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if s.get("codec_type") == "video")
    has_audio = any(s.get("codec_type") == "audio" for s in d["streams"])
    num, den = (v.get("r_frame_rate") or "25/1").split("/")
    fps = float(num) / float(den or 1)
    dur = float(d["format"].get("duration") or v.get("duration") or 0.0)
    nb = int(v.get("nb_frames") or 0) or int(round(dur * fps))
    return Media(
        path=path,
        width=int(v["width"]), height=int(v["height"]), fps=fps,
        nb_frames=nb, duration=dur,
        pix_fmt=v.get("pix_fmt", ""),
        color_space=v.get("color_space", ""),
        color_transfer=v.get("color_transfer", ""),
        codec=v.get("codec_name", ""),
        has_audio=has_audio,
    )


# ---------------------------------------------------------------- shots


@dataclass
class Shot:
    index: int
    start_frame: int
    end_frame: int      # exclusive
    start_t: float
    end_t: float

    @property
    def n_frames(self):
        return self.end_frame - self.start_frame

    @property
    def duration(self):
        return self.end_t - self.start_t


def shots_from_cuts(media: Media, cuts) -> list[Shot]:
    """Build a shot list from explicit cut frames.

    Wanted whenever detection is not trustworthy: an EDL exists, the cuts are
    known, or the change between shots is colour only. A pure colour change
    with identical framing is invisible to content based detection, which
    compares picture content and correctly sees none.
    """
    edges = sorted({0, *(int(c) for c in cuts if 0 < int(c) < media.nb_frames),
                    media.nb_frames})
    out = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        out.append(Shot(i, a, b, a / media.fps, b / media.fps))
    return out or [Shot(0, 0, media.nb_frames, 0.0, media.duration)]


def detect_shots(media: Media, threshold=27.0, min_len_frames=12, verbose=True) -> list[Shot]:
    """PySceneDetect ContentDetector. Falls back to one shot if it is absent."""
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
    except ImportError:
        if verbose:
            print("scenedetect not installed, treating the file as one shot", file=sys.stderr)
        return [Shot(0, 0, media.nb_frames, 0.0, media.duration)]

    video = open_video(media.path)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_len_frames))
    sm.detect_scenes(video, show_progress=False)
    scenes = sm.get_scene_list()
    if not scenes:
        return [Shot(0, 0, media.nb_frames, 0.0, media.duration)]
    shots = []
    for i, (a, b) in enumerate(scenes):
        shots.append(Shot(i, a.get_frames(), b.get_frames(),
                          a.get_seconds(), b.get_seconds()))
    return shots


# ---------------------------------------------------------------- sampling


def sample_frames(media: Media, width=320, every=1, max_frames=None):
    """One decode pass. Yields (frame_index, HxWx3 float32 0..1) for every
    `every`-th frame, downscaled to `width`. Memory friendly: it streams.
    """
    h = int(round(media.height * width / media.width))
    h += h % 2
    vf = f"scale={width}:{h}:flags=bilinear"
    if every > 1:
        vf = f"select=not(mod(n\\,{every})),{vf}"
    cmd = [FFMPEG, "-v", "error", "-i", media.path, "-vf", vf,
           "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    frame_bytes = width * h * 3
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         bufsize=frame_bytes * 4)
    n = 0
    try:
        while True:
            buf = p.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, width, 3)
            yield n * every, arr.astype(np.float32) / 255.0
            n += 1
            if max_frames and n >= max_frames:
                break
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.terminate()
        p.wait(timeout=5)


def collect_shot_samples(media: Media, shots: list[Shot], per_shot=8, width=320):
    """Return {shot_index: [frames]} using a single decode pass.

    Sampling stride is chosen so each shot gets roughly `per_shot` frames,
    and short shots still get at least one.
    """
    wanted = {}
    for s in shots:
        n = max(1, min(per_shot, s.n_frames))
        # evenly spaced inside the shot, avoiding the very first and last frame
        # because dissolves and flash frames live there
        lo = s.start_frame + max(1, s.n_frames // 10)
        hi = s.end_frame - max(1, s.n_frames // 10)
        if hi <= lo:
            lo, hi = s.start_frame, max(s.start_frame + 1, s.end_frame)
        idx = np.linspace(lo, hi - 1, n).round().astype(int)
        for i in idx:
            wanted.setdefault(int(i), []).append(s.index)

    out = {s.index: [] for s in shots}
    if not wanted:
        return out
    targets = sorted(wanted)
    tset = set(targets)
    for fi, frame in sample_frames(media, width=width, every=1):
        if fi in tset:
            for si in wanted[fi]:
                out[si].append(frame.copy())
            tset.discard(fi)
            if not tset:
                break
    # any shot that came up empty (seek/count drift) gets its middle frame
    for s in shots:
        if not out[s.index]:
            out[s.index] = [grab_frame(media, (s.start_t + s.end_t) / 2.0, width=width)]
    return out


def grab_frame(media: Media, t: float, width=None):
    """Single frame at time t, full resolution unless `width` is given."""
    vf = []
    if width:
        h = int(round(media.height * width / media.width))
        h += h % 2
        vf = ["-vf", f"scale={width}:{h}"]
    cmd = [FFMPEG, "-v", "error", "-ss", f"{max(t, 0):.4f}", "-i", media.path,
           "-frames:v", "1", *vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout
    w = width or media.width
    h = int(round(media.height * w / media.width))
    h += h % 2
    if len(out) < w * h * 3:
        return np.zeros((h, w, 3), dtype=np.float32)
    return np.frombuffer(out[: w * h * 3], dtype=np.uint8).reshape(h, w, 3).astype(np.float32) / 255.0


def write_png(path, img01):
    from PIL import Image
    a = np.clip(np.asarray(img01) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    Image.fromarray(a).save(path)


# ---------------------------------------------------------------- render


def render(media: Media, shots, lut_paths: dict, out_path, crf=16, preset="medium",
           codec="libx264", extra_vf=None, progress=None):
    """Apply one LUT per shot in a single decode pass, then mux the audio back.

    Builds trim/concat branches rather than cutting to temp files, so there is
    exactly one decode and exactly one encode, and cut points stay frame exact.
    """
    parts = []
    branches = []
    n = 0
    for s in shots:
        lut = lut_paths.get(s.index)
        chain = [f"trim=start_frame={s.start_frame}:end_frame={s.end_frame}", "setpts=PTS-STARTPTS"]
        if lut:
            chain.append(f"lut3d=file={_esc(lut)}:interp=tetrahedral")
        if extra_vf:
            chain.append(extra_vf)
        branches.append(f"[0:v]{','.join(chain)}[v{n}]")
        parts.append(f"[v{n}]")
        n += 1
    if n == 0:
        raise ValueError("no shots to render")
    graph = ";".join(branches) + ";" + "".join(parts) + f"concat=n={n}:v=1:a=0[vout]"

    cmd = [FFMPEG, "-y", "-v", "error", "-stats", "-i", media.path,
           "-filter_complex", graph, "-map", "[vout]"]
    if media.has_audio:
        cmd += ["-map", "0:a", "-c:a", "copy"]
    cmd += ["-c:v", codec, "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed:\n{proc.stderr[-4000:]}")
    return out_path


def _esc(p):
    """Escape a path for use inside an ffmpeg filter argument."""
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def render_still(src_png, lut_path, out_png):
    cmd = [FFMPEG, "-y", "-v", "error", "-i", src_png,
           "-vf", f"lut3d=file={_esc(lut_path)}:interp=tetrahedral", out_png]
    run(cmd)
    return out_png
