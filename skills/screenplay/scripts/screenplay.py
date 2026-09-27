#!/usr/bin/env python3
"""Screenplay skill: create, version, export, and analyze Fountain screenplays."""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fountain_export  # noqa: E402

# Use project's safe_json for atomic writes if available
UTILS_DIR = Path(__file__).resolve().parent.parent.parent / "utils"
sys.path.insert(0, str(UTILS_DIR))
try:
    from safe_json import load_json as _safe_load, save_json as _safe_save
    USE_SAFE_JSON = True
except ImportError:
    USE_SAFE_JSON = False

SCRIPT_TYPES = {
    "short": {"label": "Short Film", "target_pages": "5-15"},
    "feature": {"label": "Feature Film", "target_pages": "90-120"},
    "episode": {"label": "TV Episode", "target_pages": "22-60"},
    "sketch": {"label": "Sketch / Skit", "target_pages": "1-5"},
}

STARTER_TEMPLATE = """Title: {title}
Credit: Written by
Author: {author}
Draft date: {date}
Type: {script_type}

# Act One

INT. LOCATION - DAY

Action description goes here.

CHARACTER
Dialogue goes here.

"""


def slugify(text: str, sep: str = "-") -> str:
    """Letters and digits (any script) joined by sep: "Love/Hate: A Test" gives love-hate-a-test."""
    text = re.sub(r"['\"\u2018\u2019]", "", text.lower())
    return re.sub(r"[\W_]+", sep, text).strip(sep) or "untitled"


def version_arg(text: str) -> int:
    """A version number as typed: 3 or v3."""
    match = re.fullmatch(r"[vV]?(\d+)", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"not a version number: {text!r} (use 3 or v3)")
    return int(match.group(1))


def fail(message: str):
    print(f"Error: {message}")
    sys.exit(1)


def load_metadata(project_dir: Path) -> dict:
    meta_path = project_dir / "metadata.json"
    if USE_SAFE_JSON:
        return _safe_load(meta_path, default={})
    if not meta_path.exists():
        return {}
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def save_metadata(project_dir: Path, meta: dict):
    meta_path = project_dir / "metadata.json"
    if USE_SAFE_JSON:
        _safe_save(meta_path, meta)
    else:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)


def cmd_create(args):
    """Create a new screenplay project with versioned directory structure."""
    title = args.title
    author = args.author or "Unknown"
    script_type = args.type or "short"

    if script_type not in SCRIPT_TYPES:
        print(f"Error: Unknown script type '{script_type}'. Options: {', '.join(SCRIPT_TYPES)}")
        sys.exit(1)

    # Determine project directory
    if args.dir:
        project_dir = Path(args.dir)
    else:
        project_dir = Path.cwd() / slugify(title)

    if project_dir.exists() and (project_dir / "metadata.json").exists():
        print(f"Error: Project already exists at {project_dir}")
        sys.exit(1)

    # Create structure
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "versions").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    # Write starter draft, unless the folder already holds one: that is the
    # writer's script, adopted as it is
    date_str = datetime.now().strftime("%Y-%m-%d")
    draft_path = project_dir / "draft.fountain"
    adopted = draft_path.exists()
    if not adopted:
        draft_content = STARTER_TEMPLATE.format(
            title=title,
            author=author,
            date=date_str,
            script_type=SCRIPT_TYPES[script_type]["label"],
        )
        draft_path.write_text(draft_content, encoding="utf-8")

    # Write metadata
    meta = {
        "title": title,
        "author": author,
        "type": script_type,
        "created": date_str,
        "current_version": 0,
        "versions": [],
    }
    save_metadata(project_dir, meta)

    type_info = SCRIPT_TYPES[script_type]
    print(f"Created screenplay project: {title}")
    print(f"  Type: {type_info['label']} (target: {type_info['target_pages']} pages)")
    print(f"  Location: {project_dir}")
    print(f"  Draft: {draft_path}" + (" (existing script kept)" if adopted else ""))


def cmd_save(args):
    """Save current draft as a new version."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    draft_path = project_dir / "draft.fountain"
    if not draft_path.exists():
        print("Error: No draft.fountain found")
        sys.exit(1)

    # Increment version
    new_version = meta["current_version"] + 1
    date_str = datetime.now().strftime("%Y-%m-%d")
    version_filename = f"v{new_version}_{date_str}.fountain"
    version_path = project_dir / "versions" / version_filename

    # Copy draft to versions
    shutil.copy2(draft_path, version_path)

    # Update metadata
    note = args.note or ""
    meta["current_version"] = new_version
    meta["versions"].append({
        "version": new_version,
        "date": datetime.now().isoformat(),
        "filename": version_filename,
        "note": note,
    })
    save_metadata(project_dir, meta)

    print(f"Saved version v{new_version}: {version_filename}")
    if note:
        print(f"  Note: {note}")
    print(f"  Total versions: {new_version}")


def cmd_export(args):
    """Export the current draft or a specific version to PDF/HTML/FDX."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    # Determine source file
    if args.version:
        version_num = args.version
        version_entry = None
        for v in meta["versions"]:
            if v["version"] == version_num:
                version_entry = v
                break
        if not version_entry:
            print(f"Error: Version v{version_num} not found")
            sys.exit(1)
        source_path = project_dir / "versions" / version_entry["filename"]
        version_label = f"v{version_num}"
    else:
        source_path = project_dir / "draft.fountain"
        version_label = f"v{meta['current_version'] + 1}_draft"

    if not source_path.exists():
        print(f"Error: Source file not found: {source_path}")
        sys.exit(1)

    fmt = args.format or "pdf"
    engine = args.engine or "screenplain"
    try:
        text = fountain_export.read_text(source_path)
    except RuntimeError as exc:
        fail(str(exc))
    needs_afterwriting = [flag for flag, value in (
        ("--scene-numbers", args.scene_numbers),
        ("--watermark", args.watermark),
        ("--no-title-page", args.no_title_page),
    ) if value]
    if engine == "afterwriting" and fmt != "pdf":
        fail("afterwriting writes PDF only; export html and fdx with the default engine")
    if needs_afterwriting and engine != "afterwriting":
        verb = "needs" if len(needs_afterwriting) == 1 else "need"
        fail(f"{', '.join(needs_afterwriting)} {verb} --engine afterwriting")
    if engine == "afterwriting" and fountain_export.needs_unicode_font(text):
        fail("this script has letters (Greek, for one) that afterwriting's fonts do not have; "
             "they would print as blank space. Export with the default engine (add --a4 for A4); "
             "scene numbers and watermarks are not available there")

    exports_dir = project_dir / "exports"
    exports_dir.mkdir(exist_ok=True)

    slug = slugify(meta.get("title", "script"), "_")
    output_filename = f"{slug}_{version_label}.{fmt}"
    output_path = exports_dir / output_filename

    done = {}
    if engine == "afterwriting":
        # afterwriting exits 0 when it cannot read the script and writes nothing,
        # so it writes to a fresh name first: an earlier export at this path can
        # neither pass for a fresh one nor be lost to a failed run
        part = output_path.with_name(f".{output_path.stem}.part.pdf")
        part.unlink(missing_ok=True)
        cmd = ["afterwriting", "--source", str(source_path), "--pdf", str(part), "--overwrite"]

        if args.scene_numbers:
            cmd.extend(["--setting", f"scenes_numbers={args.scene_numbers}"])
        if args.watermark:
            cmd.extend(["--setting", f"print_watermark={args.watermark}"])
        if args.no_title_page:
            cmd.extend(["--setting", "print_title_page=false"])
        if args.a4:
            cmd.extend(["--setting", "print_profile=a4"])

        try:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except FileNotFoundError:
                fail("afterwriting is not installed (npm install -g afterwriting)")
            except subprocess.TimeoutExpired:
                fail("afterwriting ran past 300 s")
            if result.returncode != 0 or not part.exists():
                fail(f"afterwriting wrote no PDF:\n{(result.stdout + result.stderr).strip()[-1500:]}")
            os.replace(part, output_path)
        finally:
            part.unlink(missing_ok=True)
    else:
        try:
            done = fountain_export.export(source_path, output_path, fmt, a4=args.a4)
        except ImportError as exc:
            fail(f"{exc}. The screenplay skill needs screenplain, reportlab and fonttools in this Python: pip install screenplain reportlab fonttools")
        except RuntimeError as exc:
            fail(str(exc))

    print(f"Exported: {output_path}")
    print(f"  Format: {fmt.upper()}")
    print(f"  Engine: {engine}")
    if done.get("pages") is not None:
        print(f"  Pages: {done['pages']} (title page not counted)")
    if done.get("family"):
        print(f"  Font: {done['family']} (the script has letters the standard Courier lacks)")
    if done.get("fallback"):
        print(f"  Set in a fallback font: {''.join(sorted(done['fallback']))}")
    if done.get("missing"):
        print(f"  Warning: no installed font has {''.join(done['missing'])}; they print as boxes")
    print(f"  Size: {output_path.stat().st_size:,} bytes")


def cmd_analyze(args):
    """Scene, character, dialogue and page counts, read with screenplain's Fountain parser."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    source_path = project_dir / "draft.fountain"
    if not source_path.exists():
        print("Error: No draft.fountain found")
        sys.exit(1)

    try:
        text = fountain_export.read_text(source_path)
        play = fountain_export.parse_text(text)
    except RuntimeError as exc:
        fail(str(exc))
    except ImportError as exc:
        fail(f"{exc}. The screenplay skill needs screenplain, reportlab and fonttools in this Python: pip install screenplain reportlab fonttools")
    counts = fountain_export.stats(play)

    # The page count is the default PDF export's own layout (US Letter, 55
    # lines; A4 gives the same count), rendered in memory
    pages, page_note = None, ""
    try:
        fallback = {}
        if fountain_export.needs_unicode_font(text):
            _, fallback, _ = fountain_export.use_unicode_courier(text)
        pages = fountain_export.write_pdf(play, io.BytesIO(), fallback=fallback)
    except RuntimeError as exc:
        page_note = str(exc)

    speeches, words = counts["speeches"], counts["words"]
    print(f"Script Analysis: {meta.get('title', 'Unknown')}")
    print(f"  Type: {SCRIPT_TYPES.get(meta.get('type', 'short'), {}).get('label', 'Unknown')}")
    if pages is not None:
        print(f"  Pages: {pages} (as the PDF export lays them out, title page not counted)")
        print(f"  Runtime: about {pages} minute{'s' if pages != 1 else ''} (a page a minute)")
    else:
        print(f"  Pages: not counted ({page_note})")
    print(f"  Scenes: {counts['scenes']}")
    print(f"  Dialogue blocks: {sum(speeches.values())}")
    print(f"  Characters: {len(speeches)} (speeches, words spoken)")
    for name, n in sorted(speeches.items(), key=lambda item: (-item[1], item[0])):
        print(f"    - {name}: {n}, {words[name]} words")
    print(f"  Locations: {len(counts['locations'])}")
    for loc in sorted(counts["locations"]):
        print(f"    - {loc}")


def cmd_versions(args):
    """List all saved versions."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    if not meta["versions"]:
        print("No versions saved yet. Use 'save' to create the first version.")
        return

    print(f"Versions of '{meta['title']}':")
    for v in meta["versions"]:
        version_path = project_dir / "versions" / v["filename"]
        size = version_path.stat().st_size if version_path.exists() else 0
        note_str = f'  "{v["note"]}"' if v.get("note") else ""
        print(f"  v{v['version']}  {v['date'][:10]}  {size:>6,} bytes{note_str}")

    print(f"\nCurrent version: v{meta['current_version']}")


def cmd_restore(args):
    """Restore a previous version as the current draft."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    version_num = args.version
    version_entry = None
    for v in meta["versions"]:
        if v["version"] == version_num:
            version_entry = v
            break

    if not version_entry:
        print(f"Error: Version v{version_num} not found")
        sys.exit(1)

    version_path = project_dir / "versions" / version_entry["filename"]
    if not version_path.exists():
        print(f"Error: Version file missing: {version_path}")
        sys.exit(1)

    draft_path = project_dir / "draft.fountain"

    # Auto-save current draft before overwriting
    if draft_path.exists():
        current_content = draft_path.read_text(encoding="utf-8")
        restore_content = version_path.read_text(encoding="utf-8")
        if current_content == restore_content:
            print(f"Draft is already at v{version_num}. No changes needed.")
            return

        latest = meta["versions"][-1] if meta["versions"] else None
        latest_path = project_dir / "versions" / latest["filename"] if latest else None
        if latest_path and latest_path.exists() and latest_path.read_text(encoding="utf-8") == current_content:
            print(f"Current draft is already saved as v{latest['version']}")
            shutil.copy2(version_path, draft_path)
            print(f"Restored v{version_num} as current draft")
            return

        # Save current as a version first
        backup_version = meta["current_version"] + 1
        date_str = datetime.now().strftime("%Y-%m-%d")
        backup_filename = f"v{backup_version}_{date_str}.fountain"
        backup_path = project_dir / "versions" / backup_filename
        shutil.copy2(draft_path, backup_path)
        meta["current_version"] = backup_version
        meta["versions"].append({
            "version": backup_version,
            "date": datetime.now().isoformat(),
            "filename": backup_filename,
            "note": f"Auto-saved before restoring v{version_num}",
        })
        print(f"Auto-saved current draft as v{backup_version}")

    # Restore
    shutil.copy2(version_path, draft_path)
    save_metadata(project_dir, meta)
    print(f"Restored v{version_num} as current draft")


def cmd_diff(args):
    """Show differences between two versions."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    def find_version_path(vnum):
        if vnum == 0:
            return project_dir / "draft.fountain"
        for v in meta["versions"]:
            if v["version"] == vnum:
                return project_dir / "versions" / v["filename"]
        return None

    v1_num = args.v1
    v2_num = args.v2
    v1_path = find_version_path(v1_num)
    v2_path = find_version_path(v2_num)

    if not v1_path or not v1_path.exists():
        print(f"Error: Version v{v1_num} not found")
        sys.exit(1)
    if not v2_path or not v2_path.exists():
        print(f"Error: Version v{v2_num} not found")
        sys.exit(1)

    v1_label = "draft" if v1_num == 0 else f"v{v1_num}"
    v2_label = "draft" if v2_num == 0 else f"v{v2_num}"

    result = subprocess.run(
        ["diff", "-u", "--label", v1_label, "--label", v2_label,
         str(v1_path), str(v2_path)],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode == 0:
        print(f"No differences between {v1_label} and {v2_label}")
    elif result.returncode == 1:
        print(result.stdout)
    else:
        fail(f"diff failed: {result.stderr.strip()}")


def main():
    parser = argparse.ArgumentParser(description="Screenplay skill: Fountain screenwriting toolkit")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # create
    p_create = subparsers.add_parser("create", help="Create a new screenplay project")
    p_create.add_argument("title", help="Script title")
    p_create.add_argument("--author", "-a", help="Author name")
    p_create.add_argument("--type", "-t", choices=SCRIPT_TYPES.keys(), default="short",
                          help="Script type (default: short)")
    p_create.add_argument("--dir", "-d", help="Project directory (default: auto from title)")

    # save
    p_save = subparsers.add_parser("save", help="Save current draft as a new version")
    p_save.add_argument("project_dir", help="Path to screenplay project")
    p_save.add_argument("--note", "-n", help="Version note")

    # export
    p_export = subparsers.add_parser("export", help="Export to PDF/HTML/FDX")
    p_export.add_argument("project_dir", help="Path to screenplay project")
    p_export.add_argument("--format", "-f", choices=["pdf", "html", "fdx"], default="pdf",
                          help="Output format (default: pdf)")
    p_export.add_argument("--engine", "-e", choices=["screenplain", "afterwriting"],
                          default="screenplain", help="PDF engine (default: screenplain)")
    p_export.add_argument("--version", "-v", type=version_arg,
                          help="Export a saved version, 3 or v3 (default: current draft)")
    p_export.add_argument("--scene-numbers", choices=["none", "left", "right", "both"],
                          help="Scene numbers (afterwriting only)")
    p_export.add_argument("--watermark", help="Watermark text (afterwriting only)")
    p_export.add_argument("--no-title-page", action="store_true", help="Skip title page (afterwriting only)")
    p_export.add_argument("--a4", action="store_true", help="Use A4 paper (default: US Letter)")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Pages, scenes, characters, dialogue, locations")
    p_analyze.add_argument("project_dir", help="Path to screenplay project")

    # versions
    p_versions = subparsers.add_parser("versions", help="List saved versions")
    p_versions.add_argument("project_dir", help="Path to screenplay project")

    # restore
    p_restore = subparsers.add_parser("restore", help="Restore a previous version")
    p_restore.add_argument("project_dir", help="Path to screenplay project")
    p_restore.add_argument("--version", "-v", type=version_arg, required=True,
                           help="Version to restore, 3 or v3")

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare two versions")
    p_diff.add_argument("project_dir", help="Path to screenplay project")
    p_diff.add_argument("--v1", type=version_arg, required=True, help="First version, 3 or v3 (0 = current draft)")
    p_diff.add_argument("--v2", type=version_arg, required=True, help="Second version, 3 or v3 (0 = current draft)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "create": cmd_create,
        "save": cmd_save,
        "export": cmd_export,
        "analyze": cmd_analyze,
        "versions": cmd_versions,
        "restore": cmd_restore,
        "diff": cmd_diff,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
