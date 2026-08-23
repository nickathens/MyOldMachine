# Resolution: what a file carries, what an enlargement adds, and what boils

The department that is entered twice, and putting the second visit where the
first one belongs is the expensive error.

Two kinds of material below, and they are marked. Sections headed **Ground**
come from primary sources, read on the dates given, and are stated with the
source. Sections headed **Measured here** were reproduced against ground truth
while `upres.py` was built, so the numbers are this machine's own and are
reproducible with `selftest.py`. Everything else is the general form of failures
34 to 39, which happened on real files.

Engine: `scripts/upres.py` and `scripts/_res.py`. They need numpy, OpenCV and
SciPy in this skill's own environment, never the bot's, and they are run with
`~/.venvs/post/bin/python`.

---

## 0. Two homes in the spine

    RESTORE AT SIZE          stage 3, before the cut, long before colour
    ENLARGE TO THE RASTER    stage 10, at the master

They are not the same operation and they do not belong in the same place.

**Restoring is a stage 3 job** because it changes every pixel the grade will
then be built on. Restore after the grade and the grade is describing a picture
that no longer exists; restore after the cut and every handle and every version
has to be rebuilt. It also comes AFTER picture repair, because repair removes
frames that were never real and there is no sense paying a restoration model to
rebuild a duplicate.

**Enlarging is a stage 10 job** because the delivery raster is a delivery fact,
not a working one. Enlarge at the head of the pipeline and every department
downstream pays for pixels nobody asked for: the comp tracks at four times the
area, the grade renders four times as long, and when the raster later changes,
which it does, all of it is rebuilt.

The one case that breaks the rule and is worth knowing: if the delivery raster
is larger than the source AND the comp has to place elements at delivery scale,
the enlargement moves earlier by necessity. Say so explicitly and write the
reason down, because the next person will read it as a mistake.

---

## 1. A raster is a claim

The frame size in a file's header is what the container was told, not what the
picture holds. A whole review set once arrived named for 4K and every file in it
was 1280x720 (failure 1). The same set had files named for one room showing
three different rooms. Names drift; headers get rewritten; the pixels do not.

`spec.py probe` reads the header. `upres.py effres` reads the PICTURE.

### Ground: why the spectrum can answer this

The radially averaged power spectrum of a natural image follows a power law over
its mid band. An enlargement is a low pass filter followed by a resample, so it
snaps that law off at the SOURCE's Nyquist and leaves nothing above it. The
frequency where the measured spectrum leaves its own extrapolated law is
therefore the resolution the file really carries.

### Measured here: the calibration, and what it cost

Fitted band **0.03 to 0.16 of Nyquist**, 128 radial bins, knee at **6 dB** below
the extrapolated law, sustained.

The top of the fit band is the number that took the work. At the obvious choice
of 0.30 a real lens has already begun to roll off INSIDE the fit band, so the
law being extrapolated is the softness itself and the tool measures the blur
through the blur. That is the same trap as scoring a de-plastic filter with the
filter that made it. Curvature of the log-log fit, in dB per decade squared,
across a native frame, three resamplers and lens PSFs to sigma 1.2:

| fit band | native | Lanczos 2x | lens sigma 0.8 | lens sigma 1.2 | defocus sigma 25 |
|---|---|---|---|---|---|
| 0.06 to 0.30 | -0.3 | -1.9 | -7.5 | -16.4 | +14.3 |
| **0.03 to 0.16** | **+1.2** | **+0.3** | **-0.7** | **-3.1** | **+254.2** |

On the narrowed band every honest case sits inside 3.1 and the unmeasurable one
sits at 254. That is the guard: two orders of margin, not a tuned threshold.

Readings on synthetic ground truth, declared raster 1920x1080:

| case | verdict | knee | effective | consistent with |
|---|---|---|---|---|
| native | CARRIES | 1.00 | 1920x1080 | |
| good lens (sigma 0.5) | CARRIES | 1.00 | 1920x1080 | |
| Lanczos 2x | SHORT | 0.566 | 1088x612 | a 960x540 source enlarged 2x |
| bicubic 2x | SHORT | 0.543 | 1042x586 | a 960x540 source enlarged 2x |
| Lanczos 1.5x | SHORT | 0.699 | 1342x755 | a 1280x720 source enlarged 1.5x |
| Lanczos 3x | SHORT | 0.371 | 712x401 | a 640x360 source enlarged 3x |
| Lanczos 4x | SHORT | 0.285 | 548x308 | a 480x270 source enlarged 4x |
| nearest 2x | SHORT | 0.500 | 960x540 | 960x540 pixel doubled 2x |
| defocus sigma 25 | UNDETERMINED | | | |
| flat card | UNDETERMINED | | | |

And on a real H.264 encode of a real pan, through the real decode path: the
960x540 source read CARRIES at knee 1.00, and its own Lanczos 2x read SHORT at
0.566 and named the source raster correctly.

### Three things the measurement cannot do, and says so

**It cannot tell an enlargement from a soft lens or from heavy compression.**
All three suppress the same band. The verdict never claims otherwise: it offers
a source raster as *consistent with* the reading and names the alternatives in
the same sentence. What it always settles is the useful half, which is that
there is nothing above that frequency for a resampler to find.

**The knee is an UPPER bound on the source's Nyquist**, because a resampler
rolls off through a transition band instead of cutting. Measured bias across
Lanczos, bicubic and bilinear at 2x, 3x, 4x and 1.5x: 1.05 to 1.14. That is why
a source raster is matched by the rule `ratio <= knee <= 1.25 * ratio` and not by
proximity.

**It is blind to pixel doubling on its own.** A nearest neighbour enlargement
does not band limit anything; it replicates the spectrum, so the power runs to
Nyquist and the spectral verdict reads CARRIES on a picture that carries nothing
(measured: knee 0.99 against the Lanczos version's 0.57). The lattice detector
covers that case, and it is exact rather than statistical: for a step of N, N-1
of every N neighbouring column differences are IDENTICALLY zero. Measured ratio
of the quiet parity to the loud one: **0.000** on a nearest 2x, **0.860** on a
Lanczos 2x, **0.998** on a native frame. Where the two disagree the exact fact
wins and the spectral verdict is overridden.

### Read the best frame, never one frame

Resolution is a ceiling. One frame that carries detail to Nyquist proves the
file can, and no number of soft frames disproves it, because a defocus, a whip
pan or a fade to black reads SHORT for reasons that have nothing to do with the
file. `effres` samples across the clip and takes the verdict from the best
readable frame, and it says so in the note.

---

## 2. A stills upscaler has no idea a frame has neighbours

This is the fault that reaches delivery, because no still frame shows it.

Run a per frame model down a clip and every picture invents its own detail
independently. Freeze any one of them and it looks superb. Play them and the
surface crawls. The `upscale` skill in this toolkit is exactly such a tool: it
is the right instrument for a card, a logo or a poster, and the wrong one for a
shot, and nothing said that out loud before this document.

### Ground: the standard measurement, and its standard trap

Temporal consistency in video restoration is measured with warping error: push a
frame forward along the optical flow and see how far it misses the next one.
Every published treatment that uses it also warns that the number is LARGE on
ground truth video, because flow is wrong at every occlusion and every
specular, so the level is meaningless on its own.

That is this skill's generation floor argument, moved from code levels into
time. Read the measurement against the source's own, never against zero.

### Measured here: the sign convention that cost an afternoon

OpenCV's `calc(f, g)` returns the field F with `f(y,x) ~ g(y+Fy, x+Fx)`. So the
field that resamples `src` into `dst`'s frame is `calc(dst, src)`, not
`calc(src, dst)`. On an exact seven by three pixel translation with known ground
truth: **the right way round leaves 0.0017 mean absolute error, the wrong way
round leaves 0.0824** — and 0.0824 is indistinguishable from two unrelated
frames. A sign slip here does not look like a bug. It looks like a film that
boils, on every candidate equally, so nothing can be ranked and the instrument
silently has no sensitivity at all. It was found only by testing the solver
against a flow that was KNOWN rather than solved.

### Measured here: why the verdict is not the warping error ratio

The obvious statistic is the candidate's warping error over the control's. It
was built, and it is reported, and it does not drive the verdict, because its
denominator is *how trackable the shot is*:

| candidate | control error | warping ratio | excess incoherence |
|---|---|---|---|
| Lanczos 2x | 0.000069 | 1.12 | +0.0005 |
| downscale | 0.000062 | 1.29 | +0.0002 |
| per frame model | 0.000069 | **669.08** | **+0.6027** |

A threshold on an unbounded ratio is a threshold on the shot. On a clean
synthetic pan the control's error is 7e-05 and a fault reads 669 times it; the
same fault on a hand held plate reads three times it. Same fault, same file,
different verdicts.

**Detail incoherence** is used instead, and it is bounded by construction. Take
the fine band residual of each frame, warp it forward, and normalise the
difference by the residual's own magnitude. For two independent zero mean fields
the mean absolute difference is `2s/sqrt(pi)` and the normaliser is
`2s*sqrt(2/pi)`, so the ratio is `1/sqrt(2) = 0.7071` whatever the amplitude.
That fixed ceiling is what makes the number readable: **0 is detail that belongs
to the scene, 0.707 is detail invented from nothing on every frame.**

The verdict is on the EXCESS over the control's own incoherence:

    STABLE     excess <= 0.021   (3 per cent of the independent ceiling)
    MARGINAL   excess <= 0.085   (12 per cent)
    BOILS      above that

Calibration: honest cases read +0.0005 and +0.0002; a barely visible 1.2 per
cent per frame wobble on synthetic ground truth reads +0.029; a per frame model
on a real encode reads +0.60. The limits sit an order of magnitude above the
honest cases and an order below the mild fault.

### One motion model, applied to both

The flow is solved ONCE, on the control, and applied to the candidate. Solving
it separately on each would let a boiling candidate move the yardstick it is
being measured with. This is the opposite discipline to the compositing
department's two independent routes, and deliberately so: there the point is that
the check shares no assumption with the build, here the point is that the two
things being compared share every assumption except the one under test.

### When the instrument has no grip

Two refusals, both scale free. If the flow can be believed on less than half the
frame, or if the CONTROL's own detail already reads more than 0.35 incoherent
against a fully independent 0.707, the flow never locked onto this motion and
anything measured against that floor would be noise. The answer is UNDETERMINED
with the reason named, not a number.

---

## 3. The pairing that decides whether the run was worth it

Two measurements, four answers, and only one of them is a restoration:

| effective resolution of the result | downscale back PSNR | what happened |
|---|---|---|
| high | high | detail was added AND it is faithful |
| high | low | detail was INVENTED, not recovered |
| low | high | a resample with extra steps |
| low | low | the run damaged the picture |

Neither number can do this alone. Downscale back PSNR is high for a plain
resample, which added nothing; effective resolution is high for a hallucinating
model, which added lies.

**Read the downscale back deficit, never the level.** The neutral resample's own
round trip is the ceiling, and it is content dependent: a noisy star field round
trips at 43.10 dB where a smooth cloud frame reaches 56.91, so 38.60 dB is near
ceiling on one file and poor on the other. `verify` prints both and the gap.

---

## 4. Detail by luminance band, because the sign flips with the material

A single global detail figure hides half the faults, because a detail model's
sign is not fixed. On one job in one recipe on one day, the same structure mask
**invented** texture on a cloud sky at 1.86x and **deleted** a star field at
0.61x. Splitting the frame by luminance is the cheapest split that separates sky
from ground with nobody drawing a horizon, and `verify` reports every band with
its own verdict and a tolerance of 25 per cent in either direction.

The star field case is worth stating in full because every stock metric hid it.
Against a plain Lanczos ceiling of 72.3 per cent of the faintest third of stars
retained: raw ESRGAN kept 13.5 per cent and invented 290 peaks, the stock hybrid
kept 51.2 per cent, and a guarded hybrid kept 71.5 per cent. PSNR, grain
deviation and edge gain all looked fine throughout. The cause was that the
structure mask pre-blurs luma at sigma 1.5, which flattens a point source, so
the mask never rises above about 0.25 on a star and that quarter vote is enough
for the model to denoise the sky.

A brightness gate is the wrong fix: measured background luma was sky 27.5, olive
trees 24.3, stone arches 40.4 DN, so the dark foreground that most wants detail
is as dark as the sky. The right discriminator is **edge continuity, with the
point sources killed by a 5 px median FIRST**, because without the median a
dense field of faint stars scores exactly like texture.

---

## 5. What an upscaler is not allowed to touch

An enlargement changes pixels. It changes nothing else, and every one of these
has been silently broken by a real tool:

- **The clock.** Same rate, same frame count, same duration, same constant
  timing, verified on the delivered file.
- **The sound.** Most upscalers drop audio without a word.
- **The colour tags.** Primaries, transfer, matrix and range. A hash proves the
  pixels and cannot see any of them.
- **The bit depth.** Native depth always; a deliverable is never reduced.
- **The frame size, downwards.** A smaller copy is a viewing copy, it is
  labelled as one, and it keeps the source's frame size.
- **The shape.** Both axes scale by the same factor. Circles in the source are
  the test.

`upres.py verify` checks all six before it looks at a single pixel, and it
REFUSES the per frame comparisons outright when the clock differs, because frame
N of a 25 fps file and frame N of a 30 fps file are different moments of the
world and every number computed across them is meaningless. Failing the
ambiguity is the point; absorbing it would produce a plausible wrong answer.

---

## 6. The routes

| route | fixes | cost | stage | licence |
|---|---|---|---|---|
| leave it alone | nothing | free | n/a | n/a |
| Lanczos resample | nothing, moves detail onto a bigger grid | free | 10 | n/a |
| faithful hybrid, STILLS ONLY | softness on edges and lettering | free, ~4 s a megapixel at 2x | 10 | BSD-3 |
| video restoration at size | compression damage, softness, noise, mush | free but heavy | 3 | Apache 2.0 |
| Resolve Super Scale | resolution, temporally aware | Studio licence | 10 | commercial |
| generative reimagine | flaws that are CONTENT | credits per frame | 3 | per model |

**Always run the neutral resample as the control in the same pass.** If the
clever route cannot beat plain Lanczos on the measurements in `verify`, it is
not earning its risk, and on a clean source the honest gap is smaller than the
argument about it.

**No upscaler fixes content.** Garbled lettering, wrong geometry, a hand with
six fingers: none of those is a resolution problem, so no amount of resolution
touches them. That is a repaint, it costs credits, and on a CLIP it is not a
route at all because independently repainted frames do not cut together.

### Ground: the video restoration route on Apple silicon, read 2026-08-23

SeedVR2 is a one step diffusion transformer for video restoration, ICLR 2026,
**Apache 2.0**, which is clean for paid client work with no permission to seek.

The reference implementation is not the route on this class of machine. Its own
repository states that **one H100 80G handles 100 frames at 720x1280, and 1080p
and 2K need four of them**. The Apple silicon route is the MLX port
(`mlx-community/SeedVR2-3B-mlx`, transformer fp16 about 7.9 GB, 8.44 GB total,
with an int8 variant), driven through `mlx-gen`, which preserves the source
frame rate and the matching audio by default and fails rather than publishing a
silently misaligned result.

Two things about it decide how it is used here. Its video profile enforces low
RAM mode and **refuses to enlarge video unless a `--force-unsafe-video-memory`
flag is passed**, and that guard exists for the reason it says. And a companion,
SwiftVR, restores at source resolution roughly 40x faster. So on a 24 GB machine
the realistic use is **restoration at size**, which is the stage 3 job anyway,
and enlargement stays a Super Scale job or a stills job.

### Ground: Resolve Super Scale, read 2026-08-23

From `Developer/Scripting/README.txt` inside the installed application, against
Resolve 21.0.4. **Studio only**, and on the free edition a Studio call returns
false rather than raising, so the return value of every Set call is the only
sign of which edition you are on.

    project.SetSetting('superScale', N)          0=Auto, 1=none, 2/3/4 = 2x/3x/4x
    clip.SetClipProperty('Super Scale', N)       1=none, 2/3/4 = 2x/3x/4x

The project setting has an Auto value and the clip property does not. For 2x
Enhanced, **exactly four arguments** or it silently falls back to plain 2x:

    clip.SetClipProperty('Super Scale', 2, sharpness, noiseReduction)

both floats in 0.0 to 1.0. Set it on the media pool item BEFORE the clip is cut
in: it is a decode side setting, so it feeds everything downstream including the
grade. Resolve will not tell you whether the result boils or whether it invented
detail. Bring the render back to `upres.py verify`; that measurement is this
skill's job, not Resolve's.

---

## 7. A still lifted from a clip is never upscaled from that one frame

Neighbouring frames sample the subject at different sub pixel phases, so
iterative back projection over about thirteen aligned frames recovers real
detail that no single frame model can. The proof is a hold out: a build made
from the even offsets predicts the ODD frames better than a plain Lanczos
enlargement does. Measured 15 to 18 per cent better, on 12 of 12 frames.

Two traps that came with it: thin parts escaping the matte leave a travel band
in a fused background plate, and a plate swap can delete a real contact shadow.

---

## 8. What is delegated, and what is deliberately not here

**Delegated.** The `upscale` skill owns Real-ESRGAN and the faithful hybrid for
STILLS, and it is the right tool there. The `davinci-resolve` skill owns the
connection to Resolve. The `colorgrade` skill owns frame repair. This department
owns the ORDER, the routing decision and the proof.

**Not here, on purpose.** No restoration model is installed by this skill. The
weights are gigabytes, the right one changes every few months, and a skill that
pins a model pins a mistake. The route is named with its licence and its memory
behaviour, and everything downstream of the render is measured here.

No sharpening. It is not a resolution operation, it is a contrast operation on
an edge band, and it is a grading decision that belongs to a colourist with eyes
on a calibrated display.
