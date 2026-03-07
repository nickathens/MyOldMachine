# Image Upscale Skill

AI-powered image upscaling using Real-ESRGAN.

## Capabilities

- **Upscale**: 2x, 4x resolution increase
- **Denoise**: Remove noise while upscaling
- **Face enhance**: Better face restoration

## Commands

```bash
# Basic 4x upscale
python -m realesrgan -i input.jpg -o output.png -s 4

# 2x upscale
python -m realesrgan -i input.jpg -o output.png -s 2

# With face enhancement
python -m realesrgan -i input.jpg -o output.png -s 4 --face_enhance
```

## Script Location

`scripts/upscale.py` - Upscaling wrapper

## Examples

"Upscale this image to 4x"
"Enhance this low resolution photo"
"Increase the resolution of this image"
"Upscale with face enhancement"

## Notes

- First run downloads model (~60MB)
- 4x upscale: 512x512 → 2048x2048
- CPU only (GTX 970 not supported)
- Processing time depends on image size
