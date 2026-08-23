# Sources, with dates and verification state

Every figure this skill states carries a source. A figure with no source is a
gap, and a gap is acceptable. A fabricated figure is a critical failure, which
is why this file exists and why `standards.py freshness` nags about it.

Two states only:

- **READ**: the primary document was fetched and read on the date given, and the
  numbers quoted are the document's own.
- **[VERIFY]**: the figure came from a secondary source or from memory and must
  be confirmed in the primary text before it is used on paid work.

---

## READ in the primary text

**EBU R 128, version 5, Geneva, November 2023.**
tech.ebu.ch/publications/r128, PDF read in full 2026-08-23.
Target -23.0 LUFS; the plus or minus 1.0 LU tolerance applies only where the
target is not practically achievable, for example live programmes; plus or minus
0.2 LU allowed for measurement error in QC; true peak not above -1 dBTP in
production with a plus or minus 0.3 dB measurement tolerance; level gated per
ITU-R BS.1770 equation 7, relative gate -10 LU since v2; measured in its
entirety without emphasis on speech; supplements s1 short form, s2 streaming, s3
radio, s4 cinematic.

**EBU R 95, Revision 1, July 2016.**
tech.ebu.ch/files/live/sites/tech/files/shared/r/r095-2016_2.pdf, read 2026-08-23.
Action safe inset 3.5 per cent per edge, graphics safe 5 per cent per edge, so
93 and 90 per cent of the raster, concentric. The document's own errata confirms
3.5 per cent of 1920 is 67 pixels. Per raster first and last safe line and pixel
numbers live in the figures as images and should be read from the PDF for a
broadcast deliverable.

**Netflix Sound Mix Specifications and Best Practices v1.3.**
partnerhelp.netflixstudios.com, fetched 2026-08-23.
-27 LKFS, plus or minus 2 LU, dialog gated; true peak not above -2 dBTP;
measured over the full programme per ITU-R BS.1770-1; the same figures for
original mixes, dubs and audio description.

**Netflix English Timed Text Style Guide.**
partnerhelp.netflixstudios.com, fetched 2026-08-23.
42 characters per line; maximum two lines; 20 characters per second for adult
programmes and 17 for children's. Durations are in the separate General
Requirements: minimum five sixths of a second, maximum 7 seconds.

**W3C TTML Profiles for Internet Media Subtitles and Captions (IMSC) 1.2.**
w3.org/TR/ttml-imsc1.2/, fetched 2026-08-23.
W3C Recommendation of 4 August 2020. Defines a Text Profile and an Image
Profile; a document cannot conform to both.

**OpenTimelineIO.**
pypi.org/pypi/OpenTimelineIO/json, read 2026-08-23.
Version 0.18.1, Apache 2.0, requires Python above 3.9.

**ffmpeg 9.0.1** is what is installed on this machine, checked 2026-08-23. Every
ffmpeg behaviour this skill relies on was measured against that build.

---

## Measured on this machine rather than cited

These are not standards. They are calibrations, made here, with the date, so
that a later reading can be compared with them.

**Bit depth lattice separation, 2026-08-23, remeasured the same day.** An 8
bit test pattern promoted to yuv422p10le and written as ProRes HQ kept 90.98 per
cent of its luma samples on the multiple of 4 lattice, over 594 distinct codes.
A ramp written at 10 bit steps and put through the same encode sat at 25.00 per
cent, which is the chance level exactly. A lossless promotion carried 20 codes
with a gcd of 4, and 20 codes with a gcd of 1 read as native. This is what
`spec.py depth` reads, and the four clips are the controls in `selftest.py`.

The first version of this calibration read 95.2 and 31.5 per cent, and both
figures came from clips written with `-colorspace bt709` against an untagged
source, which converts rather than tags. The promoted clip measured after that
conversion reads 31.75 per cent, so the two numbers were closer to each other
than either was to the thing being measured. Tag the source with `setparams`
and the reading is reproducible on any build. See failure 43.

**Scaler contamination, 2026-08-23.** The same 10 bit ProRes clip reports 805
distinct luma codes read in its own pixel format and 27946 read through
gray16le, because the scaler range converts and dithers. Hence the rule that a
depth measurement must be taken in the file's own format.

**Colour tag change at a splice, 2026-08-23.** Two ProRes segments, one tagged
bt709 and one written with no colour flags, concatenated with `-c copy`, produce
a master whose declared matrix changes at exactly the join frame. Reproduced
here to test `prove.py tags`.

**ProRes slice quantisation, 2026-08-23.** On a 320x180 test film, a change
confined to rows 20 to 60 left a region at row 120 BIT IDENTICAL across a full
re-encode, which is the slice behaviour described in failure 3 showing up in a
controlled case.

**Effective resolution knee, 2026-08-23.** The fit band for the power law was
CALIBRATED here, not chosen. At 0.06 to 0.30 of Nyquist a real lens roll off
sits inside the fit band and the tool measures the blur through the blur: log
log curvature in dB per decade squared read -0.3 native, -7.5 at lens sigma 0.8,
+14.3 on a frame blurred past measurability. Narrowed to 0.03 to 0.16 with 128
radial bins the same cases read +1.2, -0.7 and +254.2, so every honest case sits
inside 3.1 and the unmeasurable one two orders outside it. Resampler bias, the
amount by which the knee overstates the source's own Nyquist, measured 1.05 to
1.14 across Lanczos, bicubic and bilinear at 2x, 3x, 4x and 1.5x, which is why a
source raster is matched by `ratio <= knee <= 1.25 * ratio` rather than by
proximity. Reproduced on a real H.264 encode: a 960x540 source read CARRIES at
knee 1.00 and its own Lanczos 2x read SHORT at 0.566, naming 960x540.

**Pixel doubling lattice, 2026-08-23.** A nearest neighbour enlargement is
invisible to the spectrum, because it replicates rather than band limits: knee
0.99 against the Lanczos version's 0.57. The exact test is the parity of
neighbouring column differences, measured 0.000 on a nearest 2x, 0.860 on a
Lanczos 2x and 0.998 on a native frame.

**Optical flow sign convention, 2026-08-23.** OpenCV's `calc(f, g)` returns F
with `f(y,x) ~ g(y+Fy, x+Fx)`, so the field that resamples `src` into `dst`'s
frame is `calc(dst, src)`. On an exact seven by three pixel translation with
known ground truth: 0.0017 mean absolute error the right way round, 0.0824
reversed, and 0.0824 is indistinguishable from two unrelated frames. Failure 36.

**Temporal statistics, 2026-08-23.** Detail incoherence lands on 1/sqrt(2) for
two independent fields by construction, measured 0.6864 against the predicted
0.7071. On real encodes of a real pan: a Lanczos 2x reads +0.0005 excess over
the control, a downscale +0.0002, a per frame model +0.6027. The warping error
RATIO on the same three files reads 1.12, 1.29 and 669.08, which is why the
verdict runs on the bounded statistic and not on the ratio. Failure 37.

**Two OpenCV majors agree, 2026-08-23.** Every resolution figure above was
calibrated under OpenCV 5.0.0 in this skill's environment and re-read under
OpenCV 4.13.0 on the system interpreter. The spectral reading was identical to
four decimal places (knee 0.5664, effective raster 1088x612 on the same encode)
and the temporal excess differed in the fourth decimal (0.6025 against 0.6028).
The thresholds are therefore not resting on one library build.

**CIEDE2000**, checked against the twelve published reference rows, worst
deviation 4.2e-5. The colorgrade skill on this machine is checked against the
same table, so two independent implementations here agree with one external
source.

**Font metrics**, cross checked against fontTools 4.62.1 on a system face: units
per em, ascender, descender, cap height, total advance and ink box all agree
exactly.


---

## READ in the primary text: compositing

**Alvy Ray Smith and James F. Blinn, "Blue Screen Matting", SIGGRAPH 1996,
pages 259 to 268.** PDF read in full 2026-08-23 from the CMU 15-463 course
copy, graphics.cs.cmu.edu/courses/15-463/2005_fall/www/Papers/smith-blinn.pdf.

The proof that constant colour matting is underspecified: "A complete solution
requires Ro, Go, Bo, and alpha. Thus we have three equations and four unknowns,
an incompletely specified problem and hence an infinity of solutions, unsolvable
without more information." The Vlahos Assumption, `Bo <= a2 Go` with the usual
range `0.5 <= a2 <= 1.5`, attributed to Vlahos's patents. The First Vlahos Form
`alpha = 1 - a1 (Bf - a2 Gf)`, clamped, abstracted from the earliest electronic
patent. Solution 1 (no blue), Solution 2 (grey or flesh, with flesh at roughly
`[d, 0.5d, 0.5d]` across all races), Solution 3 (triangulation) and the three
theorems that generalise them. Theorem 3, used verbatim by `comp.py
triangulate`: `alpha = 1 - (sum(Cf1) - sum(Cf2)) / (sum(Ck1) - sum(Ck2))`,
valid whenever the two backings' coordinate sums differ, the backings needing to
be neither constant nor clean. The paper's own note that derivations with non
premultiplied alpha "are not so elegant".

**Georgios D. Evangelidis and Emmanouil Z. Psarakis, "Projective image alignment
by using ECC maximisation", VISAPP 2008, pages 413 to 420,
DOI 10.5220/0001087204130420.** PDF read in full 2026-08-23 from
scitepress.org. The long form is the same authors' "Parametric image alignment
using enhanced correlation coefficient maximization", IEEE TPAMI 2008 [VERIFY],
which is the paper OpenCV cites as EP08 for `findTransformECC`.

The objective `E(p) = || ir/||ir|| - iw(p)/||iw(p)|| ||^2` on zero mean
normalised vectors, "invariant to possibly existing contrast and/or brightness
changes since involved vectors are zero-mean and normalized". Theorem I and
Lemma I give the closed form optimal perturbation per iteration. The eight
parameter projective model and its parameter dependent Jacobian, which must be
recomputed each iteration. The paper's own warning, which is why `track`
initialises from the previous frame: "the convergence of the proposed algorithm
can critically be affected by the values of vector p0 when the images overlap by
a small amount". Compared against forward additive Lucas-Kanade, ECC is better
in speed and in probability of convergence, including under over modelling of
the warp.

**Zhengyou Zhang and Li-wei He, "Note-taking with a camera: whiteboard scanning
and image enhancement", ICASSP 2004, pages III-533 to III-536.** PDF read
2026-08-23 from microsoft.com research. The claim this skill relies on, in the
authors' own words: "since we know that it is a rectangle in space, we are able
to estimate both the camera's focal length and the rectangle's aspect ratio".

The journal version is Digital Signal Processing 17(2), April 2007, pages 414 to
432. **The derivation itself is in the accompanying technical report
MSR-TR-2003-39, which was NOT reachable on 2026-08-23** (Microsoft Research
returns 403 for the archived PDF). `_geom.rectangle_aspect` therefore implements
the standard construction from the orthogonality of two vanishing points under
the image of the absolute conic, and it is validated against SYNTHETIC GROUND
TRUTH in `selftest.py` rather than transcribed from the report: a 16:9 rectangle
projected at a known focal length comes back at 1.7778 and the focal length to
0.1 px. That validation is stronger evidence than the citation would have been,
and the citation is here for the idea, not for the algebra.

**Ben McEwan, "Deconstructing Despill Algorithms",
benmcewan.com/blog/understanding-despill-algorithms.** Fetched 2026-08-23. The
five named limits used by `comp.py despill`, in his notation for a green screen:
average `(r+b)/2`, double blue `(2b+r)/3`, double red `(b+2r)/3`, blue limit `b`,
red limit `r`. His own note that the blue limit is "the darkest and produces the
least-desirable results as-is", and that despill "make[s] the background turn
dark", which is the cost `--preserve-luma` exists to return.

**Ben McEwan, "Re-Graining Your Comp Using Existing Plate Grain",
benmcewan.com/blog/2018/05/29/re-graining-your-comp-using-existing-plate-grain.**
Fetched 2026-08-23. Grain is "the difference between the plate with grain, and a
denoised version"; it is added back with a Plus, not multiplied; the original
grain is keymixed back over the areas that already have it so it does not double;
and where the lighting is uneven a luma key modulates it, brighter in the dark
areas and darker in the bright ones, "to mimic realistic grain response". His
advice to examine the blue channel first when denoising, as usually the
strongest, is the reason `grain` reports sigma per channel.

---

## [VERIFY] before use on paid work

- **SMPTE ST 12-1** timecode, including the drop frame definition. The
  arithmetic in `conform.py` is instead proved by round trip and by the
  well established anchors (107892 frames in a drop frame hour, 2589408 in a
  day), which is a weaker grounding than reading the standard.
- **SMPTE ST 2046-1** safe areas, said to agree with R95.
- **SMPTE ST 2067** IMF family; **SMPTE RDD 59** IMF Application DPP.
- **AS-11 UK DPP**, thedpp.com/specs, including the 1 October 2014 date.
- **ATSC A/85**, the -24 LKFS US broadcast target.
- **OpenColorIO** version numbers for ACES 2.0 support (said to be 2.4.2 for
  support and 2.5 for the built in configs), opencolorio.readthedocs.io.
- Whether an **IMSC text profile later than 1.2** has advanced at w3.org.
- **CMX 3600 EDL** conventions beyond what `conform.py` parses.
- Per language timed text guides for any language other than English.
- Every platform delivery specification, always, at the moment of delivery.
- **Thomas Porter and Tom Duff, "Compositing Digital Images", SIGGRAPH 1984.**
  The `over` operator and the premultiplied form the operators are defined in.
  Universally reported as such and implemented that way everywhere, but the
  paper itself was not fetched on 2026-08-23. The arithmetic in `_pix.over` is
  instead proved by round trip and by the triangulation test, which recovers a
  known foreground through it to 1.2e-07.
- **Daniel Barath et al., "MAGSAC++, a Fast, Reliable and Accurate Robust
  Estimator", CVPR 2020.** Used by `comp.py track` through OpenCV's
  `USAC_MAGSAC`. The claim relied on is that it is markedly less sensitive to
  the inlier-outlier threshold than plain RANSAC; the abstract was read but not
  the paper.
- **SAM 3** (Meta, released 2025-11-20) and **SAM 3.1** (2026-03-27), read on
  2026-08-23 from Meta's own SAM 3.1 announcement and the gated Hugging Face
  model card. Figures relied on: promptable concept segmentation from a text
  prompt or image exemplar returning every instance; Object Multiplex tracking
  up to 16 objects in one forward pass; 32 fps on a single H100, up from 16;
  about 30 ms for one image with more than 100 objects on an H200; near real
  time to about five concurrent objects in video; base checkpoint 0.9B
  parameters; weights gated behind a contact form; Meta's own SAM License,
  commercial use permitted with military, ITAR, nuclear and weapons carve outs.
  Announcement page read; no weights run and no benchmark reproduced here.
  Supersedes the SAM 2 reference this document carried until 2026-08-23.
- **MatAnyone (CVPR 2025) and MatAnyone 2 (CVPR 2026)** as the 2026 state of the
  art for learned video matting with memory propagation, driven from a SAM 3
  segmentation. Named in `reference/06_compositing.md` section 10 as the HOW MUCH
  half of a roto job. **Licence: S-Lab License 1.0, non commercial use only;
  commercial use requires written permission from the authors.** That licence
  status was checked on 2026-08-23 and is recorded because a job can ship before
  anyone thinks to look. Permissive alternatives named there: BiRefNet, ViTMatte,
  SeC, Robust Video Matting. Search results and licence text only; no paper read,
  no weights run, no benchmark reproduced here.
- **DaVinci Resolve 21 scripting README**, read off this machine on 2026-08-23 at
  `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/README.txt`.
  Relied on for `CreateMagicMask(mode)` with modes F, B and BI,
  `RegenerateMagicMask()`, `SmartReframe()`, the `superScale` project and
  `Super Scale` clip properties (enumerated 0 to 4, with 2x Enhanced taking a
  sharpness and a noise reduction float in [0.0, 1.0]), and Blackmagic's own
  statement that a Studio function called from the free version returns False
  rather than raising.
- **The same API, MEASURED rather than read, on 2026-08-23**, from an external
  Python process against DaVinci Resolve Studio 21.0.4 with the licence live on
  this machine: a scratch project created and deleted, a real clip imported and
  cut to a timeline, a Fusion comp opened on it. `Stabilize()` and
  `SmartReframe()` returned True; `CreateMagicMask("F")` returned False in 0.0 s
  with no stroke on the clip; `SetSetting('superScale', '2')` returned False and
  the int form returned True. Fusion's registry carries a `MagicMask` tool whose
  `Strokes` input is datatype `MagicMaskStrokes` and serialises to a plain text
  `.setting`. Failures 41 and 42.
- **SeedVR2**, ICLR 2026, ByteDance, read on 2026-08-23. **Apache 2.0**, so
  clean for paid client work with no permission to seek. Its own repository
  states that one H100 80G handles 100 frames at 720x1280 and that 1080p and 2K
  need four, which is why the reference implementation is not the route on an
  Apple silicon machine. The MLX port `mlx-community/SeedVR2-3B-mlx` is
  transformer fp16 about 7.9 GB, 8.44 GB total, with an int8 variant; driven
  through `mlx-gen` it preserves the source frame rate and the matching audio by
  default, enforces low RAM mode on its video profile, and refuses to enlarge
  video unless `--force-unsafe-video-memory` is passed. A companion, SwiftVR,
  restores at source resolution roughly 40x faster. Repository text, model card
  and CLI documentation read; no weights downloaded and no output measured here,
  so every quality claim about it is UNVERIFIED on this machine.
- **The Brown-Conrady lens distortion model** (radial k1 k2 k3, tangential
  p1 p2). Named as the standard; not implemented and not verified.
- **The often quoted figure that ECC at half resolution costs about 0.13 px.**
  It came from one job on one region. `track` now MEASURES it per plate, and on
  the synthetic screen comp half resolution cost 6.2 px, not 0.13. Treat the
  original figure as an anecdote, not a constant.


---

## How to re-verify

`standards.py freshness` lists every domain with the age of its verification and
flags anything past 120 days. When a domain goes stale, fetch the primary
document again, update the numbers AND the `as_of` date in `standards.py`, and
add a line here.

The most expensive mistake in this department is applying last year's figure
with this year's confidence.
