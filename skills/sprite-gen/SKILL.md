# Sprite Generator Skill

Create sprites and sprite sheets for games.

## Capabilities

- **Sprite sheets**: Combine images into grid
- **Split sheets**: Extract frames from sprite sheet
- **Resize**: Scale sprites to specific sizes
- **Pixel art**: Nearest-neighbor scaling

## Commands

```bash
S=skills/sprite-gen/scripts/sprite.py

# Frames into a sheet (natural order: frame_2 before frame_10)
python $S sheet frame_*.png --cols 4 --output spritesheet.png --padding 2

# Sheet into frames, named by cell (row * cols + col); empty cells are skipped, numbering kept
python $S split spritesheet.png --cols 4 --rows 4 --padding 2 --output frames/

# Pixel-perfect scale, and pixelate
python $S resize sprite.png --scale 2 --output sprite_2x.png
python $S pixelate photo.png --pixel-size 8 --output pixel.png
```

Frames of different sizes get cells as big as the largest, each frame
centred (the first frame's size used to be taken for all). Fixed 2026-09-27:
a text sort put frame_10 between frame_1 and frame_2, and split renumbered
the frames after an empty cell.

## Examples

"Combine these frames into a sprite sheet"
"Split this sprite sheet into individual frames"
"Scale this sprite to 2x size"
"Create a 4x4 sprite sheet"
