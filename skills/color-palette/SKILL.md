# Color Palette

Generate and extract color palettes.

## Script

`scripts/palette.py` in this skill directory.

## Commands

```bash
# Extract palette from image: exact colours, ordered by share of the image
python scripts/palette.py extract image.jpg --colors 5

# Harmonies from one colour (#rrggbb or #rgb)
python scripts/palette.py complement "#3498db"
python scripts/palette.py analogous "#3498db"      # -30, base, +30 degrees
python scripts/palette.py triadic "#3498db"
python scripts/palette.py split "#3498db"          # base, +150, +210
python scripts/palette.py mono "#3498db" --colors 5

# Convert (all three formats, or one with --to)
python scripts/palette.py convert "#3498db" --to rgb
```

Notes: harmonies rotate the hue in HSL with floats kept between steps (they
used to be rounded to integers at each step, so the untouched base colour came
back changed, #3498db as #3497d9). ColorThief finds the palette; each colour
is then refined to the true mean of the pixels nearest it (ColorThief alone
quantises, reporting 28,204,28 for a flat 30,200,30), with its share of the
image. Pixels more than half transparent are ignored.

## Color Harmonies

- **Complementary**: Opposite on color wheel
- **Analogous**: Adjacent colors
- **Triadic**: Three evenly spaced
- **Split-complementary**: Base + two adjacent to complement

## Notes

- Uses colorthief for image palette extraction
- Supports HEX, RGB, HSL color formats
