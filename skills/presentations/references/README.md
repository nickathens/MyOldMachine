# Aesthetic References

Hand-curated reference library for the presentations skill. Each file encodes a brand or cinematic aesthetic as structured frontmatter plus prose notes.

These are not meant to be imitated literally. They are anchor points. When a treatment says "in A24 style" or "something close to MUBI," the reference seeds a baseline scheme/fonts/mood. Treatment-level JSON overrides still win on every field.

## Usage

```bash
# List references
python scripts/references.py list

# Inspect one
python scripts/references.py show a24

# Use in a treatment
python scripts/create_presentation.py --json mydoc.json --aesthetic a24 --output out.html
```

## File format

```yaml
---
name: A24
category: film
mood: literary, melancholic, modern-gothic
color_palette:
  bg: "#0a0a0a"
  text: "#f3f1ea"
  accent: "#c7a25c"
  brand_color: "#b32222"
fonts:
  heading: "Neue Haas Grotesk"
  body: "Neue Haas Grotesk"
  serif: "GT Sectra"
avoid:
  - SaaS gradients
texture_notes:
  - film grain
  - deep blacks
---

Free-form prose below describes the aesthetic in plain language.
```

## Curation rules

- Real fonts and real hex values only. No invented colors.
- Reference the brand's actual publication/film presence, not a fan pastiche.
- `avoid:` is as important as `color_palette:` — it tells the treatment what not to drift toward.
- New references go in as single `.md` files. Filename is the slug.
