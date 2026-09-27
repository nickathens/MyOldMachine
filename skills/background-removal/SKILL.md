# Background Removal

AI background removal with rembg (U2-Net by default), through the skill's
own script.

## Commands

```bash
# Remove background from a single image
python skills/background-removal/scripts/rembg_wrapper.py remove input.png output.png

- **Remove background**: automatic subject detection, transparent result
- **New background**: fill the removed area with a colour instead
- **Alpha mask**: the mask alone (white is kept)
- **Batch**: every image in a folder, the model loaded once

# Get alpha mask only
python skills/background-removal/scripts/rembg_wrapper.py mask input.png mask.png
```

## CLI Alternative

```bash
B=skills/background-removal/scripts/rembg_wrapper.py
python $B remove photo.jpg cutout.png                   # transparent PNG
python $B remove photo.jpg cutout.webp                  # transparent WebP (lossless)
python $B remove photo.jpg product.jpg                  # JPEG has no alpha: flattened onto white
python $B remove photo.jpg on-cream.png --background "#f4f1ea"
python $B remove portrait.jpg cutout.png --alpha-matting    # finer hair and fur edges, slower
python $B mask photo.jpg mask.png
python $B batch ./photos ./cutouts                      # PNGs; --format webp|tif|jpg
```

- The output name decides the format: .png, .webp and .tif keep the
  transparency, .jpg is flattened onto `--background` (white by default).
- The phone's EXIF rotation is applied, and the colour profile (Display P3,
  Adobe RGB) is carried into the result. CMYK input is converted through its
  profile; 16-bit grey input is scaled, not clipped.
- Batch skips nothing silently: a file that fails is reported and the run
  exits 1. Two inputs with the same name (`photo.jpg`, `photo.png`) become
  `photo_jpg.png` and `photo_png.png`.
- `--model`: `u2net` (default, downloaded), `u2net_human_seg` for people,
  `isnet-general-use`, `birefnet-general` (newer, larger). A model that is
  not in `~/.u2net` yet downloads there on first use (u2net itself is 176 MB).

Use the script rather than the `rembg` command line tool: the CLI needs
rembg's `[cli]` extras, and without them it stops with "No module named
'filetype'" (seen 2026-09-27). Before then the script wrote PNG bytes into a
.jpg, dropped the colour profile, reloaded the model for every image in a
batch, and let `photo.png` overwrite the cutout of `photo.jpg`.

## Examples

"Remove the background from this image"
"Make this image transparent"
"Put this product on a white background"
"Remove background from all images in folder"

## Notes

- Works best with a clear subject against a distinct background
- About 1 GB of memory while running
