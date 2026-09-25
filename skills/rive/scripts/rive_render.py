#!/usr/bin/env python3
"""Render a Rive project or .riv file to a still, an image sequence or a video.

Two engines, chosen by what you hand it:

  cli  a project folder (rive.yaml + RML/Luau). One headless Rive CLI capture
       per frame, in parallel. The reference look: same Metal renderer as the
       previewer, runs unsigned scripts. Transparency is solved from a black
       and a white pass.
  web  a .riv file. Rive's web runtime stepped frame by frame in headless
       Chromium. Real single-pass alpha, data can change mid-shot, but
       unsigned scripts do not run and edges are anti-aliased differently.

Outputs by extension: .png (one frame, or a sequence with --frames/--duration
when the name holds %d), a folder (PNG sequence), .mp4 (H.264), .mov
(ProRes 422 HQ, 4444 with --alpha), .webm (VP9, alpha with --alpha), .gif.

Examples:
  rive_render.py myproject -o still.png --at 1.5
  rive_render.py myproject -o intro.mp4 --duration 6 --fps 30
  rive_render.py myproject -o super.mov --alpha --duration 5 --fps 25 --data name="Ada Lovelace"
  rive_render.py myproject -o ui.mov --size 3840x2160 --duration 8 --click 2.0@560,560
  rive_render.py hero.riv -o hero.webm --alpha --duration 4 --state-machine Main
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402
import rivetimeline as T  # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".gif"}
PRORES_PROFILES = {"proxy": 0, "lt": 1, "422": 2, "422hq": 3, "4444": 4, "4444xq": 5}
ACCURATE = "accurate_rnd+full_chroma_int"


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def parse_size(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text or "")
    if not m:
        raise L.RiveError(f"size {text!r} must be WIDTHxHEIGHT, e.g. 1920x1080")
    w, h = int(m.group(1)), int(m.group(2))
    if w < 2 or h < 2:
        raise L.RiveError("size must be at least 2x2")
    return w, h


def parse_timed(spec: str, what: str) -> tuple[float, str]:
    if "@" not in spec:
        raise L.RiveError(f"--{what} {spec!r} must be TIME@VALUE, e.g. 2.0@560,560")
    t, rest = spec.split("@", 1)
    try:
        return float(t), rest
    except ValueError as exc:
        raise L.RiveError(f"--{what} {spec!r}: {t!r} is not a time in seconds") from exc


def xy(text: str) -> list[float]:
    try:
        x, y = text.split(",", 1)
        return [float(x), float(y)]
    except ValueError as exc:
        raise L.RiveError(f"{text!r} must be X,Y in artboard coordinates") from exc


def events_from_args(args) -> list[dict]:
    events: list[dict] = []
    for spec in args.click or []:
        t, rest = parse_timed(spec, "click")
        events.append({"at": t, "click": xy(rest)})
    for spec in args.drag or []:
        t, rest = parse_timed(spec, "drag")
        steps = 8
        if ":" in rest:
            rest, steps_s = rest.rsplit(":", 1)
            steps = int(steps_s)
        if ">" not in rest:
            raise L.RiveError(f"--drag {spec!r} must be TIME@X1,Y1>X2,Y2[:STEPS]")
        a, b = rest.split(">", 1)
        events.append({"at": t, "drag": xy(a) + xy(b), "steps": steps})
    for spec in args.key or []:
        t, rest = parse_timed(spec, "key")
        events.append({"at": t, "key": rest})
    return events


def build_timeline(args) -> T.Timeline:
    if args.timeline:
        timeline = T.load_timeline_file(args.timeline)
    else:
        timeline = T.Timeline()
    extra_events = events_from_args(args)
    data = dict(timeline.data)
    for spec in args.data or []:
        if "=" not in spec:
            raise L.RiveError(f"--data {spec!r} must be PATH=VALUE")
        path, value = spec.split("=", 1)
        data[path.strip()] = value
    curves = dict(timeline.curves)
    for spec in args.data_curve or []:
        path, curve = T.load_curve(spec)
        curves[path] = curve
    steps = list(timeline.steps) + [s for i, e in enumerate(extra_events)
                                     for s in T.expand_event(e, 1000 + i)]
    steps.sort(key=lambda s: (s.at, s.source))
    return T.Timeline(steps=steps, data=data, curves=curves)


# --------------------------------------------------------------------------
# frames: the CLI engine
# --------------------------------------------------------------------------


class CliEngine:
    name = "cli"

    def __init__(self, project: Path, args, report: dict):
        self.args = args
        self.report = report
        self.binary = L.require_rive()
        self.version = L.cli_version(self.binary)
        report["engine"] = {"name": "cli", "rive_cli": self.version, "binary": self.binary}
        note = L.version_note(self.version)
        if note:
            report["warnings"].append(note)
        self.workdir = Path(tempfile.mkdtemp(prefix="rive_skill_render_", dir=args.work_dir))
        try:
            self._prepare(project, args, report)
        except BaseException:
            self.close()
            raise

    def _prepare(self, project: Path, args, report: dict) -> None:
        self.source = project.resolve()
        report["source"] = {"path": str(self.source), "kind": "project",
                            "sha256": L.hash_project(self.source)}
        base = L.snapshot_project(self.source, self.workdir)
        verify = L.run_rive([str(base), "--verify", "--format=json"], timeout=300)
        envelope = L.parse_envelope(verify.stdout) or {}
        if verify.returncode != 0:
            errors = envelope.get("errors") or [verify.stderr.strip()[-600:]]
            raise L.RiveError("the project does not build: " + json.dumps(errors)[:900],
                              "fix it first: rive <dir> --verify, then rive_check.py")
        inspect = L.inspect_project(base)
        self.board = L.pick_artboard(inspect, args.artboard)
        problems = [p for p in L.problems(inspect) if p.get("severity") == "error"]
        if problems:
            raise L.RiveError("rive inspect reports errors: " + json.dumps(problems)[:900])
        report["artboard"] = {"name": self.board.name, "size": list(self.board.size),
                              "state_machine": self.board.state_machine,
                              "view_model": self.board.view_model}
        if not self.board.state_machine:
            report["warnings"].append(
                "the artboard has no default state machine: data binds and listeners do nothing, "
                "only the first timeline plays (see references/rml.md, silent failures)")
        self.passes: dict[str, Path] = {}
        if args.alpha:
            if self.board.opaque_background():
                raise L.RiveError("--alpha, but the artboard has an opaque background fill "
                                  f"({', '.join(self.board.background)}), so nothing would be transparent",
                                  "remove the artboard's <Fill>, or render without --alpha")
            for label, argb in (("black", "FF000000"), ("white", "FFFFFFFF")):
                snap = L.snapshot_project(self.source, self.workdir)
                L.add_artboard_background(snap, self.board, argb)
                self.passes[label] = snap
        else:
            snap = base
            if not self.board.opaque_background() and not args.keep_backdrop:
                bg = args.background.upper()
                L.add_artboard_background(snap, self.board, "FF" + bg)
                report["background"] = "#" + bg
            self.passes["main"] = snap
        self.view_args = view_args(self.board, args, report)

    def capture(self, pass_name: str, t: float, timeline: T.Timeline, out_png: Path) -> T.FrameArgs:
        frame = timeline.frame_args(t)
        cmd = [str(self.passes[pass_name]), "--quiet", f"--screenshot={out_png}", *self.view_args]
        if self.args.artboard:
            cmd.append(f"--artboard={self.board.name}")
        cmd += frame.args
        result = L.run_rive(cmd, timeout=self.args.frame_timeout, binary=self.binary)
        if result.returncode != 0 or not out_png.is_file():
            msg = (result.stderr or result.stdout).strip()[-500:]
            raise L.RiveError(f"capture at {t:.4f}s failed ({L.explain_exit(result.returncode)}): {msg}")
        return frame

    def close(self):
        L.remove_tree(self.workdir)


def view_args(board: L.ArtboardInfo, args, report: dict) -> list[str]:
    """--viewport/--fit for the requested output size."""
    out: list[str] = []
    bw, bh = board.size
    if not args.size:
        report["size"] = [bw, bh]
        return out
    w, h = parse_size(args.size)
    report["size"] = [w, h]
    if (w, h) == (bw, bh) and not args.fit:
        return out
    fit = args.fit
    same_aspect = bw and bh and abs((w / h) - (bw / bh)) < 0.001
    if not fit:
        fit = "contain" if same_aspect else "layout"
        if not same_aspect:
            report["warnings"].append(
                f"{w}x{h} is not the artboard's aspect ({bw}x{bh}); fit=layout reflows a responsive "
                "artboard and pins a fixed one top-left. Pass --fit contain/cover to scale instead.")
    if args.alpha and fit in ("contain", "fit-width", "fit-height", "none", "scale-down") and not same_aspect:
        raise L.RiveError(f"--alpha with fit={fit} leaves an opaque letterbox (the CLI backdrop)",
                          "match the artboard's aspect, or use --fit fill/cover/layout")
    report["fit"] = fit
    out += [f"--viewport={w}x{h}", f"--fit={fit}"]
    return out


# --------------------------------------------------------------------------
# frames: the web engine
# --------------------------------------------------------------------------


class WebEngine:
    name = "web"

    def __init__(self, riv: Path, args, report: dict):
        import riveweb as W
        self.args = args
        self.report = report
        self.riv = riv.resolve()
        report["source"] = {"path": str(self.riv), "kind": "riv", "sha256": L.sha256_file(self.riv)}
        self.W = W
        self.session = W.WebSession(self.riv, flavour=args.web_runtime).__enter__()
        try:
            self._prepare(args, report)
        except BaseException:
            self.close()
            raise

    def _prepare(self, args, report: dict) -> None:
        W = self.W
        info = self.session.info()
        boards = {b["name"]: b for b in info["artboards"]}
        name = args.artboard or info.get("defaultArtboard") or (info["artboards"][0]["name"] if info["artboards"] else None)
        if name not in boards:
            raise L.RiveError(f"no artboard {name!r}; the file has {', '.join(boards) or 'none'}")
        board = boards[name]
        machines = [m["name"] for m in board["stateMachines"]]
        sm = args.state_machine
        if sm and sm not in machines:
            raise L.RiveError(f"no state machine {sm!r} on {name}; it has {', '.join(machines) or 'none'}")
        if not sm and len(machines) > 1:
            report["warnings"].append(
                f"{name} has {len(machines)} state machines and the web runtime cannot see which one is the "
                f"default; using the first ({machines[0]}). Pass --state-machine to choose.")
        bw, bh = int(round(board["width"])), int(round(board["height"]))
        w, h = parse_size(args.size) if args.size else (bw, bh)
        self.size = (w, h)
        report["size"] = [w, h]
        fit = args.fit or "contain"
        if fit == "layout":
            report["warnings"].append("the web engine has no layout fit; using contain")
            fit = "contain"
        report["fit"] = fit
        self.setup_args = dict(width=w, height=h, artboard=name, state_machine=sm, fit=fit)
        self.board_name = name
        report["engine"] = {"name": "web", "runtime": W.runtime_version(args.web_runtime),
                            "renderer": self.session.renderer_string}
        scripts = [a for a in info.get("assets", []) if a.get("ext") in ("luau", "wasm")]
        if scripts:
            report["warnings"].append("the file carries scripts; web runtimes reject unsigned ones")
        report["artboard"] = {"name": name, "size": [bw, bh], "state_machine": sm or (machines[0] if machines else None)}
        self.properties = {p["name"] for vm in info["viewModels"] for p in vm["properties"]}

    def render_sequence(self, times: list[float], timeline: T.Timeline, folder: Path, pad: int) -> list[dict]:
        schedule = T.web_schedule(timeline)
        data0 = dict(timeline.data)
        for path, curve in timeline.curves.items():
            data0[path] = T.format_value(curve.at(times[0] if times else 0.0))
        setup = self.session.setup(data=data0, **self.setup_args)
        self.report["engine"]["state_machine"] = setup.get("stateMachine")
        done = 0
        frames = []
        for k, t in enumerate(times):
            due = []
            while done < len(schedule) and schedule[done]["at"] + T.FRAME <= t + T.EPS:
                due.append(schedule[done])
                done += 1
            data = {p: T.format_value(c.at(t)) for p, c in timeline.curves.items()} or None
            png, meta = self.session.frame_png(t, due, data)
            path = folder / f"f{k:0{pad}d}.png"
            path.write_bytes(png)
            frames.append({"t": t, "events": meta.get("events", [])})
        return frames

    def close(self):
        try:
            self.session.__exit__(None, None, None)
        except Exception:
            pass


# --------------------------------------------------------------------------
# alpha: solve transparency from a black and a white pass
# --------------------------------------------------------------------------

# 255 - (white - black) is the coverage per channel; black / alpha is the
# straight colour. Verified on a glow, a radial gradient to clear, a 50%
# fill and text: recomposited over #1D1D1D it matches the single pass
# within 2 codes (mean 0.10). ffmpeg's unpremultiply=inplace=1 after
# alphamerge did NOT un-premultiply here (errors up to 65 codes), so the
# division is done with blend.
ALPHA_GRAPH = ("[0:v]format=gbrp,split[b1][b2];[1:v]format=gbrp[w];"
               "[w][b1]blend=all_expr='255-(A-B)',split[a3a][a3b];"
               "[b2][a3a]blend=all_expr='if(gt(B,0),min(255,(A*255+B/2)/B),0)'[c];"
               "[a3b]extractplanes=g[a];[c][a]alphamerge,format=rgba[rgba]")


def solve_alpha(black: Path, white: Path, out_dir: Path, fps: float, pad: int) -> None:
    cmd = [L.ffmpeg_bin(), "-v", "error", "-y", "-framerate", str(fps), "-i", str(black / f"f%0{pad}d.png"),
           "-framerate", str(fps), "-i", str(white / f"f%0{pad}d.png"),
           "-filter_complex", ALPHA_GRAPH, "-map", "[rgba]", "-start_number", "0",
           str(out_dir / f"f%0{pad}d.png")]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise L.RiveError("alpha solve failed: " + result.stderr[-500:])


def recomposite_error(rgba_png: Path, reference_png: Path, backdrop=L.CLEAR_RGB) -> dict:
    """How far the solved alpha, laid back over the backdrop, is from a single pass."""
    try:
        import numpy as np  # optional: exact numbers when present
        _, _, a_raw = L.png_rgba(rgba_png)
        w, h, r_raw = L.png_rgba(reference_png)
        a = np.frombuffer(a_raw, np.uint8).reshape(h, w, 4).astype(np.float32)
        r = np.frombuffer(r_raw, np.uint8).reshape(h, w, 4)[..., :3].astype(np.float32)
        alpha = a[..., 3:4] / 255.0
        comp = a[..., :3] * alpha + np.array(backdrop, np.float32) * (1 - alpha)
        diff = np.abs(comp - r)
        return {"max": float(diff.max()), "mean": float(diff.mean()), "method": "numpy"}
    except ImportError:
        pass
    colour = "0x" + "".join(f"{v:02X}" for v in backdrop)
    w, h, _ = L.png_rgba(reference_png)
    cmd = [L.ffmpeg_bin(), "-v", "info", "-i", str(rgba_png), "-i", str(reference_png),
           "-filter_complex",
           f"color=c={colour}:s={w}x{h}:d=1[bg];[0:v]format=rgba[fg];"
           "[bg][fg]overlay=format=auto:shortest=1,format=rgb24[comp];[1:v]format=rgb24[ref];[comp][ref]psnr",
           "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"average:([0-9.inf]+)", result.stderr)
    if result.returncode != 0 or not m:
        # No number is not a match. A solved frame that is missing or does not
        # decode left ffmpeg nothing to compare, and reading that as inf let
        # the check pass it (measured on both, without numpy).
        errors = [re.sub(r"^\[[^]]*\]\s*", "", s) for s in result.stderr.splitlines() if "rror" in s]
        return {"error": (errors[0] if errors else f"ffmpeg exited {result.returncode} with no usable PSNR")[:200],
                "method": "ffmpeg-psnr"}
    return {"psnr_db": float(m.group(1)) if m.group(1) != "inf" else math.inf, "method": "ffmpeg-psnr"}


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------


def encode(frames: Path, pad: int, fps: float, out: Path, args, report: dict, has_alpha: bool) -> None:
    ext = out.suffix.lower()
    pattern = str(frames / f"f%0{pad}d.png")
    cmd = [L.ffmpeg_bin(), "-v", "error", "-y", "-framerate", str(fps), "-start_number", "0", "-i", pattern]
    audio = args.audio
    duration = report["frames"] / fps
    if audio and ext in (".mp4", ".mov"):
        if args.audio_offset:
            cmd += ["-ss", str(args.audio_offset)]
        cmd += ["-i", str(audio)]
    # ffmpeg 9 writes the frame's own primaries/transfer (unknown for PNG) over
    # -color_primaries/-color_trc, so the tags are set on the source. Measured:
    # identical YUV either way, but only this spelling tags the file bt709.
    vf = ["setparams=color_primaries=bt709:color_trc=bt709"]
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    w, h = report["size"]
    if ext == ".mp4":
        if w % 2 or h % 2:
            vf.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
            report["warnings"].append(f"{w}x{h} is odd; H.264 4:2:0 needs even sides, padded by one pixel")
        if has_alpha:
            report["warnings"].append("H.264 has no alpha channel; the transparent areas are black here")
        vf.append(f"scale=out_color_matrix=bt709:out_range=tv:flags={ACCURATE}")
        cmd += ["-vf", ",".join(vf), "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
                "-pix_fmt", "yuv420p", *tags, "-color_range", "tv", "-movflags", "+faststart"]
        if audio:
            cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "320k"]
        codec = "h264"
    elif ext == ".mov":
        profile = args.prores or ("4444" if has_alpha else "422hq")
        if profile not in PRORES_PROFILES:
            raise L.RiveError(f"--prores {profile!r}: one of {', '.join(PRORES_PROFILES)}")
        if has_alpha and profile not in ("4444", "4444xq"):
            raise L.RiveError("ProRes carries alpha only in 4444/4444xq", "use --prores 4444")
        pix = ("yuva444p10le" if has_alpha else "yuv444p10le") if profile.startswith("4444") else "yuv422p10le"
        vf.append(f"scale=out_color_matrix=bt709:out_range=tv:flags={ACCURATE}")
        cmd += ["-vf", ",".join(vf), "-c:v", "prores_ks", "-profile:v", str(PRORES_PROFILES[profile]),
                "-vendor", "apl0", "-pix_fmt", pix, *tags, "-color_range", "tv"]
        if has_alpha:
            cmd += ["-alpha_bits", "16"]
        if audio:
            cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "pcm_s24le"]
        codec = f"prores {profile}"
    elif ext == ".webm":
        pix = "yuva420p" if has_alpha else "yuv420p"
        vf.append(f"scale=out_color_matrix=bt709:out_range=tv:flags={ACCURATE}")
        cmd += ["-vf", ",".join(vf), "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(args.webm_crf),
                "-row-mt", "1", "-pix_fmt", pix, *tags, "-color_range", "tv"]
        if has_alpha:
            cmd += ["-auto-alt-ref", "0"]
        codec = "vp9"
    elif ext == ".gif":
        gif_fps = args.gif_fps or min(fps, 30)
        graph = (f"fps={gif_fps},split[a][b];[a]palettegen=reserve_transparent={1 if has_alpha else 0}"
                 ":stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:alpha_threshold=128")
        cmd += ["-filter_complex", graph, "-loop", "0"]
        codec = "gif"
    else:
        raise L.RiveError(f"cannot encode {ext}; use .mp4, .mov, .webm or .gif")
    cmd += ["-t", f"{duration:.6f}", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise L.RiveError(f"ffmpeg could not write {out.name}: {result.stderr.strip()[-600:]}")
    report["encode"] = {"codec": codec, "command": " ".join(cmd[:-1]) + " <out>"}
    report["probe"] = probe_output(out)


def probe_output(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                             "-show_entries", "stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,"
                             "nb_read_frames,color_space,color_primaries,color_transfer,color_range",
                             "-of", "json", str(path)], capture_output=True, text=True)
    try:
        return (json.loads(result.stdout).get("streams") or [{}])[0]
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def plan_frames(args) -> tuple[list[float], bool]:
    """Return (frame times, is_still)."""
    out = Path(args.output)
    is_seq_name = "%" in out.name
    wants_many = args.duration is not None or args.frames is not None
    if out.suffix.lower() == ".png" and not is_seq_name:
        if wants_many:
            raise L.RiveError("a PNG sequence needs a %05d pattern (frames/f%05d.png) or a folder name")
        return [float(args.at or 0.0)], True
    if out.suffix.lower() in VIDEO_EXTS or is_seq_name or out.suffix == "" or wants_many:
        if args.frames is not None:
            n = int(args.frames)
        elif args.duration is not None:
            n = int(round(args.duration * args.fps))
        else:
            raise L.RiveError("a sequence or a video needs --duration SECONDS or --frames N")
        if n < 1:
            raise L.RiveError("nothing to render: zero frames")
        return T.frame_times(args.fps, n, args.start), False
    raise L.RiveError(f"cannot tell what to write from {out.name}")


def render(args) -> dict:
    src = Path(args.source)
    report: dict = {"tool": "rive_render.py", "output": str(Path(args.output).resolve()),
                    "warnings": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    engine_name = args.engine
    if engine_name == "auto":
        engine_name = "web" if src.suffix.lower() == ".riv" else "cli"
    if engine_name == "cli" and not L.is_project(src):
        raise L.RiveError(f"{src} is not a project folder",
                          "the CLI engine renders projects; for a .riv file use --engine web")
    if engine_name == "web" and src.suffix.lower() != ".riv":
        raise L.RiveError("the web engine renders a .riv file",
                          "build one with: rive <dir> --once (unsigned, fine when it has no scripts)")
    timeline = build_timeline(args)
    times, is_still = plan_frames(args)
    report.update({"fps": args.fps, "frames": len(times), "start": times[0] if times else 0,
                   "alpha": bool(args.alpha),
                   "timeline": {"steps": len(timeline.steps), "data": timeline.data,
                                "curves": sorted(timeline.curves)}})
    pad = max(5, len(str(len(times))))
    out = Path(args.output)
    started = time.time()
    engine = None
    work = Path(tempfile.mkdtemp(prefix="rive_skill_frames_", dir=args.work_dir))
    try:
        if engine_name == "cli":
            engine = CliEngine(src, args, report)
            known = set(engine.board.view_model_props)
        else:
            engine = WebEngine(src, args, report)
            known = engine.properties
        if args.curves:
            attach_curves(timeline, Path(args.curves), known, report)
        report["timeline"]["curves"] = sorted(timeline.curves)
        if timeline.curves and engine_name == "cli":
            report["warnings"].append(
                "data curves set each frame's value before that frame's run (level semantics on the CLI "
                "engine): a transition triggered by a change starts at time zero of the run")
        folders: dict[str, Path] = {}
        if engine_name == "cli":
            for pass_name in engine.passes:
                folders[pass_name] = work / pass_name
                folders[pass_name].mkdir()
            late: dict[int, float] = {}
            jobs = [(p, k, t) for p in engine.passes for k, t in enumerate(times)]
            workers = max(1, min(args.workers, len(jobs)))

            def run_one(job):
                pass_name, k, t = job
                fa = engine.capture(pass_name, t, timeline, folders[pass_name] / f"f{k:0{pad}d}.png")
                return k, fa

            # map cancels whatever is still queued when a capture raises
            # (measured: 5 of 400 frames ran), so a failure reports at once.
            # Keyed by frame: an alpha render's two passes are one frame late.
            with concurrent.futures.ThreadPoolExecutor(workers) as pool:
                for k, fa in pool.map(run_one, jobs):
                    if fa.late_by > 1e-6:
                        late[k] = round(fa.late_by, 5)
            if late:
                report["warnings"].append(f"{len(late)} frames were captured late because gestures overlapped")
                report["late_frames"] = [{"frame": k, "late_by_s": late[k]} for k in sorted(late)][:50]
            if args.alpha:
                folders["rgba"] = work / "rgba"
                folders["rgba"].mkdir()
                solve_alpha(folders["black"], folders["white"], folders["rgba"], args.fps, pad)
                final = folders["rgba"]
                check_alpha(engine, timeline, times, final, work, pad, report, allow_bad=args.allow_bad_alpha)
            else:
                final = folders["main"]
            report["sample_args"] = {str(k): timeline.frame_args(times[k]).args
                                     for k in sorted({0, len(times) // 2, len(times) - 1})}
        else:
            final = work / "web"
            final.mkdir()
            per_frame = engine.render_sequence(times, timeline, final, pad)
            events = [{"t": round(f["t"], 5), "events": f["events"]} for f in per_frame if f["events"]]
            if events:
                report["events"] = events[:200]
            if not args.alpha:
                flatten(final, args.background, pad, args.fps, report)
        check_blank(final, len(times), pad, report, args)
        write_output(final, pad, out, args, report, is_still, has_alpha=bool(args.alpha))
    finally:
        if engine:
            engine.close()
        L.remove_tree(work)
    report["seconds"] = round(time.time() - started, 2)
    report["frames_per_second_rendered"] = round(len(times) / max(report["seconds"], 1e-6), 1)
    return report


def attach_curves(timeline: T.Timeline, path: Path, known: set, report: dict) -> None:
    """Bind every curve in a curves file whose name is a view model property."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise L.RiveError(f"cannot read curves {path}: {exc}") from exc
    names = sorted((doc.get("curves") or {}).keys())
    used = [n for n in names if n in known and n not in timeline.curves]
    for name in used:
        timeline.curves[name] = T.curve_from_doc(doc, name)
    report["curves_used"] = used
    unused = [n for n in names if n not in used]
    if unused:
        report["curves_unused"] = unused
    if not used:
        raise L.RiveError(f"no curve in {path.name} matches a view model property "
                          f"(curves: {', '.join(names)}; properties: {', '.join(sorted(known)) or 'none'})")


def flatten(folder: Path, background: str, pad: int, fps: float, report: dict) -> None:
    """Lay web frames (always RGBA) over a solid background for opaque outputs."""
    colour = "0x" + background.upper()
    w, h = report["size"]
    tmp = folder.parent / "flat"
    tmp.mkdir()
    cmd = [L.ffmpeg_bin(), "-v", "error", "-y", "-framerate", str(fps), "-start_number", "0",
           "-i", str(folder / f"f%0{pad}d.png"),
           "-filter_complex", f"color=c={colour}:s={w}x{h}:r={fps}[bg];[0:v]format=rgba[fg];"
           "[bg][fg]overlay=format=auto:shortest=1,format=rgb24[o]", "-map", "[o]", "-start_number", "0",
           str(tmp / f"f%0{pad}d.png")]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise L.RiveError("could not flatten the web frames: " + result.stderr[-400:])
    for p in folder.iterdir():
        p.unlink()
    for p in tmp.iterdir():
        p.rename(folder / p.name)
    tmp.rmdir()
    report["background"] = "#" + background.upper()


def check_alpha(engine: CliEngine, timeline, times, rgba: Path, work: Path, pad: int, report: dict,
                allow_bad: bool = False) -> None:
    """Re-render a few frames single-pass and compare the solved alpha against them.

    A failure stops the render, like a blank one: a mis-solved frame looks
    finished, so a warning alone lets it go out. Measured on a difference
    blend over a 50% fill: 143 codes off, and a warning-only check still
    wrote the file and exited 0.
    """
    ref_snap = L.snapshot_project(engine.source, engine.workdir)
    engine.passes["reference"] = ref_snap
    samples = sorted({0, len(times) // 2, len(times) - 1})
    results = []
    for k in samples:
        ref = work / f"ref_{k}.png"
        engine.capture("reference", times[k], timeline, ref)
        err = recomposite_error(rgba / f"f{k:0{pad}d}.png", ref)
        err["frame"] = k
        results.append(err)
    report["alpha_check"] = results
    bad = [r for r in results if "error" in r or r.get("max", 0) > 8 or r.get("psnr_db", math.inf) < 40]
    if not bad:
        return

    def said(r: dict) -> str:
        if "error" in r:
            return f"frame {r['frame']} could not be compared ({r['error']})"
        if "max" in r:
            return f"frame {r['frame']} off by up to {r['max']:.0f} codes"
        return f"frame {r['frame']} at {r['psnr_db']:.1f} dB"

    worst = ", ".join(said(r) for r in bad)
    if all("error" in r for r in bad):
        problem = "the alpha recomposite check could not run (" + worst + ")"
    else:
        problem = ("the solved alpha does not recomposite onto the single-pass render (" + worst +
                   "; the limit is 8 codes, or 40 dB): something in the scene is not plain src-over "
                   "(a blend mode over transparency?)")
    if not allow_bad:
        raise L.RiveError(problem, "render it opaque (drop --alpha, or put the blend on an opaque plate), "
                                   "or pass --allow-bad-alpha to write it anyway and check the frames by eye")
    report["warnings"].append(problem + "; written anyway because of --allow-bad-alpha")


def check_blank(folder: Path, n: int, pad: int, report: dict, args) -> None:
    samples = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1})
    blank = {}
    for k in samples:
        reason = L.blank_reason(folder / f"f{k:0{pad}d}.png")
        if reason:
            blank[k] = reason
    if blank:
        report["blank_frames"] = {str(k): v for k, v in blank.items()}
        if len(blank) == len(samples) and not args.allow_blank:
            raise L.RiveError("every sampled frame is empty: " + next(iter(blank.values())),
                              "pass --allow-blank if that is intended")
        report["warnings"].append(f"{len(blank)} of {len(samples)} sampled frames are a single flat colour")


def write_output(folder: Path, pad: int, out: Path, args, report: dict, is_still: bool, has_alpha: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if is_still:
        shutil.copy2(folder / f"f{0:0{pad}d}.png", out)
        report["output_sha256"] = L.sha256_file(out)
        return
    ext = out.suffix.lower()
    if ext in VIDEO_EXTS:
        encode(folder, pad, args.fps, out, args, report, has_alpha)
        report["output_sha256"] = L.sha256_file(out)
        if args.keep_frames:
            keep = Path(args.keep_frames)
            keep.mkdir(parents=True, exist_ok=True)
            for p in sorted(folder.iterdir()):
                shutil.copy2(p, keep / p.name)
            report["frames_kept"] = str(keep.resolve())
        return
    # a PNG sequence: a folder, or a %d pattern
    if "%" in out.name:
        target_dir = out.parent
        for k, p in enumerate(sorted(folder.iterdir())):
            shutil.copy2(p, target_dir / (out.name % k))
        report["sequence"] = str(out.resolve())
    else:
        out.mkdir(parents=True, exist_ok=True)
        for p in sorted(folder.iterdir()):
            shutil.copy2(p, out / p.name)
        report["sequence"] = str((out / f"f%0{pad}d.png").resolve())


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a project folder (cli engine) or a .riv file (web engine)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--engine", choices=["auto", "cli", "web"], default="auto")
    ap.add_argument("--artboard")
    ap.add_argument("--state-machine", help="web engine only; the CLI always runs the default one")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--duration", type=float, help="seconds")
    ap.add_argument("--frames", type=int)
    ap.add_argument("--start", type=float, default=0.0, help="scene time of the first frame")
    ap.add_argument("--at", type=float, help="scene time of a still (default: the first frame)")
    ap.add_argument("--size", help="WIDTHxHEIGHT; default the artboard's own size")
    ap.add_argument("--fit", choices=["layout", "contain", "cover", "fill", "fit-width", "fit-height",
                                      "none", "scale-down"])
    ap.add_argument("--alpha", action="store_true", help="transparent output")
    ap.add_argument("--background", default="000000", help="RRGGBB behind transparent areas (default black)")
    ap.add_argument("--keep-backdrop", action="store_true",
                    help="cli engine: keep the CLI's #1D1D1D backdrop instead of --background")
    ap.add_argument("--data", action="append", help="PATH=VALUE for the whole render (repeatable)")
    ap.add_argument("--data-curve", action="append", help="PATH=FILE[:KEY][@LO:HI] per-frame values")
    ap.add_argument("--curves", help="a curves file (rive_audio.py): every curve named like a view model "
                                     "property drives that property, values as they are")
    ap.add_argument("--timeline", help="JSON with events, data and curves")
    ap.add_argument("--click", action="append", help="TIME@X,Y in artboard coordinates")
    ap.add_argument("--drag", action="append", help="TIME@X1,Y1>X2,Y2[:STEPS]")
    ap.add_argument("--key", action="append", help="TIME@KEY (a full press)")
    ap.add_argument("--audio", help="audio to mux into .mp4/.mov (trimmed to the video length)")
    ap.add_argument("--audio-offset", type=float, default=0.0, help="seconds into the audio file to start")
    ap.add_argument("--prores", help="proxy, lt, 422, 422hq, 4444, 4444xq (default 422hq, 4444 with --alpha)")
    ap.add_argument("--crf", type=int, default=14, help="H.264 quality (lower is better)")
    ap.add_argument("--webm-crf", type=int, default=20)
    ap.add_argument("--gif-fps", type=float)
    ap.add_argument("--workers", type=int, default=max(1, min(10, (os.cpu_count() or 4) - 2)))
    ap.add_argument("--frame-timeout", type=float, default=120.0)
    ap.add_argument("--web-runtime", choices=["webgl2", "canvas"], default=None)
    ap.add_argument("--keep-frames", help="also copy the PNG frames here")
    ap.add_argument("--allow-blank", action="store_true")
    ap.add_argument("--allow-bad-alpha", action="store_true",
                    help="write an --alpha render even when its recomposite check fails")
    ap.add_argument("--work-dir", help="parent for scratch folders (default: system temp)")
    ap.add_argument("--report", help="where to write the JSON report (default: next to the output)")
    ap.add_argument("--dry-run", action="store_true", help="print the per-frame CLI arguments and stop")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.fps <= 0:
        ap.error("--fps must be positive")
    try:
        if args.dry_run:
            timeline = build_timeline(args)
            times, _ = plan_frames(args)
            for k in sorted({0, len(times) // 2, len(times) - 1}):
                fa = timeline.frame_args(times[k])
                print(f"frame {k} t={times[k]:.4f}s late={fa.late_by:.4f}: {' '.join(fa.args)}")
            return 0
        report = render(args)
    except L.RiveError as exc:
        print(f"rive_render: {exc}", file=sys.stderr)
        return 1
    report_path = Path(args.report) if args.report else Path(str(Path(args.output)) + ".render.json")
    if "%" in report_path.name:
        report_path = report_path.with_name(report_path.name.replace("%", "pct"))
    L.write_json(report_path, report)
    print(json.dumps({"output": report["output"], "engine": report.get("engine", {}).get("name"),
                      "frames": report["frames"], "seconds": report["seconds"],
                      "warnings": report["warnings"], "report": str(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
