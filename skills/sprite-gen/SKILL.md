# Sprite Generator Skill

Create sprites and sprite sheets for games.

## Capabilities

- **Sprite sheets**: Combine images into grid
- **Split sheets**: Extract frames from sprite sheet
- **Resize**: Scale sprites to specific sizes
- **Pixel art**: Nearest-neighbor scaling

## Script Location

`scripts/sprite.py` - Sprite manipulation tools

## Commands

```bash
# Create sprite sheet from frames
python sprite.py sheet frame_*.png --cols 4 --output spritesheet.png

# Split sprite sheet into frames
python sprite.py split spritesheet.png --cols 4 --rows 4 --output frames/

# Resize sprite (pixel-perfect)
python sprite.py resize sprite.png --scale 2 --output sprite_2x.png

# Create tileset
python sprite.py sheet tile_*.png --cols 8 --rows 8 --output tileset.png
```

## Examples

"Combine these frames into a sprite sheet"
"Split this sprite sheet into individual frames"
"Scale this sprite to 2x size"
"Create a 4x4 sprite sheet"
