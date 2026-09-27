# Inkscape Skill

Vector graphics creation and manipulation via Inkscape CLI.

## Capabilities

- **Create SVGs**: Programmatic vector graphics generation
- **Convert formats**: SVG to PNG, PDF, EPS
- **Batch operations**: Process multiple files
- **Text to path**: Convert text to vectors
- **Export options**: Custom DPI, area selection

## Commands

```bash
# Convert SVG to PNG at 300 DPI
inkscape input.svg --export-filename=output.png --export-dpi=300

# Convert to PDF
inkscape input.svg --export-filename=output.pdf

# Export specific area
inkscape input.svg --export-area=0:0:100:100 --export-filename=crop.png

# Run action commands
inkscape input.svg --actions="select-all;object-to-path;export-filename:output.svg;export-do"
```

## vector.py

`scripts/vector.py` writes three simple SVG templates and converts SVG with Inkscape:

```bash
V=skills/inkscape/scripts/vector.py
python $V social --title "Grand Opening" --subtitle "Friday 12 June" -o post.svg   # 1080x1080, gradient
python $V album --title "Night Swim" --artist "Artist" -o cover.svg               # 1400x1400, rings
python $V logo --title "Brand" -o logo.svg                                         # 800x400, text
python $V convert -i post.svg --format png --dpi 300      # post.png beside the input
python $V convert -i post.svg -o post.pdf                 # format taken from the output name
```

Titles are measured with the font Inkscape draws with (fontconfig's
sans-serif) and wrapped, then shrunk, to stay on the canvas; before
2026-09-27 a long title ran off both edges. `convert` without `-o` used to
write an SVG to /tmp whatever `--format` said. A conversion that writes
nothing is an error, and it leaves any earlier file of that name alone.
`convert` refuses an output that is the input itself (`-i photo.png` with no
`-o` would be one), where it used to delete the input.

## Examples

"Create a simple logo with circles and text"
"Convert this SVG to high-res PNG"
"Generate social media template (1080x1080)"
"Create album cover template"
