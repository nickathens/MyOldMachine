# Image Editing

Edit images: resize, crop, rotate, convert formats, apply filters, adjust colors.

## Script

`scripts/image.py` in this skill directory. Run with `python <path-to-script> <command> [args]`.

## Commands

```bash
# Resize (maintains aspect ratio by default)
python scripts/image.py resize input.png output.png --width 800
python scripts/image.py resize input.png output.png --width 800 --height 600

# Crop (left, top, right, bottom)
python scripts/image.py crop input.png output.png --box 100 100 500 400

# Crop to center square
python scripts/image.py crop input.png output.png --square

# Rotate
python scripts/image.py rotate input.png output.png --angle 90

# Convert format
python scripts/image.py convert input.png output.jpg --quality 85

# Apply filter
python scripts/image.py filter input.png output.png --filter blur
python scripts/image.py filter input.png output.png --filter sharpen
python scripts/image.py filter input.png output.png --filter grayscale

# Adjust brightness/contrast/saturation
python scripts/image.py adjust input.png output.png --brightness 1.2 --contrast 1.1

# Get image info
python scripts/image.py info input.png

# Create thumbnail
python scripts/image.py thumbnail input.png output.png --size 200

# Composite/overlay images
python scripts/image.py composite base.png overlay.png output.png --position 10 10

# Add border
python scripts/image.py border input.png output.png --width 10 --color white
```

## Filters

blur, sharpen, contour, detail, edge_enhance, emboss, smooth, grayscale, sepia, invert

## Notes

- Uses PIL/Pillow library
- Supports PNG, JPG, GIF, BMP, WEBP, TIFF
- Quality option only affects JPG output
- Brightness/contrast values: 1.0 = no change, >1 = increase
