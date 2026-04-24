#!/usr/bin/env python3
"""Screenplay skill: create, version, export, and analyze Fountain screenplays."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

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


def load_metadata(project_dir: Path) -> dict:
    meta_path = project_dir / "metadata.json"
    if USE_SAFE_JSON:
        return _safe_load(meta_path, default={})
    if not meta_path.exists():
        return {}
    with open(meta_path) as f:
        return json.load(f)


def save_metadata(project_dir: Path, meta: dict):
    meta_path = project_dir / "metadata.json"
    if USE_SAFE_JSON:
        _safe_save(meta_path, meta)
    else:
        with open(meta_path, "w") as f:
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
        slug = title.lower().replace(" ", "-").replace("'", "").replace('"', "")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        if not slug:
            slug = "untitled"
        project_dir = Path.cwd() / slug

    if project_dir.exists() and (project_dir / "metadata.json").exists():
        print(f"Error: Project already exists at {project_dir}")
        sys.exit(1)

    # Create structure
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "versions").mkdir(exist_ok=True)
    (project_dir / "exports").mkdir(exist_ok=True)

    # Write starter draft
    date_str = datetime.now().strftime("%Y-%m-%d")
    draft_content = STARTER_TEMPLATE.format(
        title=title,
        author=author,
        date=date_str,
        script_type=SCRIPT_TYPES[script_type]["label"],
    )
    (project_dir / "draft.fountain").write_text(draft_content)

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
    print(f"  Draft: {project_dir / 'draft.fountain'}")


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
    exports_dir = project_dir / "exports"
    exports_dir.mkdir(exist_ok=True)

    raw_slug = meta.get("title", "script").lower().replace(" ", "_")
    slug = "".join(c for c in raw_slug if c.isalnum() or c in "_-") or "script"
    output_filename = f"{slug}_{version_label}.{fmt}"
    output_path = exports_dir / output_filename

    if engine == "afterwriting" and fmt == "pdf":
        cmd = ["afterwriting", "--source", str(source_path), "--pdf", str(output_path), "--overwrite"]

        if args.scene_numbers:
            cmd.extend(["--setting", f"scenes_numbers={args.scene_numbers}"])
        if args.watermark:
            cmd.extend(["--setting", f"print_watermark={args.watermark}"])
        if args.no_title_page:
            cmd.extend(["--setting", "print_title_page=false"])
        if args.a4:
            cmd.extend(["--setting", "print_profile=a4"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            print("Error: afterwriting timed out after 120s")
            sys.exit(1)
        if result.returncode != 0:
            print(f"Error from afterwriting: {result.stderr}")
            sys.exit(1)
    elif engine == "screenplain" or fmt in ("html", "fdx"):
        cmd = ["screenplain", str(source_path), str(output_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            print("Error: screenplain timed out after 60s")
            sys.exit(1)
        if result.returncode != 0:
            print(f"Error from screenplain: {result.stderr}")
            sys.exit(1)
    else:
        print(f"Error: Unsupported format/engine combination: {fmt}/{engine}")
        sys.exit(1)

    print(f"Exported: {output_path}")
    print(f"  Format: {fmt.upper()}")
    print(f"  Engine: {engine}")
    print(f"  Size: {output_path.stat().st_size:,} bytes")


def cmd_analyze(args):
    """Analyze the script using afterwriting stats."""
    project_dir = Path(args.project_dir)
    meta = load_metadata(project_dir)
    if not meta:
        print(f"Error: No screenplay project found at {project_dir}")
        sys.exit(1)

    source_path = project_dir / "draft.fountain"
    if not source_path.exists():
        print("Error: No draft.fountain found")
        sys.exit(1)

    # afterwriting generates a PDF and we can count pages
    # For detailed stats, we parse the fountain file directly
    content = source_path.read_text()
    lines = content.split("\n")

    # Basic stats from parsing
    scene_count = 0
    dialogue_blocks = 0
    characters = set()
    locations = set()
    current_char = None
    in_title_page = True
    seen_title_key = False

    for line in lines:
        stripped = line.strip()

        # Skip title page (key:value pairs at the start, ended by first blank line AFTER keys)
        if in_title_page:
            if ":" in stripped and stripped.split(":")[0].strip() in (
                "Title", "Credit", "Author", "Authors", "Source",
                "Draft date", "Contact", "Type"
            ):
                seen_title_key = True
                continue
            if stripped == "" and seen_title_key:
                in_title_page = False
                continue
            if stripped == "" and not seen_title_key:
                continue
            in_title_page = False

        # Scene headings
        if stripped.upper().startswith(("INT.", "EXT.", "INT/EXT.", "INT./EXT.", "I/E.", "EST.")) or stripped.startswith("."):
            scene_count += 1
            # Extract full location (everything except time of day)
            parts = stripped.split(" - ")
            if len(parts) >= 2:
                # Last part is usually time of day (DAY, NIGHT, etc.)
                time_words = {"DAY", "NIGHT", "DAWN", "DUSK", "CONTINUOUS", "LATER", "MOMENTS LATER", "SAME"}
                if parts[-1].strip().upper() in time_words:
                    loc = " - ".join(parts[:-1])
                else:
                    loc = stripped
            else:
                loc = stripped
            loc = loc.lstrip(".")
            locations.add(loc.strip())
            current_char = None
            continue

        # Character (uppercase line, not a scene heading, not empty)
        if stripped and stripped == stripped.upper() and stripped[0].isalpha() and not stripped.endswith(":"):
            # Filter out transitions and Fountain directives
            if not stripped.endswith("TO:") and not stripped.startswith(">"):
                # Filter out FADE OUT, FADE IN, THE END, and section headers
                base = stripped.split("(")[0].strip().rstrip(".")
                if base not in ("FADE OUT", "FADE IN", "THE END", "BLACKOUT", "CUT TO"):
                    # Could be a character
                    char_name = stripped.split("(")[0].strip()
                    if char_name and len(char_name) < 40:
                        characters.add(char_name)
                        current_char = char_name
                        dialogue_blocks += 1
                    continue

        if stripped == "":
            current_char = None

    # Estimate page count: ~56 lines per page including blank lines for spacing
    # Count all lines after title page (blank lines between elements matter for pacing)
    body_lines = 0
    past_title = False
    for l in lines:
        s = l.strip()
        if not past_title:
            if s and ":" in s and s.split(":")[0].strip() in (
                "Title", "Credit", "Author", "Authors", "Source",
                "Draft date", "Contact", "Type"
            ):
                continue
            if s == "":
                continue
            past_title = True
        body_lines += 1
    estimated_pages = max(1, body_lines // 56)

    # Estimated runtime (1 page ~ 1 minute)
    est_minutes = estimated_pages

    print(f"Script Analysis: {meta.get('title', 'Unknown')}")
    print(f"  Type: {SCRIPT_TYPES.get(meta.get('type', 'short'), {}).get('label', 'Unknown')}")
    print(f"  Estimated pages: ~{estimated_pages}")
    print(f"  Estimated runtime: ~{est_minutes} minutes")
    print(f"  Scenes: {scene_count}")
    print(f"  Dialogue blocks: {dialogue_blocks}")
    print(f"  Characters: {len(characters)}")
    if characters:
        for c in sorted(characters):
            print(f"    - {c}")
    print(f"  Locations: {len(locations)}")
    if locations:
        for loc in sorted(locations):
            print(f"    - {loc}")

    # Also try afterwriting PDF for accurate page count
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        result = subprocess.run(
            ["afterwriting", "--source", str(source_path), "--pdf", tmp_path, "--overwrite"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(tmp_path):
            try:
                import PyPDF2
                with open(tmp_path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    real_pages = len(reader.pages)
                print(f"  Actual PDF pages: {real_pages} (via afterwriting)")
            except ImportError:
                file_size = os.path.getsize(tmp_path)
                print(f"  PDF generated: {file_size:,} bytes")
    except Exception:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


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
        current_content = draft_path.read_text()
        restore_content = version_path.read_text()
        if current_content == restore_content:
            print(f"Draft is already at v{version_num}. No changes needed.")
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
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"No differences between {v1_label} and {v2_label}")
    else:
        print(result.stdout)


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
    p_export.add_argument("--version", "-v", type=int, help="Export specific version (default: current draft)")
    p_export.add_argument("--scene-numbers", choices=["none", "left", "right", "both"],
                          help="Scene numbers (afterwriting only)")
    p_export.add_argument("--watermark", help="Watermark text (afterwriting only)")
    p_export.add_argument("--no-title-page", action="store_true", help="Skip title page (afterwriting only)")
    p_export.add_argument("--a4", action="store_true", help="Use A4 paper (afterwriting only)")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Analyze script stats")
    p_analyze.add_argument("project_dir", help="Path to screenplay project")

    # versions
    p_versions = subparsers.add_parser("versions", help="List saved versions")
    p_versions.add_argument("project_dir", help="Path to screenplay project")

    # restore
    p_restore = subparsers.add_parser("restore", help="Restore a previous version")
    p_restore.add_argument("project_dir", help="Path to screenplay project")
    p_restore.add_argument("--version", "-v", type=int, required=True, help="Version number to restore")

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare two versions")
    p_diff.add_argument("project_dir", help="Path to screenplay project")
    p_diff.add_argument("--v1", type=int, required=True, help="First version (0 = current draft)")
    p_diff.add_argument("--v2", type=int, required=True, help="Second version (0 = current draft)")

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
