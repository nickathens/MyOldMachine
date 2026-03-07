# GIMP Skill

Image editing and manipulation via GIMP's Script-Fu and Python-Fu.

## Capabilities

- **Batch processing**: Resize, crop, convert multiple images
- **Filters**: Blur, sharpen, color adjustments, artistic effects
- **Format conversion**: PNG, JPEG, WebP, TIFF, PSD (import)
- **Automation**: Scripted image manipulation
- **Layer operations**: Merge, flatten, extract

## Commands

```bash
# Batch resize images
gimp -i -b '(python-fu-batch-resize RUN-NONINTERACTIVE "/input/*.jpg" 800 600 "/output/")' -b '(gimp-quit 0)'

# Convert format
gimp -i -b '(file-png-save RUN-NONINTERACTIVE (car (gimp-file-load RUN-NONINTERACTIVE "input.jpg" "input.jpg")) (car (gimp-image-get-active-layer (car (gimp-file-load RUN-NONINTERACTIVE "input.jpg" "input.jpg")))) "output.png" "output.png" 0 9 0 0 0 0 0)' -b '(gimp-quit 0)'

# Apply filter via script
gimp -i -b '(script-fu-batch-unsharp-mask pattern radius amount threshold)' -b '(gimp-quit 0)'
```

## Script Location

`scripts/batch.py` - Batch image processing wrapper

## Examples

"Resize all images in folder to 1080p"
"Convert PNG to JPEG with 85% quality"
"Apply blur to this image"
"Create thumbnail from image"
"Batch crop images to square"

## Notes

- Use `-i` flag for non-interactive (no GUI) mode
- `-b` runs batch commands
- GIMP 2.10 uses Script-Fu (Scheme) and Python-Fu
- For simple operations, ImageMagick may be faster
