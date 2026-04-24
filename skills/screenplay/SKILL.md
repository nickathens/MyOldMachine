# Screenplay

Write, format, version, and export screenplays using Fountain markup.

## Tools

- **screenplain** (Python): Fountain to PDF/HTML/FDX conversion
- **afterwriting** (Node.js CLI): Fountain to PDF with analytics (page count, timing, dialogue stats, scene breakdown)

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

# Export to PDF (screenplain: clean formatting)
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format pdf

# Export to PDF (afterwriting: with scene numbers, watermark, custom config)
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format pdf --engine afterwriting --scene-numbers both

# Export to HTML or FDX
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format html
python $SKILL_DIR/scripts/screenplay.py export <project_dir> --format fdx

# Analyze script (page count, runtime, scenes, characters, locations)
python $SKILL_DIR/scripts/screenplay.py analyze <project_dir>

# List all versions
python $SKILL_DIR/scripts/screenplay.py versions <project_dir>

# Restore a previous version (auto-saves current draft first)
python $SKILL_DIR/scripts/screenplay.py restore <project_dir> --version 3

# Diff two versions (use 0 for current draft)
python $SKILL_DIR/scripts/screenplay.py diff <project_dir> --v1 2 --v2 5
```

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

Each screenplay project gets this structure:

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

## Afterwriting Engine Flags

When using `--engine afterwriting`, you can customize via:
- `--scene-numbers` : none, left, right, both
- `--watermark "DRAFT"` : Print watermark on every page
- `--no-title-page` : Skip title page
- `--a4` : Use A4 paper (default: US Letter)

## Documentation

The `docs/` directory within this skill contains screenwriting craft reference:
- `fountain-spec.md` : Complete Fountain syntax specification
- `structure.md` : Story structures (3-act, Save the Cat, Story Circle, Syd Field)
- `craft.md` : Dialogue, subtext, action lines, show-don't-tell, pacing
- `short-film.md` : Short film specific guidance
- `formatting.md` : Industry standard formatting rules

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
