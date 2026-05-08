# Design Library

A curated set of DESIGN.md files describing established brand visual systems.
Pass `--design-md <name>` to `create_presentation.py` (or set `"design_md": "<name>"`
in the treatment JSON) to seed `scheme` and `fonts` from one of these files.

## Available entries

| Slug      | Voice                                                             |
|-----------|-------------------------------------------------------------------|
| `apple`   | Premium minimal. White canvas, near-black text, blue link accent. |
| `linear`  | Dev-tool dark canvas (#010102) with lavender-blue accent.         |
| `notion`  | Illustration-rich, multi-color, pastel feature cards.             |
| `runway`  | Pure black + white. Single-typeface system. AI/film posture.     |
| `stripe`  | Financial infrastructure. Deep navy ink, electric-indigo primary. |
| `vercel`  | White gallery. Compressed Geist tracking. Shadow-as-border.       |

## Format

Two formats are supported:

1. **YAML frontmatter + body markdown** (Linear, Stripe, Apple, Notion, ...).
   `colors:` and `typography:` keys map directly into `scheme` and `fonts`.

2. **Prose markdown only** (Vercel, Runway, ...). The parser extracts hex codes
   from `**Name** (#hex): description` items in a `## Color Palette` section
   and font families from `**Primary**: Family` lines under `## Typography`.

## Provenance

These files are forked from VoltAgent's `awesome-design-md` repository under
its MIT license: https://github.com/VoltAgent/awesome-design-md

Local edits should be limited to fixing typos / clarifying token names. New
entries can be authored from scratch using the same shape — the parser only
needs `colors:` and a heading/body font to produce a usable seed.

## Adding your own brand

Drop a `<slug>.md` (or `<slug>/DESIGN.md`) into this directory. Either:

```yaml
---
name: My Brand
description: One-line voice summary.
colors:
  canvas: "#0a0a0a"
  ink: "#f0eee8"
  primary: "#c9a84c"
fonts:
  heading: "Manrope"
  body: "Manrope"
---
```

…or write a prose `## Color Palette` section with `**Name** (#hex): role`
items and a `## Typography` section with a `**Primary**: Family` line.

Run `python ../scripts/design_md.py show <slug>` to verify what the parser
extracts before using it in a treatment.
