# Color Palette

Generate and extract color palettes.

## Script

`scripts/palette.py` in this skill directory.

## Commands

```bash
# Extract palette from image
python scripts/palette.py extract image.jpg --colors 5

# Generate complementary colors
python scripts/palette.py complement "#3498db"

# Generate analogous colors
python scripts/palette.py analogous "#3498db"

# Convert color formats
python scripts/palette.py convert "#3498db" --to rgb
```

## Color Harmonies

- **Complementary**: Opposite on color wheel
- **Analogous**: Adjacent colors
- **Triadic**: Three evenly spaced
- **Split-complementary**: Base + two adjacent to complement

## Notes

- Uses colorthief for image palette extraction
- Supports HEX, RGB, HSL color formats
