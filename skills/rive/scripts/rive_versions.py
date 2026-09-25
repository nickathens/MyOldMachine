#!/usr/bin/env python3
"""One Rive template, many versions: a render per row of a table.

Supers, lower thirds, name cards, price tags, social cuts in several
languages: the file is built once with view model properties for what
changes, and each row of a CSV or JSON table becomes its own render. Column
names are view model property paths (title, name, card/price); the column
named by --name-column (default: slug, else name, else the first column)
names the file.

  rive_versions.py supers -t names.csv -o out/ --ext mov --alpha --duration 5 --fps 25
  rive_versions.py cards  -t cards.json -o out/ --ext png --at 1.5
  rive_versions.py promo  -t langs.csv -o out/ --ext mp4 --duration 8 -- --size 1080x1920 --fit cover

Everything after -- goes to rive_render.py unchanged. Writes out/index.json
(row, file, sha256) and out/sheet.png, one still per version side by side.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402
import rive_render as R  # noqa: E402


def load_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, dict) and "rows" in rows:
            rows = rows["rows"]
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise L.RiveError("a JSON table is a list of objects (or {\"rows\": [...]})")
        return [{str(k): v for k, v in r.items()} for r in rows]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(r) for r in csv.DictReader(handle)]


def known_properties(source: str) -> set[str] | None:
    """Top-level view model property names the source exposes, or None if unknown."""
    path = Path(source)
    try:
        if L.is_project(path):
            return set(L.pick_artboard(L.inspect_project(path)).view_model_props)
        if path.suffix.lower() == ".riv":
            import riveweb as W
            with W.WebSession(path) as session:
                info = session.info()
            return {p["name"] for vm in info["viewModels"] for p in vm["properties"]}
    except L.RiveError:
        return None
    return None


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_")
    return slug[:80] or "version"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, passthrough = argv[:cut], argv[cut + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="project folder or .riv")
    ap.add_argument("-t", "--table", required=True, help="CSV or JSON rows")
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--ext", default="png", help="png (a still), mp4, mov, webm or gif")
    ap.add_argument("--name-column", help="column that names each file")
    ap.add_argument("--at", type=float, default=1.0, help="still time for --ext png and the sheet")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--alpha", action="store_true")
    ap.add_argument("--only", help="comma list of names to render (rerun just those)")
    args = ap.parse_args(argv)
    rows = load_rows(Path(args.table))
    if not rows:
        print("rive_versions: the table has no rows", file=sys.stderr)
        return 1
    columns = list(rows[0].keys())
    name_col = args.name_column or next((c for c in ("slug", "name", "file") if c in columns), columns[0])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    known = known_properties(args.source)
    if known is not None:
        unused = [c for c in columns if c not in known and "/" not in c and c not in (name_col, "file")]
        if unused:
            print(f"note: columns with no matching view model property are ignored: {', '.join(unused)}")
    index, stills, seen = [], [], set()
    for i, row in enumerate(rows):
        slug = slugify(row.get(name_col, f"row{i + 1}"))
        base = slug
        n = 2
        while slug in seen:
            slug = f"{base}_{n}"
            n += 1
        seen.add(slug)
        if only and slug not in only and base not in only:
            continue
        def usable(key: str) -> bool:
            return known is None or key in known or "/" in key

        data = [f"{k}={v}" for k, v in row.items()
                if k not in (name_col, "file") and v not in (None, "") and usable(k)]
        if name_col not in ("slug", "file") and known is not None and name_col in known:
            data.append(f"{name_col}={row[name_col]}")
        target = out / f"{slug}.{args.ext}"
        render_argv = [args.source, "-o", str(target), "--fps", str(args.fps), "--report",
                       str(out / f"{slug}.render.json")]
        for d in data:
            render_argv += ["--data", d]
        if args.ext == "png":
            render_argv += ["--at", str(args.at)]
        else:
            if args.duration is None:
                print("rive_versions: a video needs --duration", file=sys.stderr)
                return 1
            render_argv += ["--duration", str(args.duration)]
        if args.alpha:
            render_argv.append("--alpha")
        render_argv += passthrough
        print(f"[{i + 1}/{len(rows)}] {slug}")
        code = R.main(render_argv)
        entry = {"row": row, "file": str(target), "ok": code == 0}
        if code == 0 and target.is_file():
            entry["sha256"] = L.sha256_file(target)
            if args.ext == "png":
                stills.append(target)
            else:
                still = out / f"{slug}.sheet.png"
                subprocess.run([L.ffmpeg_bin(), "-v", "error", "-y", "-ss", str(min(args.at, args.duration or args.at)),
                                "-i", str(target), "-frames:v", "1", str(still)], capture_output=True)
                if still.is_file():
                    stills.append(still)
        index.append(entry)
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    if stills:
        from rive_check import contact_sheet
        sheet = contact_sheet(stills[:8], out / "sheet.png", height=270)
        if sheet:
            print(f"sheet: {sheet}")
    failed = [e for e in index if not e["ok"]]
    print(f"{len(index) - len(failed)} of {len(index)} versions rendered into {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
