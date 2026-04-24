# Image Upscale Skill

AI-powered image upscaling using Real-ESRGAN.

## Capabilities

- **Upscale**: 2x or 4x resolution increase
- **Face enhance**: Better face restoration via GFPGAN
- **Tile-based**: Low-memory tiling for CPU-only systems

## Commands

The `python -m realesrgan` CLI does not ship in `realesrgan 0.3.0`. Use the wrapper script:

```bash
# 2x upscale
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 2

# 4x upscale
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 4

# With face enhancement (uses GFPGAN, downloads ~350 MB weights on first run)
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 4 --face

# Custom tile size (0 = no tiling, requires more RAM)
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 2 --tile 512
```

## Script Location

`scripts/upscale.py` — Python API wrapper around `RealESRGANer` + `RRDBNet`.

## Examples

"Upscale this image to 4x"
"Enhance this low resolution photo"
"Increase the resolution of this image"
"Upscale with face enhancement"

## Notes

- First run downloads model weights to `~/.cache/realesrgan/` (~65 MB for x2plus, ~65 MB for x4plus, ~350 MB for GFPGAN face enhance).
- 2x upscale: 768×432 → 1536×864 (~15 s on i5-6600K with `--tile 256`).
- 4x upscale: 512×512 → 2048×2048 (minutes on CPU; scale quadratically).
- CPU only on this machine: the GTX 970 is unsupported by the CUDA wheels Real-ESRGAN uses.
- The script auto-patches `torchvision.transforms.functional_tensor` (removed in torchvision ≥0.17) to keep `basicsr` happy. No manual site-packages edits needed.
