#!/usr/bin/env python3
"""The gate to run after every pass on a Rive project.

`rive --verify` proves the project compiles and `rive inspect` proves some of
the wiring, but neither looks at a pixel and both pass a scene that draws
nothing. This runs all three lenses plus the silent failures neither
reports, and writes pictures you can look at:

  1  build        rive <dir> --verify (RML, Luau types, shaders, re-import),
                  then rive <dir> --test when the project has Luau scripts
                  (a failing case exits 6 with its name, line and message)
  2  wiring       rive inspect problems, object counts per artboard
  3  lint         the silent failures from references/rml.md that neither
                  reports (units without *UnitsValue, cubic keys with no
                  curve, text styles the editor cannot label, fonts taken
                  from the system, keyboard phase masks, script inputs that
                  match no Input<> field, ...)
  4  pixels       a capture at --at seconds, refused when it is one flat colour
  5  optional     --sizes (responsive), --interaction (rest / on / off),
                  --probe-binds (which data visibly drives the picture)

Exit code 0 means no errors (warnings may remain), 1 means something failed.

  rive_check.py myproject
  rive_check.py myproject --at 2 --sizes 390x844,1280x720 --interaction click@120,60
  rive_check.py myproject --probe-binds --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402

LUAU_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if",
                 "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "type", "typeof",
                 "until", "while", "continue", "export"}

UNIT_PAIRS = ["padding", "margin", "gap", "border", "position"]
SIDES = {"padding": ["Left", "Right", "Top", "Bottom"], "margin": ["Left", "Right", "Top", "Bottom"],
         "border": ["Left", "Right", "Top", "Bottom"], "position": ["Left", "Right", "Top", "Bottom"],
         "gap": ["Horizontal", "Vertical"]}

SYSTEM_FONT_DIRS = ("/Library/Fonts", "/System/Library/Fonts", str(Path.home() / "Library" / "Fonts"),
                    "/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"),
                    str(Path.home() / ".local" / "share" / "fonts"))


def in_system_fonts(ref: str) -> bool:
    """Is this font path inside a system or user font folder (by folder, not by name prefix)?"""
    path = Path(ref)
    return any(Path(d) == path or Path(d) in path.parents for d in SYSTEM_FONT_DIRS)


def walk(node):
    if isinstance(node, dict):
        yield node
        for child in node.get("children") or []:
            yield from walk(child)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


# --------------------------------------------------------------------------
# lint: the silent failures, read from the markup and the resolved tree
# --------------------------------------------------------------------------


def lint_markup(project: Path) -> list[dict]:
    findings: list[dict] = []

    def add(severity, kind, rml, line, message):
        findings.append({"severity": severity, "kind": kind, "file": str(rml.relative_to(project)),
                         "line": line, "message": message})

    for rml in L.rml_files(project):
        text = rml.read_text(encoding="utf-8")
        for comment in re.finditer(r"<!--(.*?)-->", text, flags=re.S):
            if "--" in comment.group(1):
                add("warning", "comment-double-dash", rml, text.count("\n", 0, comment.start()) + 1,
                    "an XML comment contains '--' (a CLI flag?): the Rive CLI accepts it, strict XML parsers "
                    "and other tools reject the file")
        for match in re.finditer(r"<(\w+)\b([^>]*)>", text, flags=re.S):
            element, attrs = match.group(1), match.group(2)
            line = text.count("\n", 0, match.start()) + 1
            names = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', attrs))
            if element in ("LayoutComponentStyle", "LayoutParticipant"):
                for prop, sides in SIDES.items():
                    for side in sides:
                        key = f"{prop}{side}"
                        if key in names and f"{key}UnitsValue" not in names:
                            add("warning", "units-undefined", rml, line,
                                f"{element} sets {key}={names[key]} with no {key}UnitsValue; units default "
                                "to undefined, so the value is ignored (write "
                                f'{key}UnitsValue="points")')
            if element == "TextStylePaint":
                if "familyName" not in names or "styleName" not in names:
                    add("warning", "text-style-unlabelled", rml, line,
                        "TextStylePaint without familyName and styleName renders fine but opens in the "
                        "editor with Font and Weight showing '-'")
            if element == "FontAsset":
                ref = names.get("file")
                if not ref:
                    add("warning", "font-without-file", rml, line,
                        f"FontAsset {names.get('name', '')!r} has no file=, so text using it renders as "
                        "nothing here (the host is meant to supply it)")
                elif in_system_fonts(ref):
                    add("error", "system-font-embedded", rml, line,
                        f"{ref} is a system font; building the .riv redistributes it. Use a font licensed "
                        "for embedding (OFL from Google Fonts: rive_fonts.py add)")
            if element == "KeyboardInput":
                phase = names.get("keyPhase", "0")
                if phase == "0":
                    add("warning", "key-phase-zero", rml, line,
                        "KeyboardInput keyPhase is 0 (the default), which matches no phase: the listener is dead")
                elif phase == "7":
                    add("warning", "key-phase-seven", rml, line,
                        "keyPhase 7 fires on press AND release: one keystroke acts twice (use 1, or 3 to repeat)")
            order = names.get("childOrder") or names.get("order")
            if order is not None and "/" not in order:
                add("warning", "fractional-index", rml, line,
                    f'order "{order}" has no slash, so it parses as invalid and is ignored; write "{order}/1"')
            if element == "Fill":
                close = text.find("</Fill>", match.end())
                inner = text[match.end():close] if close > 0 else ""
                if "<Feather" in inner:
                    add("error", "feather-in-fill", rml, line,
                        "Feather inside a Fill renders nothing at any strength; feather a Stroke, or use a "
                        "RadialGradient to transparent for a soft filled glow")
            if element == "DataBindContext" and names.get("nameBased") == "true":
                add("warning", "name-based-bind", rml, line,
                    "nameBased binds need a ManifestAsset this toolchain cannot build; the bind is inert")
            if element == "NestedSimpleAnimation" and names.get("isPlaying") != "true":
                add("warning", "nested-animation-paused", rml, line,
                    "NestedSimpleAnimation isPlaying defaults to false: the child holds its first frame")
            if element == "TransitionValueIdComparator":
                add("error", "abstract-comparator", rml, line,
                    "TransitionValueIdComparator is a base class; use the Enum/Asset/Artboard comparator")
    findings += lint_script_inputs(project)
    return findings


def lint_script_inputs(project: Path) -> list[dict]:
    """ScriptInput* children bind to Input<> fields by name, and nothing checks it."""
    scene_inputs: set[str] = set()
    for rml in L.rml_files(project):
        for m in re.finditer(r'<ScriptInput\w*\b[^>]*\bname\s*=\s*"([^"]+)"', rml.read_text(encoding="utf-8")):
            scene_inputs.add(m.group(1))
    if not scene_inputs:
        return []
    fields: set[str] = set()
    for luau in project.rglob("*.luau"):
        if "build" in luau.relative_to(project).parts:
            continue
        for m in re.finditer(r"(\w+)\s*:\s*Input<", luau.read_text(encoding="utf-8")):
            fields.add(m.group(1))
    missing = sorted(scene_inputs - fields)
    return [{"severity": "warning", "kind": "script-input-unmatched", "file": "*.rml", "line": 0,
             "message": f"ScriptInput {name!r} matches no Input<> field in any .luau file; the script keeps "
                        "its default and the scene's value is discarded"} for name in missing]


def lint_tree(inspect: dict) -> list[dict]:
    findings = []
    for node in walk(inspect.get("artboards") or []):
        t = node.get("type", "")
        if t.startswith("KeyFrame") and (node.get("enums") or {}).get("interpolationType") in ("cubic", "elastic"):
            kids = [c for c in node.get("children") or [] if str(c.get("type", "")).endswith("Interpolator")]
            if not kids:
                findings.append({"severity": "warning", "kind": "curve-missing", "line": node.get("line"),
                                 "message": f"{t} at frame {node.get('frame', 0)} is "
                                            f"{node['enums']['interpolationType']} with no interpolator "
                                            "child, so the segment does not ease"})
        if t == "LayoutComponent" and not node.get("styleId"):
            findings.append({"severity": "warning", "kind": "layout-style-unlinked", "line": node.get("line"),
                             "message": f"LayoutComponent {node.get('name')!r} names no style (styleId), so none of "
                                        "its flex settings apply"})
        if t == "TextModifierGroup" and not node.get("modifierFlags"):
            findings.append({"severity": "warning", "kind": "modifier-flags-zero", "line": node.get("line"),
                             "message": "TextModifierGroup has modifierFlags 0: every value on it is inert "
                                        "(set modifyTranslation/Rotation/Scale/Opacity)"})
    for root in inspect.get("roots") or []:
        if root.get("type") != "ViewModel":
            continue
        for prop in root.get("children") or []:
            name = prop.get("name") or ""
            if not str(prop.get("type", "")).startswith("ViewModelProperty"):
                continue
            if name in LUAU_KEYWORDS or re.match(r"^_*\d", name):
                findings.append({"severity": "warning", "kind": "vm-name", "line": prop.get("line"),
                                 "message": f"view model property {name!r} is a Luau keyword or starts with a "
                                            "digit; the editor will flag it and scripts cannot name it"})
    return findings


def run_tests(project: Path, report: dict) -> None:
    """Run the project's Luau Tests scripts, when it has any Luau at all.

    Measured on CLI 1.1.1: all pass exits 0, a failing case exits 6 with
    data.failures [{test, line, message}], a script that does not parse
    exits 1, and a project with no Tests scripts exits 0 with noTestsFound.
    """
    if not any("build" not in f.relative_to(project).parts for f in project.rglob("*.luau")):
        return
    result = L.run_rive([str(project), "--test", "--format=json"], timeout=300)
    envelope = L.parse_envelope(result.stdout) or {}
    data = envelope.get("data") or {}
    if result.returncode == 0 and data.get("noTestsFound"):
        return
    report["tests"] = {"exit": result.returncode, "passed": data.get("passed", 0),
                       "failed": data.get("failed", 0), "failures": data.get("failures", [])}
    if result.returncode == 0:
        return
    failures = data.get("failures") or []
    for f in failures:
        report["errors"].append(f"test {f.get('test')} (line {f.get('line')}): {f.get('message')}")
    others = [e for e in envelope.get("errors") or [] if not any(e.endswith(str(f.get("message"))) for f in failures)]
    if others or not failures:
        detail = others or [(result.stderr or result.stdout).strip()[-500:]]
        report["errors"].append(f"the Tests scripts could not run ({L.explain_exit(result.returncode)}): "
                                + "; ".join(detail)[:900])


# --------------------------------------------------------------------------
# pixels
# --------------------------------------------------------------------------


def capture(project: Path, out: Path, args: list[str]) -> tuple[bool, str]:
    result = L.run_rive([str(project), "--quiet", f"--screenshot={out}", *args], timeout=180)
    if result.returncode != 0 or not out.is_file():
        return False, (result.stderr or result.stdout).strip()[-400:]
    return True, ""


def current_values(project: Path, args: list[str]) -> dict:
    """The bound view model's values at the capture time, from --data-dump."""
    result = L.run_rive([str(project), "--quiet", "--data-dump=-", *args], timeout=120)
    doc = L.parse_envelope(result.stdout) or {}
    values = {}
    for prop in ((doc.get("viewModel") or {}).get("properties") or []):
        values[prop.get("name")] = prop.get("value")
    return values


def probe_value(kind: str, current, prop: str) -> str | None:
    """A value that cannot look like the current one."""
    if kind == "string":
        return "PROBE-" + prop.upper()
    if kind == "boolean":
        return "false" if current is True else "true"
    if kind == "number":
        try:
            base = float(current)
        except (TypeError, ValueError):
            base = 0.0
        return str(round(base * 0.25 + 73.0, 3)) if base != 0 else "73"
    if kind == "color":
        return "FF00FF00" if str(current).upper() == "FFFF00FF" else "FFFF00FF"
    return None


def same_picture(a: Path, b: Path) -> bool:
    return L.sha256_file(a) == L.sha256_file(b)


AWAY = "--pointer=move@-1000,-1000"


def interaction_shots(at: float, x: str, y: str, settle: float) -> dict[str, list[str]]:
    """The four captures --interaction compares, in pairs at the same scene time.

    Each capture is compared with a rest capture at the SAME scene time, so an
    idle animation cannot read as a response: a click costs 3 frames and a
    move 1 (measured on CLI 1.1.1). The pointer leaves the artboard after
    every click, or a hover style would read as the click working (measured:
    the button template with its click listener removed passed, on its hover
    scale alone, while the pointer stayed over it).
    """
    base = max(at, L.FIRST_FRAME_EPSILON)
    start, wait, click = L.advance_arg(base), L.advance_arg(settle), f"--pointer=click@{x},{y}"
    return {
        "rest_on": [L.advance_arg(base + 4 * L.FRAME + settle)],
        "on": [start, click, AWAY, wait],
        "rest_off": [L.advance_arg(base + 8 * L.FRAME + 2 * settle)],
        "off": [start, click, AWAY, wait, click, AWAY, wait],
    }


def probe_args(base_args: list[str], data_args: list[str], prop: str, value: str, at: str) -> list[str]:
    """One --probe-binds capture: the user's --data as for the main capture, then
    the probe's own value, which wins because the CLI keeps the last --data for
    a path (measured). Without the user's values every probe differed from the
    main capture, and an unbound property read as driving the picture."""
    return [*base_args, *data_args, f"--data={prop}={value}", at]


def contact_sheet(images: list[Path], out: Path, height: int = 360) -> Path | None:
    images = [p for p in images if p.is_file()]
    if not images:
        return None
    inputs = []
    for p in images:
        inputs += ["-i", str(p)]
    chain = "".join(f"[{i}:v]scale=-2:{height},format=rgb24[s{i}];" for i in range(len(images)))
    if len(images) == 1:
        graph = chain + "[s0]copy[o]"
    else:
        graph = chain + "".join(f"[s{i}]" for i in range(len(images))) + f"hstack=inputs={len(images)}[o]"
    cmd = [L.ffmpeg_bin(), "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[o]",
           "-frames:v", "1", str(out)]
    return out if subprocess.run(cmd, capture_output=True).returncode == 0 else None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def run(args) -> dict:
    project = Path(args.project).resolve()
    if not L.is_project(project):
        raise L.RiveError(f"{project} is not a Rive project (no rive.yaml)")
    out_dir = Path(args.out).resolve() if args.out else project / "build" / "check"
    out_dir.mkdir(parents=True, exist_ok=True)
    version = L.cli_version()
    report: dict = {"project": str(project), "rive_cli": version, "errors": [], "warnings": [],
                    "images": {}, "lint": []}
    note = L.version_note(version)
    if note:
        report["warnings"].append(note)

    # 1 build
    verify = L.run_rive([str(project), "--verify", "--format=json"], timeout=300)
    envelope = L.parse_envelope(verify.stdout) or {}
    report["verify"] = {"exit": verify.returncode, "errors": envelope.get("errors", []),
                        "warnings": envelope.get("warnings", [])}
    if verify.returncode != 0:
        report["errors"].append("build failed: " + json.dumps(envelope.get("errors") or verify.stderr[-500:])[:1200])
        return report
    run_tests(project, report)

    # 2 wiring
    inspect = L.inspect_project(project)
    probs = L.problems(inspect)
    report["problems"] = probs
    for p in probs:
        target = report["errors"] if p.get("severity") == "error" else report["warnings"]
        target.append(f"inspect {p.get('kind')} line {p.get('line')}: {p.get('message')}")
    boards = L.artboards(inspect)
    report["artboards"] = [{"name": b.name, "size": list(b.size), "state_machine": b.state_machine,
                            "view_model": b.view_model, "properties": b.view_model_props} for b in boards]
    report["objects"] = sum(1 for _ in walk(inspect.get("artboards") or []))
    board = L.pick_artboard(inspect, args.artboard)

    # 3 lint
    lint = lint_markup(project) + lint_tree(inspect)
    report["lint"] = lint
    for f in lint:
        target = report["errors"] if f["severity"] == "error" else report["warnings"]
        target.append(f"lint {f['kind']}: {f['message']}")

    # 4 pixels (a snapshot, so parallel checks never write into the project)
    snap = L.snapshot_project(project)
    try:
        base_args = [f"--artboard={board.name}"] if args.artboard else []
        data_args = [f"--data={d}" for d in args.data or []]
        at = L.advance_arg(max(args.at, L.FIRST_FRAME_EPSILON))
        main_png = out_dir / "at.png"
        ok, err = capture(snap, main_png, base_args + data_args + [at])
        if not ok:
            report["errors"].append(f"capture failed: {err}")
            return report
        report["images"]["at"] = str(main_png)
        blank = L.blank_reason(main_png)
        if blank:
            report["errors"].append(f"the capture at {args.at}s is empty: {blank}")

        if args.sizes:
            for size in args.sizes.split(","):
                size = size.strip()
                png = out_dir / f"size_{size}.png"
                ok, err = capture(snap, png, base_args + data_args + [f"--viewport={size}", at])
                if ok:
                    report["images"][f"size {size}"] = str(png)
                else:
                    report["warnings"].append(f"capture at {size} failed: {err}")

        if args.interaction:
            gesture = args.interaction
            m = re.fullmatch(r"click@(-?[\d.]+),(-?[\d.]+)", gesture)
            if not m:
                raise L.RiveError("--interaction takes click@X,Y in artboard coordinates")
            shots = interaction_shots(args.at, m.group(1), m.group(2), args.settle)
            paths = {}
            for name, extra in shots.items():
                png = out_dir / f"interaction_{name}.png"
                ok, err = capture(snap, png, base_args + data_args + extra)
                if not ok:
                    report["errors"].append(f"interaction capture {name} failed: {err}")
                    break
                paths[name] = png
                report["images"][f"interaction {name}"] = str(png)
            if len(paths) == 4:
                changed = not same_picture(paths["rest_on"], paths["on"])
                returns = same_picture(paths["rest_off"], paths["off"])
                report["interaction"] = {"responds": changed, "returns_to_rest": returns}
                if not changed:
                    report["errors"].append("the click changed nothing (identical to a rest capture at the same "
                                            "time): the control is dead (no default state machine? no listener "
                                            "on that spot?)")
                elif not returns:
                    report["warnings"].append("a second click did not bring it back to the rest picture: a "
                                              "toggle that only works one way, or a control that is not a toggle")

        if args.probe_binds:
            probes = {}
            current = current_values(snap, base_args + data_args + [at])
            for prop, kind in sorted(board.view_model_props.items()):
                value = probe_value(kind, current.get(prop), prop)
                if value is None:
                    continue
                png = out_dir / f"probe_{prop}.png"
                ok, err = capture(snap, png, probe_args(base_args, data_args, prop, value, at))
                if not ok:
                    probes[prop] = f"error: {err}"
                    continue
                probes[prop] = (f"drives the picture ({prop}={value})" if not same_picture(png, main_png) else
                                f"no visible effect at {args.at}s with {prop}={value} (inert bind, off-screen, "
                                "or it only matters at another time)")
            report["binds"] = probes
    finally:
        L.remove_tree(snap.parent)

    sheet = contact_sheet([Path(p) for p in report["images"].values()][:6], out_dir / "sheet.png")
    if sheet:
        report["images"]["sheet"] = str(sheet)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--artboard")
    ap.add_argument("--at", type=float, default=1.0, help="scene time of the main capture (seconds)")
    ap.add_argument("--data", action="append", help="PATH=VALUE applied to every capture")
    ap.add_argument("--sizes", help="comma list of WIDTHxHEIGHT viewports, the responsive check")
    ap.add_argument("--interaction", help="click@X,Y: capture rest, on, off and compare")
    ap.add_argument("--settle", type=float, default=0.5, help="seconds to let a transition land")
    ap.add_argument("--probe-binds", action="store_true", help="set each view model property and look")
    ap.add_argument("--out", help="folder for the pictures (default <project>/build/check)")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)
    try:
        report = run(args)
    except L.RiveError as exc:
        print(f"rive_check: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "FAIL" if report["errors"] else ("PASS with warnings" if report["warnings"] else "PASS")
        print(f"{status}: {report['project']} (Rive CLI {report['rive_cli']}, {report.get('objects', 0)} objects)")
        for e in report["errors"]:
            print(f"  error: {e}")
        for w in report["warnings"]:
            print(f"  warning: {w}")
        for name, path in report["images"].items():
            print(f"  image {name}: {path}")
        if "tests" in report:
            t = report["tests"]
            print(f"  tests: {t['passed']} passed, {t['failed']} failed")
        if "interaction" in report:
            print(f"  interaction: {report['interaction']}")
        if "binds" in report:
            for prop, verdict in report["binds"].items():
                print(f"  bind {prop}: {verdict}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
