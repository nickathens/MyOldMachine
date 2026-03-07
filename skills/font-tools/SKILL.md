# Font Tools

Font conversion, subsetting, and optimization.

## Commands

```bash
# Convert TTF to WOFF2
python3 -c "from fontTools.ttLib import TTFont; f=TTFont('font.ttf'); f.flavor='woff2'; f.save('font.woff2')"

# Subset font (only include specific characters)
pyftsubset font.ttf --output-file=font-subset.ttf --text="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Get font info
python3 -c "from fontTools.ttLib import TTFont; f=TTFont('font.ttf'); print(f['name'].getDebugName(4))"

# Convert OTF to TTF
python3 -c "from fontTools.ttLib import TTFont; f=TTFont('font.otf'); f.save('font.ttf')"

# List all glyphs
python3 -c "from fontTools.ttLib import TTFont; f=TTFont('font.ttf'); print('\n'.join(sorted(f.getGlyphOrder())))"
```

## Examples

"Convert this font to WOFF2"
"Subset this font to only English characters"
"Get info about this font"
"Optimize this font for web"
