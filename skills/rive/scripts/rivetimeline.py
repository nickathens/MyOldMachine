"""Turn a timeline of gestures and data into per-frame Rive CLI arguments.

A headless capture is one process per frame: build, run the state machine
from time zero, replay the gestures in order, capture. So frame k of a
render is the whole timeline up to its own time, compiled into one flat list
of --advance / --pointer / --key / --data flags. This module does that
compilation and nothing else, which keeps it testable without the CLI.

Costs were measured on Rive CLI 1.1.1 with --data-dump-every=1 (25 Sep 2026):
every primitive pointer, key or gamepad step consumes one 1/60 s frame of
scene time, and a --semantic-action two. A click is three steps (move,
press, release; the listener fires on the release), a --key press is two
(down, up), a drag with s steps is s + 3.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from rivelib import FIRST_FRAME_EPSILON, FRAME, RiveError, advance_arg

EPS = 1e-9


@dataclass(frozen=True)
class Step:
    """One primitive input step, as the CLI replays it."""

    at: float          # scene time the step starts
    flag: str          # the CLI flag, e.g. --pointer=down@10,20
    kind: str          # pointer | key | gamepad | semantic
    web: tuple | None = None  # (method, x, y) for the web engine, None if unsupported
    source: int = 0    # index of the event it came from
    frames: int = 1    # scene time it consumes, in 1/60 s frames

    @property
    def cost(self) -> float:
        return self.frames * FRAME


@dataclass
class FrameArgs:
    args: list[str]
    capture_time: float
    late_by: float = 0.0


@dataclass
class Timeline:
    steps: list[Step] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)            # constant values
    curves: dict[str, "Curve"] = field(default_factory=dict)      # per-frame values

    def frame_args(self, t: float) -> FrameArgs:
        return compile_frame(self, t)


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------


def format_value(value) -> str:
    """Write a value the way --data expects it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise RiveError(f"cannot pass {value} as data")
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"
    return str(value)


@dataclass
class Curve:
    """A value that changes over the render: sampled or keyframed.

    Per-frame data has LEVEL semantics on the CLI engine: --data sets the
    value before the run starts, so frame k shows the scene with the value
    it has at frame k. A transition triggered by the value changing starts
    at time zero of that frame's run, not at the moment it changed. Use the
    web engine, or author the change into the file, for event-style data.
    """

    samples: list | None = None
    fps: float | None = None
    keys: list[tuple[float, object]] | None = None
    interpolation: str = "linear"
    lo: float | None = None
    hi: float | None = None

    def at(self, t: float):
        if self.keys is not None:
            value = _keyed(self.keys, t, self.interpolation)
        else:
            value = _sampled(self.samples or [], self.fps or 1.0, t, self.interpolation)
        if self.lo is not None and self.hi is not None and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            value = self.lo + (self.hi - self.lo) * float(value)
        return value


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sampled(samples: list, fps: float, t: float, mode: str):
    if not samples:
        raise RiveError("a data curve has no samples")
    pos = t * fps
    i = int(math.floor(pos + EPS))
    if i >= len(samples) - 1:
        return samples[-1]
    if i < 0:
        return samples[0]
    a, b = samples[i], samples[i + 1]
    frac = pos - i
    if mode == "hold" or not (_numeric(a) and _numeric(b)) or frac < EPS:
        return a
    return a + (b - a) * frac


def _keyed(keys: list, t: float, mode: str):
    if not keys:
        raise RiveError("a data curve has no keys")
    if t <= keys[0][0] + EPS:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 - EPS <= t < t1 - EPS:
            if mode == "hold" or not (_numeric(v0) and _numeric(v1)) or t1 <= t0:
                return v0
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def load_curve(spec: str, base: Path | None = None) -> tuple[str, Curve]:
    """Parse PATH=FILE[:KEY][@LO:HI] into (path, Curve).

    FILE holds {"fps": 30, "values": [...]}, {"keys": [[t, v], ...]} or, as
    rive_audio.py writes, {"fps": 30, "curves": {"rms": [...], ...}} with
    KEY naming one. @LO:HI maps a 0..1 curve onto a range.
    """
    if "=" not in spec:
        raise RiveError(f"--data-curve {spec!r} needs PATH=FILE[:KEY][@LO:HI]")
    path, rest = spec.split("=", 1)
    lo = hi = None
    if "@" in rest:
        rest, rng = rest.rsplit("@", 1)
        try:
            lo_s, hi_s = rng.split(":", 1)
            lo, hi = float(lo_s), float(hi_s)
        except ValueError as exc:
            raise RiveError(f"range {rng!r} must be LO:HI numbers") from exc
    key = None
    file_part = rest
    if ":" in rest and not Path(rest).exists():
        file_part, key = rest.rsplit(":", 1)
    file_path = Path(file_part)
    if base and not file_path.is_absolute():
        file_path = base / file_path
    try:
        doc = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RiveError(f"cannot read data curve {file_path}: {exc}") from exc
    return path.strip(), curve_from_doc(doc, key, lo, hi)


def curve_from_doc(doc: dict, key: str | None = None, lo=None, hi=None) -> Curve:
    mode = doc.get("interpolation", "linear")
    if "keys" in doc:
        keys = sorted((float(t), v) for t, v in doc["keys"])
        return Curve(keys=keys, interpolation=mode, lo=lo, hi=hi)
    fps = float(doc.get("fps") or 0)
    if fps <= 0:
        raise RiveError("a sampled data curve needs a positive fps")
    if "curves" in doc:
        if not key:
            raise RiveError("this curve file holds several curves; name one: "
                            + ", ".join(sorted(doc["curves"])))
        if key not in doc["curves"]:
            raise RiveError(f"no curve {key!r}; the file has " + ", ".join(sorted(doc["curves"])))
        samples = doc["curves"][key]
    else:
        samples = doc.get("values")
    if not isinstance(samples, list) or not samples:
        raise RiveError("a data curve needs a non-empty list of values")
    return Curve(samples=samples, fps=fps, interpolation=mode, lo=lo, hi=hi)


# --------------------------------------------------------------------------
# gestures
# --------------------------------------------------------------------------


def _pt(v) -> tuple[float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise RiveError(f"a point is [x, y] in artboard coordinates, not {v!r}")
    return float(v[0]), float(v[1])


def _num(v: float) -> str:
    return format_value(float(v))


def expand_event(event: dict, index: int = 0) -> list[Step]:
    """One timeline event into its primitive steps, each one frame long."""
    try:
        at = float(event["at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RiveError(f"event {index} needs an 'at' time in seconds: {event!r}") from exc
    if at < 0:
        raise RiveError(f"event {index} starts before zero")
    steps: list[tuple[str, str, tuple | None, int]] = []

    def pointer(kind: str, x: float, y: float):
        steps.append((f"--pointer={kind}@{_num(x)},{_num(y)}", "pointer", (kind, x, y), 1))

    if "click" in event:
        x, y = _pt(event["click"])
        pointer("move", x, y)
        pointer("down", x, y)
        pointer("up", x, y)
    elif "drag" in event:
        pts = event["drag"]
        if not isinstance(pts, (list, tuple)) or len(pts) != 4:
            raise RiveError(f"event {index}: drag is [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (float(v) for v in pts)
        n = int(event.get("steps", 8))
        if n < 1:
            raise RiveError(f"event {index}: a drag needs at least one step")
        pointer("move", x1, y1)
        pointer("down", x1, y1)
        for i in range(1, n + 1):
            f = i / n
            pointer("move", x1 + (x2 - x1) * f, y1 + (y2 - y1) * f)
        pointer("up", x2, y2)
    elif any(k in event for k in ("down", "up", "move", "exit")):
        for kind in ("move", "down", "up", "exit"):
            if kind in event:
                x, y = _pt(event[kind])
                pointer(kind, x, y)
    elif "key" in event:
        key = str(event["key"])
        mods = event.get("mods")
        suffix = ("+" + "+".join(mods)) if isinstance(mods, (list, tuple)) and mods else \
            (f"+{mods}" if isinstance(mods, str) and mods else "")
        phase = event.get("phase")
        if phase in (None, "press"):
            steps.append((f"--key={key}:down{suffix}", "key", None, 1))
            steps.append((f"--key={key}:up{suffix}", "key", None, 1))
        elif phase in ("down", "up", "repeat"):
            steps.append((f"--key={key}:{phase}{suffix}", "key", None, 1))
        else:
            raise RiveError(f"event {index}: key phase is press, down, up or repeat")
    elif "gamepad" in event:
        steps.append((f"--gamepad={event['gamepad']}", "gamepad", None, 1))
    elif "semantic" in event:
        steps.append((f"--semantic-action={event['semantic']}", "semantic", None, 2))
    else:
        raise RiveError(f"event {index} does nothing I know: {event!r}")
    out: list[Step] = []
    offset = 0.0
    for flag, kind, web, frames in steps:
        out.append(Step(at=at + offset, flag=flag, kind=kind, web=web, source=index, frames=frames))
        offset += frames * FRAME
    return out


def build_timeline(events: list[dict] | None = None, data: dict | None = None,
                   curves: dict[str, Curve] | None = None) -> Timeline:
    steps: list[Step] = []
    for i, event in enumerate(events or []):
        steps.extend(expand_event(event, i))
    steps.sort(key=lambda s: (s.at, s.source))
    return Timeline(steps=steps, data={k: format_value(v) for k, v in (data or {}).items()},
                    curves=dict(curves or {}))


def load_timeline_file(path: str | Path) -> Timeline:
    """A JSON timeline: {"events": [...], "data": {...}, "curves": {...}}."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RiveError(f"cannot read timeline {path}: {exc}") from exc
    curves = {}
    for name, spec in (doc.get("curves") or {}).items():
        if isinstance(spec, str):
            _, curves[name] = load_curve(f"{name}={spec}", base=path.parent)
        elif isinstance(spec, dict) and "file" in spec:
            rng = spec.get("range")
            text = f"{name}={spec['file']}" + (f":{spec['key']}" if spec.get("key") else "")
            if rng:
                text += f"@{rng[0]}:{rng[1]}"
            _, curves[name] = load_curve(text, base=path.parent)
        elif isinstance(spec, dict):
            curves[name] = curve_from_doc(spec)
        else:
            raise RiveError(f"curve {name!r} must be a file spec or an inline curve")
    return build_timeline(doc.get("events"), doc.get("data"), curves)


# --------------------------------------------------------------------------
# compiling one frame
# --------------------------------------------------------------------------


def compile_frame(timeline: Timeline, t: float) -> FrameArgs:
    """The CLI flags that leave the scene at time t with everything before it applied.

    A step is included once it has finished by t. Steps that would overlap
    (two gestures closer than their cost) run back to back, and the frame is
    captured that much later; FrameArgs.late_by says by how much.
    """
    args: list[str] = []
    for path, value in timeline.data.items():
        args.append(f"--data={path}={value}")
    for path, curve in timeline.curves.items():
        args.append(f"--data={path}={format_value(curve.at(t))}")
    cursor = 0.0
    advanced = False
    for step in timeline.steps:
        if step.at + step.cost > t + EPS:
            break
        start = max(step.at, cursor)
        gap = start - cursor
        if gap > EPS:
            args.append(advance_arg(gap))
            advanced = True
        args.append(step.flag)
        cursor = start + step.cost
        advanced = True
    remaining = t - cursor
    late = 0.0
    if remaining > EPS:
        args.append(advance_arg(remaining))
        advanced = True
    elif remaining < -EPS:
        late = -remaining
    if not advanced:
        # frame 0: the smallest real advance, so the state machine has run
        args.append(advance_arg(FIRST_FRAME_EPSILON))
    return FrameArgs(args=args, capture_time=max(t, cursor), late_by=late)


def frame_times(fps: float, frames: int, start: float = 0.0) -> list[float]:
    if fps <= 0:
        raise RiveError("fps must be positive")
    return [start + k / fps for k in range(frames)]


def web_schedule(timeline: Timeline) -> list[dict]:
    """The same steps for the web engine: pointer only, at the same times."""
    out = []
    for step in timeline.steps:
        if step.web is None:
            raise RiveError(f"the web engine cannot replay {step.kind} input ({step.flag}); "
                            "render this timeline with the CLI engine")
        kind, x, y = step.web
        out.append({"at": step.at, "kind": kind, "x": x, "y": y})
    return out
