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

## Script Location

`scripts/vector.py` - Python wrapper for common operations

## Examples

"Create a simple logo with circles and text"
"Convert this SVG to high-res PNG"
"Generate social media template (1080x1080)"
"Create album cover template"
