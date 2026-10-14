# Image Upscale Skill

Image and video upscaling: free on this machine with Real-ESRGAN, or paid
through Magnific's API behind a quote. A measured workflow for choosing HOW to
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

**Route C, Magnific (paid).** Magnific's own upscalers through its API, on
the account's credits. The one route here for video, and a second opinion for
stills the hybrid cannot lift. Precision is the default for both; nothing runs
without the user's yes on a quote. See the Magnific section below.

**Creative upscalers: decline.** Measured signature on smooth surfaces: an
invented crawling micro texture at 2 to 8 px scale where the picture's real
detail sits above 30 px, plus hallucinated glyphs on small lettering. The fine
band metric below is the instrument that catches it. Magnific's Creative mode
is one of these: it runs only when the user asks for it by name.

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

## Route C: Magnific

Magnific's upscalers through its API (`scripts/magnific.py`: Python's standard
library plus Pillow, and ffprobe, which comes with ffmpeg, for video). Every
job takes credits from the account's one balance: the web plan's "unlimited"
never covers the API, and on an individual plan no API call can read the
balance (the web app's top bar shows it). Key: `MAGNIFIC_API_KEY` in this
install's `.env` at the repo root. The bot strips keys from the environment it
hands to commands, so the script reads that file itself; a key exported in the
shell is used first. The key is kept to upscaling: the script refuses every
Magnific path except the upscalers and the upload a local video needs, before
a request leaves.

**Nothing spends without a yes on a quote.** Always two steps:

```bash
python $SKILL_DIR/scripts/magnific.py quote photo.jpg --scale 4     # free, offline
python $SKILL_DIR/scripts/magnific.py run 3fa91c -o photo_4x.png     # only after the user's yes
```

`quote` measures the file, prints the price from Magnific's API price list and
a six character code. Tell the user the price in plain words and wait. `run
CODE` is the only command that spends: that quote's job, once. It refuses when
the file changed, the quote is over 24 hours old, the script's price list
changed since, or the code already ran. One yes, one job.

| File | `--mode` | Upscaler | Settings |
|---|---|---|---|
| image | `precision` (default) | Precision V2, close to the source | `--scale` 2 to 16 (default 2); `--sharpen`, `--smart-grain`, `--ultra-detail` 0 to 100; `--flavor` sublime, photo or photo_denoiser |
| image | `creative` | Creative, invents detail | `--scale` 2, 4, 8 or 16; `--prompt`; `--creativity`, `--hdr`, `--resemblance`, `--fractality` -10 to 10; `--engine`; `--optimized-for` |
| video | `precision` (default) | Video Precision, faithful | `--resolution` 1k, 2k or 4k (default 2k); `--sharpen`, `--smart-grain` 0 to 100; `--strength` 0 to 100 (Magnific's default 60 blends the source back in) |
| video | `standard`, `turbo` | Video Upscaler | `--resolution`; `--creativity` 0 to 100; `--sharpen`, `--smart-grain`; `--flavor` natural (sent by default: Magnific's own default, vivid, changes the colours) or vivid |

Precision V2 is not detail free at Magnific's defaults. By its docs, ultra
detail (default 30) is how much fine detail the AI adds, smart grain (7) adds
texture and sharpen (7) sharpens; 0 for each stays closest to the source. The
quote names any of the three left unset. Video Precision is the one Magnific
describes as adding nothing.

Any video mode takes `--prores` for a ProRes 422 HQ master plus an H.264
preview; without it the result is an H.264 MP4. A setting that does not apply
to the chosen upscaler is refused, never dropped.

**Prices** (API price list, euros, read 2026-10-14). An image is €0.10 up to a
2k result, €0.20 to 4k, €1.20 to 10k, the same in both modes. Magnific's docs
say the price follows the result's pixel area but give no pixel size for a
tier, and their six worked examples fit no single rule (1280x720 at 4x is
€0.40 there, €1.20 by this list). So the quote counts the long side, which is
at or above every published figure (a test holds it to the six), and says the
real charge can be lower. Creative caps a result at 25.3 MP. Video is priced
per frame: Precision and Standard €0.013 at 1k, €0.019 at 2k, €0.024 at 4k;
Turbo €0.010, €0.012, €0.013. Frames are counted with ffprobe (the larger of
the container's count and a packet count). The API takes credits and Magnific
publishes no credit rate for it, so the euro figure is the reference: after a
first job, compare the balance before and after.

**Not offered**, because no price can be quoted before the job: 720p video,
the Topaz video upscaler, FPS boost and frame interpolation, and Precision V1
(it has no scale setting). A video already at the target size is refused.

**Results** are saved exactly as Magnific sends them, never encoded again. The
extension follows the real format (asking for `.png` and getting a JPEG saves
`.jpg` and says so), an existing file is never written over, and a line per
file says what arrived: size, depth, frames, and whether transparency or sound
was lost. Result links expire 30 minutes after a job ends, so `run` waits and
downloads at once (images up to 15 minutes, video up to 2 hours, `--wait`
changes it). Start a long video job in the background; if the wait runs out,
`fetch CODE` picks it up. A download that stops short of its stated size is
retried and never saved as the result, and a garbled answer from Magnific is
handled like a dropped connection (a missed status check, or an `unknown`
start, below). `fetch` also looks again at a job that ended without a result.
A local video is first uploaded to Magnific's storage (up to 1 GiB, MP4, MOV
or WebM), where it stays up to about 7 days behind an unguessable link.

**Free commands:** `check` (is the key good, which jobs exist), `status CODE`,
`fetch CODE`, `ledger` (every quote and job, and the price list total of what
was sent). When a start goes unanswered (a network error or a 5xx), the job
may or may not exist and may have been charged: the quote is marked `unknown`
(or stays `submitting` when the script itself was cut off) and never runs
again. `check` lists Magnific's jobs, and `fetch CODE --task TASK_ID` adopts
the right one. Quotes and jobs are kept in `~/.local/state/magnific/`
(`$XDG_STATE_HOME` moves it).

Exit codes: 0 done, 1 failed or unknown, 2 refused before anything was sent,
3 still running. Proof: `tests/test_upscale_magnific.py` (no network; a socket
guard fails any test that leaves the machine) and `proof/magnific_battery.py`
(planted faults, each must turn its own test red).

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
