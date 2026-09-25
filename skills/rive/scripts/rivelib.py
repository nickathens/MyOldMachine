"""Shared helpers for the rive skill: find the CLI, run it, read projects.

Standard library only. The scripts in this folder import it by path, so it
must stay importable with nothing but Python 3.10 and the `rive` binary.

Everything here was measured against Rive CLI 1.1.1 on macOS arm64 on
25 Sep 2026. The CLI is a technical preview that has already renamed one
flag (--frame became --advance), so every script checks the version it runs
against and `rive_doctor.py` re-probes the flags after an update.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

# The CLI versions these scripts were measured against. A different version
# is allowed, but reported, so a changed flag shows up as a named cause.
TESTED_CLI_VERSIONS = ("1.1.1",)

# The CLI steps scene time in fixed 1/60 s frames, whatever --advance says.
FRAME = 1.0 / 60.0

# What a capture shows where the artboard draws nothing at all.
CLEAR_RGB = (0x1D, 0x1D, 0x1D)

# Frame 0 of a video is captured at this advance. --advance=0 (and no
# --advance) is the authored rest pose, before the state machine applied its
# entry animation; a text keyed to opacity 0 on frame 0 still shows there.
FIRST_FRAME_EPSILON = 1e-5  # seconds

EXIT_MEANINGS = {
    0: "ok",
    1: "failure (build errors, a bad --data value, or anything without a more specific code)",
    2: "bad command line (a flag this CLI does not know, or a malformed value)",
    3: "not logged in, or the session was rejected (run `rive login`)",
    6: "Tests scripts failed (the build itself was fine)",
    7: "a Rive service could not be reached; safe to retry",
    8: "this CLI is below the published minimum (brew upgrade --cask rive-cli, or rive update on a curl install)",
}


class RiveError(RuntimeError):
    """A failure with a plain explanation and, where there is one, the fix."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        return f"{base}\n  hint: {self.hint}" if self.hint else base


# --------------------------------------------------------------------------
# finding and running the CLI
# --------------------------------------------------------------------------


def find_rive() -> str | None:
    """Return the rive binary to use, or None.

    RIVE_SKILL_CLI wins, so a second version can be tested side by side the
    way the Mermaid update was (a scratch install first on PATH).
    """
    forced = os.environ.get("RIVE_SKILL_CLI")
    if forced:
        return forced if os.access(forced, os.X_OK) else None
    found = shutil.which("rive")
    if found:
        return found
    curl_install = Path.home() / ".rive" / "bin" / "rive"
    if curl_install.is_file() and os.access(curl_install, os.X_OK):
        return str(curl_install)
    return None


def require_rive() -> str:
    binary = find_rive()
    if not binary:
        raise RiveError(
            "the Rive CLI is not installed",
            "run: python3 skills/rive/scripts/rive_doctor.py --install",
        )
    return binary


def account_config_home() -> str | None:
    """Where this session's Rive login lives.

    The CLI keeps its OAuth token at $XDG_CONFIG_HOME/rive/app.rive.cli/
    oauth.prod (measured with fs_usage, 25 Sep 2026). One OS account hosts
    several Telegram users here, so each session gets its own config home
    under its private user directory; without that, one person's
    `rive login` would publish and push as them for everybody.
    """
    user_dir = os.environ.get("JARVIS_USER_DIR")
    if not user_dir:
        return None
    return str(Path(user_dir) / "rive" / "config")


def rive_env(headless: bool = True, extra: dict | None = None) -> dict:
    """The environment every skill call runs the CLI with.

    Analytics were never consented to on this machine, so they are off per
    call rather than by flipping the stored setting.
    """
    env = dict(os.environ)
    env.update({"RIVE_ANALYTICS": "off", "RIVE_NO_TUI": "1", "NO_COLOR": "1"})
    if headless:
        # Captures never play sound; this keeps a server without an audio
        # device from failing on one. No pixel difference (measured).
        env["RIVE_NO_AUDIO_DEVICE"] = "1"
    config_home = account_config_home()
    if config_home:
        # not created here: a read (whoami, a render) works without it, and
        # nothing should appear in a user's folder unless they log in
        env["XDG_CONFIG_HOME"] = config_home
    if extra:
        env.update(extra)
    return env


def run_rive(args: list[str], *, cwd: str | Path | None = None, timeout: float = 180,
             headless: bool = True, binary: str | None = None,
             env: dict | None = None) -> subprocess.CompletedProcess:
    binary = binary or require_rive()
    try:
        return subprocess.run([binary, *args], cwd=str(cwd) if cwd else None,
                              env=env or rive_env(headless=headless),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RiveError(f"rive {' '.join(args[:3])} ... timed out after {timeout:.0f} s",
                        "a scene with a very long --advance or a script stuck in a loop") from exc


def cli_version(binary: str | None = None) -> str | None:
    binary = binary or find_rive()
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=30, env=rive_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", out)
    return match.group(1) if match else None


def version_note(version: str | None) -> str | None:
    """A warning when the CLI is not one these scripts were measured against."""
    if version is None:
        return "could not read the Rive CLI version"
    if version not in TESTED_CLI_VERSIONS:
        return (f"Rive CLI {version} is not a version this skill was measured against "
                f"({', '.join(TESTED_CLI_VERSIONS)}); run rive_doctor.py before trusting a render")
    return None


def explain_exit(code: int) -> str:
    return EXIT_MEANINGS.get(code, f"exit code {code}")


def parse_envelope(stdout: str) -> dict | None:
    """Parse the one-JSON-object envelope --format=json prints on stdout."""
    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------


def is_project(path: str | Path) -> bool:
    return (Path(path) / "rive.yaml").is_file()


def yaml_scalar(path: Path, key: str) -> str | None:
    """Read one top-level `key: value` from rive.yaml without a YAML library."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    if not match:
        return None
    value = match.group(1).split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value or None


def project_name(project: str | Path) -> str:
    project = Path(project)
    return yaml_scalar(project / "rive.yaml", "name") or project.resolve().name


def rml_files(project: str | Path) -> list[Path]:
    project = Path(project)
    return sorted(p for p in project.rglob("*.rml")
                  if "build" not in p.relative_to(project).parts)


_FILE_ATTR = re.compile(r'(\bfile\s*=\s*")([^"]*)(")')


def _absolutise_external_refs(snapshot: Path, original: Path) -> list[str]:
    """Point asset paths that leave the project at their real files.

    RML allows file="../../fonts/Inter.ttf". A copy of the project moves the
    base those resolve against, so rewrite them to absolute paths. Returns
    the paths it rewrote.
    """
    rewritten: list[str] = []
    for rml in rml_files(snapshot):
        text = rml.read_text(encoding="utf-8")

        def fix(match: re.Match) -> str:
            ref = match.group(2)
            if not ref or os.path.isabs(ref) or "://" in ref:
                return match.group(0)
            # file= is resolved against the project directory, not the .rml
            target = (original / ref).resolve()
            inside = target == original.resolve() or original.resolve() in target.parents
            if inside:
                return match.group(0)
            rewritten.append(ref)
            return f"{match.group(1)}{target}{match.group(3)}"

        new = _FILE_ATTR.sub(fix, text)
        if new != text:
            rml.write_text(new, encoding="utf-8")
    yaml_path = snapshot / "rive.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_libs = False
    changed = False
    for i, line in enumerate(lines):
        if re.match(r"^libraries:\s*$", line):
            in_libs = True
            continue
        if in_libs:
            m = re.match(r"^(\s*-\s*)(.+?)\s*$", line)
            if not m:
                in_libs = bool(line.startswith((" ", "\t")))
                continue
            ref = m.group(2).strip("\"'")
            if not os.path.isabs(ref):
                lines[i] = f"{m.group(1)}{(original / ref).resolve()}"
                rewritten.append(ref)
                changed = True
    if changed:
        yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rewritten


def snapshot_project(project: str | Path, workdir: str | Path | None = None) -> Path:
    """Copy a project into a private scratch folder and return the copy.

    Renders never touch the user's own folder: parallel captures would all
    write build/<name>.riv and the logs at once, and the alpha passes edit
    the markup. The copy lives in a mkdtemp folder that only the caller
    removes, by that exact path.
    """
    project = Path(project).resolve()
    if not is_project(project):
        raise RiveError(f"{project} is not a Rive project (no rive.yaml)")
    parent = tempfile.mkdtemp(prefix="rive_skill_snap_", dir=str(workdir) if workdir else None)
    dest = Path(parent) / project.name
    shutil.copytree(project, dest,
                    ignore=shutil.ignore_patterns("build", ".git", "node_modules", "__pycache__"))
    _absolutise_external_refs(dest, project)
    return dest


def remove_tree(path: str | Path | None) -> None:
    """Remove a scratch folder this process created. Never by pattern."""
    if path and Path(path).exists():
        shutil.rmtree(path, ignore_errors=True)


def hash_project(project: str | Path) -> str:
    """A stable fingerprint of the sources a build reads."""
    project = Path(project)
    digest = hashlib.sha256()
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project)
        if not path.is_file() or rel.parts[0] in ("build", ".git") or path.suffix in (".riv", ".rev"):
            continue
        digest.update(str(rel).encode())
        digest.update(sha256_file(path).encode())
    return digest.hexdigest()


# --------------------------------------------------------------------------
# reading what a project contains (rive inspect --json)
# --------------------------------------------------------------------------


@dataclass
class ArtboardInfo:
    name: str
    id: str | None
    width: float
    height: float
    state_machine: str | None = None
    state_machines: list[str] = field(default_factory=list)
    view_model: str | None = None
    view_model_props: dict[str, str] = field(default_factory=dict)
    background: list[str] = field(default_factory=list)  # colorValue per direct Fill, "gradient" otherwise

    @property
    def size(self) -> tuple[int, int]:
        return int(round(self.width)), int(round(self.height))

    def opaque_background(self) -> bool:
        """True when a direct artboard fill would cover a transparent render."""
        for paint in self.background:
            if paint == "gradient":
                return True
            if len(paint) == 8 and paint[:2].upper() == "FF":
                return True
        return False


_PROPERTY_KIND = {
    "ViewModelPropertyNumber": "number",
    "ViewModelPropertyString": "string",
    "ViewModelPropertyBoolean": "boolean",
    "ViewModelPropertyColor": "color",
    "ViewModelPropertyTrigger": "trigger",
    "ViewModelPropertyEnumCustom": "enum",
    "ViewModelPropertyEnumSystem": "enum",
    "ViewModelPropertyViewModel": "viewModel",
    "ViewModelPropertyList": "list",
    "ViewModelPropertyAssetImage": "image",
    "ViewModelPropertyAssetFont": "font",
    "ViewModelPropertyArtboard": "artboard",
    "ViewModelPropertySymbolListIndex": "index",
}


def inspect_project(project: str | Path, timeout: float = 120) -> dict:
    result = run_rive(["inspect", str(project), "--json"], timeout=timeout)
    data = parse_envelope(result.stdout)
    if data is None:
        raise RiveError(f"rive inspect printed no JSON (exit {result.returncode}: "
                        f"{explain_exit(result.returncode)})", result.stderr.strip()[-400:] or None)
    return data


def _view_models(inspect: dict) -> dict[str, dict]:
    models = {}
    for root in inspect.get("roots") or []:
        if root.get("type") == "ViewModel" and root.get("id"):
            props = {}
            for child in root.get("children") or []:
                kind = _PROPERTY_KIND.get(child.get("type", ""))
                if kind and child.get("name"):
                    props[child["name"]] = kind
            models[root["id"]] = {"name": root.get("name"), "props": props}
    return models


def artboards(inspect: dict) -> list[ArtboardInfo]:
    models = _view_models(inspect)
    found = []
    for board in inspect.get("artboards") or []:
        children = board.get("children") or []
        machines = {c.get("id"): c.get("name") for c in children if c.get("type") == "StateMachine"}
        background = []
        for child in children:
            if child.get("type") != "Fill":
                continue
            for paint in child.get("children") or []:
                if paint.get("type") == "SolidColor":
                    background.append(str(paint.get("colorValue", "")).upper())
                elif "Gradient" in paint.get("type", ""):
                    background.append("gradient")
        vm = models.get(board.get("viewModelId") or "")
        found.append(ArtboardInfo(
            name=board.get("name") or "",
            id=board.get("id"),
            width=float(board.get("width") or 0),
            height=float(board.get("height") or 0),
            state_machine=machines.get(board.get("defaultStateMachineId")),
            state_machines=[n for n in machines.values() if n],
            view_model=vm["name"] if vm else None,
            view_model_props=dict(vm["props"]) if vm else {},
            background=background,
        ))
    return found


def pick_artboard(inspect: dict, name: str | None = None) -> ArtboardInfo:
    boards = artboards(inspect)
    if not boards:
        raise RiveError("the project has no artboards", "check `rive inspect <dir> --summary` for problems")
    if name:
        for board in boards:
            if board.name == name:
                return board
        raise RiveError(f"no artboard named {name!r}", "artboards: " + ", ".join(b.name for b in boards))
    default = (inspect.get("defaultArtboard") or {}).get("name")
    for board in boards:
        if board.name == default:
            return board
    return boards[0]


def problems(inspect: dict) -> list[dict]:
    return list(inspect.get("problems") or [])


# --------------------------------------------------------------------------
# editing a snapshot's markup
# --------------------------------------------------------------------------


def _find_artboard_tag(text: str, board: ArtboardInfo) -> re.Match | None:
    for match in re.finditer(r"<Artboard\b[^>]*?/?>", text, flags=re.S):
        tag = match.group(0)
        if board.id and re.search(rf'\bid\s*=\s*"{re.escape(board.id)}"', tag):
            return match
        if re.search(rf'\bname\s*=\s*"{re.escape(board.name)}"', tag):
            return match
    return None


def add_artboard_background(project: str | Path, board: ArtboardInfo, argb: str) -> Path:
    """Give an artboard a solid background fill in a snapshot copy.

    An artboard paints its own fills behind its children, so a fill added
    as its first child sits under everything the scene draws. Used for the
    black and white passes that solve a transparent render.
    """
    fill = (f'<Fill name="__rive_skill_bg"><SolidColor colorValue="{argb}" '
            f'name="__rive_skill_bg_color"/></Fill>')
    for rml in rml_files(project):
        text = rml.read_text(encoding="utf-8")
        match = _find_artboard_tag(text, board)
        if not match:
            continue
        tag = match.group(0)
        if tag.endswith("/>"):
            replacement = tag[:-2].rstrip() + ">" + fill + "</Artboard>"
        else:
            replacement = tag + fill
        rml.write_text(text[:match.start()] + replacement + text[match.end():], encoding="utf-8")
        return rml
    raise RiveError(f"could not find the <Artboard> tag for {board.name!r} in the markup")


# --------------------------------------------------------------------------
# pixels, through ffmpeg (no imaging library needed)
# --------------------------------------------------------------------------


def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RiveError("ffmpeg is not installed", "brew install ffmpeg (macOS) or apt install ffmpeg")
    return found


def png_rgba(path: str | Path) -> tuple[int, int, bytes]:
    """Decode an image to raw RGBA bytes with ffmpeg."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True)
    try:
        width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    except ValueError as exc:
        raise RiveError(f"cannot read the size of {path}") from exc
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    if len(raw) != width * height * 4:
        raise RiveError(f"could not decode {path}")
    return width, height, raw


def single_colour(path: str | Path) -> tuple[bool, tuple[int, int, int, int]]:
    """Is every pixel of the image the same colour? Returns (answer, that pixel)."""
    width, height, raw = png_rgba(path)
    first = raw[:4]
    return raw == first * (width * height), tuple(first)


def blank_reason(path: str | Path) -> str | None:
    """Why a capture is empty, or None when it shows something.

    Every structural check Rive has passes on a scene that draws nothing,
    and the Linux build draws nothing at all on Mesa drivers
    (rive-app/rive-runtime#92), so a render has to look.
    """
    same, pixel = single_colour(path)
    if not same:
        return None
    if pixel[:3] == CLEAR_RGB:
        reason = ("every pixel is the CLI's empty backdrop #1D1D1D: nothing was drawn "
                  "(an empty scene, a missing default state machine, or content off the artboard)")
    else:
        colour = "".join(f"{v:02X}" for v in pixel[:3])
        reason = f"every pixel is #{colour}: only a flat background is visible"
    if platform.system() == "Linux":
        reason += ("; on Linux with Mesa drivers this is the known blank-capture bug "
                   "(rive-app/rive-runtime#92): see references/rendering.md, Linux")
    return reason


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def advance_arg(seconds: float) -> str:
    """The --advance flag for a span of scene time.

    A whole number of 1/60 s frames is passed as a frame count, so 30 and
    60 fps renders step exactly as the live player does. Anything else goes
    as fixed-point milliseconds (never an exponent); the CLI then runs whole
    frames plus one shorter one.
    """
    if seconds < 0:
        raise ValueError("an advance cannot be negative")
    frames = seconds * 60.0
    if frames >= 0.5 and abs(frames - round(frames)) < 1e-6:
        return f"--advance={int(round(frames))}"
    text = f"{seconds * 1000:.4f}".rstrip("0").rstrip(".")
    return f"--advance={text or '0'}ms"


def write_json(path: str | Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
