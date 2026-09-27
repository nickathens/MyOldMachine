# Screenplay

Write, format, version, and export screenplays using Fountain markup.

## Tools

- **screenplain** (Python): Fountain to PDF/HTML/FDX, the default engine. It runs inside `screenplay.py`, which also uses its Fountain parser for `analyze`. A script with letters the PDF standard Courier lacks (Greek) is set in Nimbus Mono PS, a Courier clone that has them; a stray word in yet another script (a Chinese word in an English script) is set in an installed font that has it.
- **afterwriting** (Node.js CLI): Fountain to PDF with scene numbers, a watermark, or no title page. Its fonts have no Greek letters (they print as blank space), so `screenplay.py` refuses it for Greek text.

## Core Workflow

1. Write the script in **Fountain format** (plain text with simple conventions)
2. Save as `.fountain` file in the project's versioned directory
3. Export to PDF (or HTML/FDX) on demand
4. Version management: save snapshots, compare, restore

## Commands

```bash
# Create a new screenplay project with versioned directory structure
python $SKILL_DIR/scripts/screenplay.py create "Script Title" --author "Author Name" --type short

# Save current version (auto-increments: v1, v2, v3...)
python $SKILL_DIR/scripts/screenplay.py save <project_dir> --note "Added climax scene"

# Export to PDF (screenplain: clean formatting; add --a4 for A4)
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format pdf
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format pdf --a4

# Export to PDF (afterwriting: with scene numbers, watermark, custom config)
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format pdf --engine afterwriting --scene-numbers both

# Export to HTML or FDX
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format html
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format fdx

# Analyze the draft: pages as the PDF export lays them out, runtime (a page a minute),
# scenes, each character's speeches and words, locations
python $SKILL_DIR/scripts/screenplay.py analyze <project_dir>

# List all versions
python $SKILL_DIR/scripts/screenplay.py versions <project_dir>

# Restore a previous version (3 or v3; the current draft is saved first unless it already is)
python $SKILL_DIR/scripts/screenplay.py restore <project_dir> --version v3

# Diff two versions (0 is the current draft)
python $SKILL_DIR/scripts/screenplay.py diff <project_dir> --v1 v2 --v2 v5
```

Pass `--dir` to `create` with the project's real home (for example `~/projects/<slug>`); without it the folder lands in the current directory. If the folder already holds a `draft.fountain`, `create` keeps it and builds the project around it.

Exports are named from the title: `<title>_v3.pdf` for saved version 3, `<title>_v4_draft.pdf` for the current draft after three saves. Any failure prints `Error:` and exits 1; afterwriting exits 0 even when it writes nothing, so the script checks for the file itself.

## Fountain Format Quick Reference

```fountain
Title: My Screenplay
Credit: Written by
Author: Jane Doe
Draft date: 2026-04-08

INT. COFFEE SHOP - DAY

A quiet corner table. DIMITRIS (30s, unshaven, hoodie) stares at a laptop screen showing nothing but a blinking cursor.

DIMITRIS
I used to know how to write code.

COUNSELOR (O.S.)
When was the last time you opened a terminal?

DIMITRIS
(defensive)
I have AI for that.

> CUT TO:

EXT. PARKING LOT - NIGHT

Dimitris walks to his car. Rain hammers the pavement.
```

### Key Syntax Rules

- **Scene headings**: Start with INT, EXT, INT./EXT (or force with leading period: `.BASEMENT`)
- **Characters**: UPPERCASE on their own line, dialogue follows immediately
- **Parentheticals**: In (parentheses) between character and dialogue
- **Transitions**: End with TO: or force with leading > symbol
- **Action**: Any paragraph that doesn't match other elements
- **Emphasis**: *italics*, **bold**, ***bold italics***, _underline_
- **Notes**: [[double brackets for comments, won't appear in output]]
- **Boneyard**: /* ignored content */
- **Dual dialogue**: Add ^ after second character name
- **Page break**: === (three or more equals signs)
- **Sections**: # Act One, ## Scene Group (organizational, not printed)
- **Synopses**: = Brief description (paired with sections, not printed)

## Project Directory Structure

`create` makes `draft.fountain`, `versions/`, `exports/` and `metadata.json`; add `development/` and `notes/` as the work needs them:

```
<project_dir>/
  draft.fountain          # Current working draft
  versions/
    v1_2026-04-08.fountain    # First saved version
    v2_2026-04-08.fountain    # Second version
  exports/
    script_v3.pdf             # Exported PDFs
    script_v3.html            # Exported HTML
  metadata.json               # Title, author, type, version log
```

## Script Types

- `short` : Short film (target: 5-15 pages)
- `feature` : Feature film (target: 90-120 pages)
- `episode` : TV episode (target: 22-60 pages)
- `sketch` : Comedy sketch / skit (target: 1-5 pages)

## Export Options

- `--a4` : A4 paper (default: US Letter), with either engine

These three need `--engine afterwriting` (the script refuses them otherwise) and so are not available for Greek scripts:
- `--scene-numbers` : none, left, right, both
- `--watermark "DRAFT"` : Print watermark on every page
- `--no-title-page` : Skip title page

## Documentation

The `docs/` directory within this skill contains screenwriting craft reference:
- `fountain-spec.md` : Complete Fountain syntax specification
- `structure.md` : Story structures (3-act, Save the Cat, Story Circle, Syd Field)
- `craft.md` : Dialogue, subtext, action lines, show-don't-tell, pacing
- `short-film.md` : Short film specific guidance
- `formatting.md` : Industry standard formatting rules

Export Greek scripts with the default engine, `--a4` for Greek and European submissions. When a
script has letters the PDF standard Courier lacks (Greek above all), the PDF is set in a Courier
style face that has them: Nimbus Mono PS (a Courier clone with Greek; on Debian or Ubuntu
`apt install fonts-urw-base35`), else Courier New, Liberation Mono or FreeMono, with a per letter
fallback font for anything still missing. Before 2026-09-27 accented vowels came out as black boxes,
and afterwriting still prints Greek as blank space, so it is refused for such scripts.

Read these before writing if you're unfamiliar with the form.

## Examples

User: "Start a new screenplay called Vibe Coder Script"
User: "Write the opening scene"
User: "Save this version"
User: "Export to PDF"
User: "Show me version history"
User: "Go back to version 3"
User: "How long is this script?"
User: "Compare version 2 and version 5"
