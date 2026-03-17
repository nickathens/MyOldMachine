# Background Removal

AI-powered background removal from images using rembg.

## Commands

```bash
# Remove background from a single image
python skills/background-removal/scripts/rembg_wrapper.py remove input.png output.png

# Batch process a directory
python skills/background-removal/scripts/rembg_wrapper.py batch input_dir/ output_dir/

# Get alpha mask only
python skills/background-removal/scripts/rembg_wrapper.py mask input.png mask.png
```

## CLI Alternative

```bash
# Direct rembg CLI
rembg i input.png output.png

# Process from stdin
cat input.png | rembg i > output.png

# Batch process directory
rembg p input_dir/ output_dir/
```

## Examples

"Remove the background from this image"
"Make this image transparent"
"Remove background from all images in folder"

## Notes

- First run downloads AI model (~170MB)
- Works best with clear subjects
- Output is PNG with alpha channel
- Supports JPG, PNG, WebP input
