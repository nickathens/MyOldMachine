# Image Upscale Skill

Image upscaling with Real-ESRGAN, and a measured workflow for choosing HOW to
upscale, because the tool is the smaller half of the job.

**Paths:** the commands below use `$SKILL_DIR`, which nothing sets for you. Set it first: `SKILL_DIR=skills/upscale` (without it `python $SKILL_DIR/scripts/...` runs `/scripts/...` and fails).

## Choosing the route, before running anything

"Upscale this" is not one request. Present the routes, measure the file,
recommend one, and when the image is a deliverable ask which way before
spending time or money. Never just run a tool.

**Route A, faithful hybrid (default for clean sources).** Free, on machine,
pixel true. Use when the source pixels are good and must survive: renders,
graded stills, finished design work. `scripts/hybrid_upscale.py`, below.

**Route B, generative repaint.** For flaws baked into the source itself
(invented micro texture, garbled small text): no upscaler can fix those,
because they are content, not resolution. That is an image to image job with a
generation model (see the image-gen skill), it costs credits, and the output is
a repaint: layout survives, fine geometry is reinvented. Text that must read
correctly gets inventoried at zoom first and spelled out in the prompt in
quotes; corrections that the model refuses in a full frame pass are done as
tight crop edits and pasted back as glyph masks, aligned by ECC, never as
rectangles. Archive every attempt beside the master: the attempts are the undo
history, and a region revert from a kept attempt is seamless by construction.

**Creative upscalers: decline.** Measured signature on smooth surfaces: an
invented crawling micro texture at 2 to 8 px scale where the picture's real
detail sits above 30 px, plus hallucinated glyphs on small lettering. The fine
band metric below is the instrument that catches it.

**Two standing rules.** A deliverable is never downscaled and never reduced in
bit depth; a lighter copy is only ever a labelled viewing copy at the same
frame size. And never remove or replace signage, logos or emblems as part of a
bundled proposal: that decision is asked as its own explicit question and
defaults to leaving them untouched.

## Route A: the faithful hybrid

```bash
python $SKILL_DIR/scripts/hybrid_upscale.py in.png out.png --scale 2 --metrics
python $SKILL_DIR/scripts/hybrid_upscale.py in.png out.png --scale 4 --metrics
python $SKILL_DIR/scripts/hybrid_upscale.py in.png out.png --mode plain     # raw ESRGAN
```

Plain ESRGAN fails a clean render two ways, both measured: it crushes the real
fine grain in flat areas (a floor's fine band std fell from a true 2.70 to
0.70) and drifts the whole picture about two levels cool. So the hybrid keeps
ESRGAN only on a feathered structure mask (Sobel of blurred luma, ramp p50 to
p90, feather sigma 8, about a fifth of a typical frame), hands everything else
to Lanczos, and pins the low frequencies (gaussian sigma 6) to Lanczos so the
tone cannot drift.

What the hybrid keeps (each fixed and tested 2026-09-27): EXIF orientation is
applied before anything (a portrait phone photo used to come back sideways);
the ICC profile is carried into the output (an Adobe RGB master used to come
back untagged and show as sRGB); alpha is carried through; a 16-bit master,
colour or grey, keeps its depth on `--mode lanczos` and is refused by the 8-bit
neural modes unless `--force-8bit` (Pillow reads a 16-bit RGB PNG or TIFF as
8-bit RGB, so this used to flatten silently). The output is always a lossless
PNG, so the name must end in `.png`. `--tile 0` is one tile for the whole
picture.

`--metrics` prints the three numbers that decide whether an upscale is honest,
measured on the delivered file:

- **downscale back PSNR vs the source.** The hybrid measured 39.96 dB on its
  reference job; plain ESRGAN 35.1; a creative upscaler 34.1. Higher is truer.
- **fine band std in flat areas, beside the Lanczos truth.** Just under truth
  is right (2.59 against 2.70 there). Far below is airbrush; above is invented
  texture.
- **colour drift per channel vs Lanczos.** Should read zero.

The script is a hand written RRDBNet loading the official x2plus/x4plus
weights with `strict=True` (no basicsr dependency), tiled, on MPS, CUDA or CPU,
whichever exists. About 4 seconds for a 1.5 MP image at 2x on an M series GPU;
weights (~65 MB each) download to `~/.cache/realesrgan` on first use.

**Memory.** On Linux both scripts run themselves again inside a memory-capped
user scope (6 GB, `UPSCALE_MEM_MAX` to change it), outside the bot's service,
where an out of memory kill would stop the whole service. Measured on CPU: 4x
with the default 512 tile peaks about 4.5 GB, 2.1 GB with `--tile 256`, 2x
about 1.7 GB; the full-size arrays add about 0.1 GB per output megapixel (a
1080p frame at 4x needs about 5 GB in all). A job the cap kills says so. On
Linux where no scope can be made (no user session), only a job estimated under
3 GB runs; the refusal says what to lower. macOS runs as before.

## The legacy wrapper

The `python -m realesrgan` CLI does not ship in `realesrgan 0.3.0`. The
original wrapper around the realesrgan package is still here, including GFPGAN
face enhancement, which the hybrid does not do:

```bash
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 2
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 4 --face
python $SKILL_DIR/scripts/upscale.py input.jpg output.png --scale 2 --tile 512
```

It also applies EXIF orientation, keeps the ICC profile, keeps 16 bits in a
PNG output, and exits 1 when the output could not be written (it used to print
"Saved" for a file that was never written).

`--face` takes the upsampler's proven device (2026-10-07). GFPGAN picked CUDA
on its own, so on a card the CUDA wheels carry no kernels for (a GTX 970) it
died on every run, and it saved its face detector's weights under whatever
directory the run started in. Those weights now live in
`~/.cache/realesrgan/gfpgan/weights`.

## Examples

"Upscale this image to 4x"
"Enhance this low resolution photo"
"Increase the resolution of this image"
"Upscale with face enhancement"

## Notes

- First run downloads model weights to `~/.cache/realesrgan/` (~65 MB for
  x2plus, ~65 MB for x4plus, ~350 MB for GFPGAN face enhance plus ~190 MB for
  its face detector and parser). A download that goes silent for 60 seconds
  fails instead of hanging.
- `hybrid_upscale.py` picks MPS, CUDA or CPU automatically. The legacy wrapper
  is CPU only where the GPU is unsupported by the CUDA wheels (e.g. GTX 970);
  budget minutes there and use `--tile`.
- `upscale.py` auto-patches `torchvision.transforms.functional_tensor` (removed
  in torchvision ≥0.17) to keep `basicsr` happy. No manual site-packages edits
  needed.
- An upscale is judged with the `--metrics` numbers plus eyes at 100 percent on
  the surfaces that matter: lettering, flat floors and walls, and any place a
  creative tool would invent texture.
