#!/usr/bin/env python3
"""Start a Rive project from one of the skill's tested templates.

Each template is a complete, working project, checked by the test suite on
every change: it builds, inspects clean, draws, and its data drives the
picture. Copy one, set its view model defaults, and it is yours to edit.

  lower_third  a name and title super on a plate, typed in, transparent (5 s)
  logo_reveal  a logo drawing itself on, filling, settling (3 s); remake it
               from your own SVG with rive_svg.py --reveal
  ui_screen    a phone screen: heading, search field that types, a list of
               results (a repeated component) that scrolls (6 s)
  visualizer   12 spectrum bars, a breathing ring, a glow on hits, driven by
               rive_audio.py curves of a real track
  counter      a number that counts up and a ring that fills to value / goal
  button       a hover / press / toggle button with keyboard, cursor and a
               screen-reader label, for web pages

  rive_new.py list
  rive_new.py lower_third ~/work/supers --set name="Ada Lovelace" --set title="Analyst"
  rive_new.py ui_screen ~/work/app --set heading=Search --set accent=FF3F7EFF
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402

TEMPLATES = L.SKILL_DIR / "templates"
FONTS = TEMPLATES / "_fonts"


def available() -> list[str]:
    return sorted(p.name for p in TEMPLATES.iterdir() if (p / "rive.yaml").is_file())


def describe(name: str) -> str:
    text = (TEMPLATES / name / "scene.rml").read_text(encoding="utf-8")
    match = re.search(r"<!--(.*?)-->", text, flags=re.S)
    if not match:
        return ""
    lines = [ln.strip() for ln in match.group(1).strip().splitlines() if ln.strip()]
    return " ".join(lines[:2])


def set_default(rml: str, prop: str, value: str) -> str:
    """Change the default instance value of a view model property by name."""
    m = re.search(rf'<ViewModelProperty\w+\s[^>]*\bname="{re.escape(prop)}"[^>]*\bid="([^"]+)"', rml) or \
        re.search(rf'<ViewModelProperty\w+\s[^>]*\bid="([^"]+)"[^>]*\bname="{re.escape(prop)}"', rml)
    if not m:
        raise L.RiveError(f"the template has no view model property {prop!r}")
    prop_id = m.group(1)
    pattern = re.compile(rf'(<ViewModelInstance\w+\s[^>]*?propertyValue=")([^"]*)("[^>]*viewModelPropertyId="{re.escape(prop_id)}")')
    escaped = value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    new, count = pattern.subn(lambda mm: mm.group(1) + escaped + mm.group(3), rml, count=1)
    if not count:
        raise L.RiveError(f"no default instance value for {prop!r} to change")
    return new


def create(name: str, dest: Path, settings: list[str]) -> Path:
    src = TEMPLATES / name
    if not (src / "rive.yaml").is_file():
        raise L.RiveError(f"no template {name!r}; templates: {', '.join(available())}")
    if dest.exists() and any(dest.iterdir()):
        raise L.RiveError(f"{dest} exists and is not empty")
    shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("build", "*.render.json"))
    scene = dest / "scene.rml"
    rml = scene.read_text(encoding="utf-8")
    # the templates share one font folder; give the new project its own copy
    for ref in sorted(set(re.findall(r'file="\.\./_fonts/([^"]+)"', rml))):
        shutil.copy2(FONTS / ref, dest / ref)
        rml = rml.replace(f'file="../_fonts/{ref}"', f'file="{ref}"')
        licence = FONTS / (Path(ref).stem.split("-")[0] + "-OFL.txt")
        if licence.is_file():
            shutil.copy2(licence, dest / licence.name)
    for item in settings:
        if "=" not in item:
            raise L.RiveError(f"--set {item!r} must be PROPERTY=VALUE")
        prop, value = item.split("=", 1)
        rml = set_default(rml, prop.strip(), value)
    scene.write_text(rml, encoding="utf-8")
    yaml = dest / "rive.yaml"
    text = yaml.read_text(encoding="utf-8")
    safe = re.sub(r"[^A-Za-z0-9_]", "_", dest.name) or name
    yaml.write_text(re.sub(r"(?m)^name:.*$", f"name: {safe}", text, count=1), encoding="utf-8")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("template", help="a template name, or 'list'")
    ap.add_argument("dest", nargs="?")
    ap.add_argument("--set", action="append", default=[], help="PROPERTY=VALUE default for the view model")
    ap.add_argument("--no-check", action="store_true", help="skip the build check after copying")
    args = ap.parse_args(argv)
    try:
        if args.template == "list":
            for name in available():
                print(f"{name:12} {describe(name)}")
            return 0
        if not args.dest:
            ap.error("give a destination folder")
        dest = create(args.template, Path(args.dest), args.set)
        print(f"created {dest} from {args.template}")
        if not args.no_check and L.find_rive():
            result = L.run_rive([str(dest), "--verify"], timeout=120)
            print("builds clean" if result.returncode == 0 else f"BUILD FAILED: {result.stderr[-400:]}")
            return 0 if result.returncode == 0 else 1
        return 0
    except L.RiveError as exc:
        print(f"rive_new: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
