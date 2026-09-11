#!/usr/bin/env python3
"""Render an After Effects project with nobody at the screen.

This is the one genuinely headless Adobe operation: aerender renders what is
already in a project file. It cannot build or edit a comp -- that needs a live
GUI session. Build the template by hand once, render it from here forever.

Standard library only. macOS only.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

APPS_DIR = Path("/Applications")


def find_aerender(explicit: str | None = None) -> Path:
    """Locate aerender, preferring the newest After Effects when several exist.

    Raises SystemExit with a reason rather than returning None, so a missing
    install fails here with something readable instead of deep inside a render.
    """
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit(f"aerender not found at {p}")
        return p

    on_path = shutil.which("aerender")
    if on_path:
        return Path(on_path)

    # Adobe versions the folder: "Adobe After Effects 2026/aerender".
    hits = sorted(APPS_DIR.glob("Adobe After Effects */aerender"))
    if not hits:
        raise SystemExit(
            "After Effects is not installed (no aerender under /Applications).\n"
            "Install it from the Creative Cloud desktop app, then retry.\n"
            "Check what is here with: python skills/adobe/scripts/adobe_status.py"
        )
    return hits[-1]


def build_command(aerender: Path, args: argparse.Namespace) -> list[str]:
    """Assemble the aerender argv. Pure: no side effects, so it can be tested."""
    cmd = [str(aerender), "-project", str(Path(args.project).resolve())]

    if args.comp:
        cmd += ["-comp", args.comp]
    if args.start is not None:
        cmd += ["-s", str(args.start)]
    if args.end is not None:
        cmd += ["-e", str(args.end)]
    if args.rs_template:
        cmd += ["-RStemplate", args.rs_template]
    if args.om_template:
        cmd += ["-OMtemplate", args.om_template]
    if args.out:
        cmd += ["-output", str(Path(args.out).resolve())]
    if args.threads:
        cmd += ["-mp"]
    # Leave the render settings and output module alone by default: whoever
    # built the template chose the codec, and that is the right place for it.
    return cmd


def validate(args: argparse.Namespace) -> None:
    project = Path(args.project)
    if not project.is_file():
        raise SystemExit(f"No project file at {project}")
    if project.suffix.lower() not in (".aep", ".aepx"):
        raise SystemExit(f"Not an After Effects project: {project.name}")
    if args.out:
        parent = Path(args.out).resolve().parent
        if not parent.is_dir():
            raise SystemExit(f"Output folder does not exist: {parent}")
    if args.start is not None and args.end is not None and args.end < args.start:
        raise SystemExit(f"End frame {args.end} is before start frame {args.start}")
    if args.comp is None and args.out:
        raise SystemExit(
            "--out needs --comp. Without a comp, aerender renders the project's "
            "own render queue and each item writes to its own configured path."
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="the .aep file")
    ap.add_argument("--comp", help="comp to render; omit to run the project's render queue")
    ap.add_argument("--out", help="output file (requires --comp)")
    ap.add_argument("--start", type=int, help="first frame")
    ap.add_argument("--end", type=int, help="last frame")
    ap.add_argument("--rs-template", help="render settings template name")
    ap.add_argument("--om-template", help="output module template name")
    ap.add_argument("--threads", action="store_true", help="enable multi-frame rendering")
    ap.add_argument("--aerender", help="explicit path to aerender")
    ap.add_argument("--dry-run", action="store_true", help="print the command, render nothing")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("After Effects is macOS only.", file=sys.stderr)
        return 2

    validate(args)
    aerender = find_aerender(args.aerender)
    cmd = build_command(aerender, args)

    if args.dry_run:
        print(" ".join(repr(c) if " " in c else c for c in cmd))
        return 0

    print(f"aerender: {aerender}", file=sys.stderr)
    # Stream aerender's own progress rather than buffering it: a long render
    # with no output is indistinguishable from a hung one.
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(
            f"\naerender exited {proc.returncode}. Common causes: a comp name that "
            "does not match exactly (it is case sensitive), a missing footage file, "
            "or an output module template that is not in this install.",
            file=sys.stderr,
        )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
