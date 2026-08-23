# Compositing: tracking, mattes, inserts and the checks that lie

The department where the most convincing wrong answers live.

A composite can be built on a track that is six pixels out and pass every check
somebody runs, because the matte came from the plate's own green and clipped the
result to the right region no matter where the track thought the quad was.
Measured on a synthetic film built for this document, with a track deliberately
offset by 6.3 px: `verify ring` PASSED and `verify content` failed at 8.20 px.
That is the shape of the whole department, and it is why every command here ends
by naming what it did NOT prove.

Two kinds of material below, and they are marked. Sections headed **Ground**
come from primary literature, read on 2026-08-23, and are stated with their
source. Sections headed **Measured here** were reproduced against synthetic
ground truth while `comp.py` was built, so the numbers are this machine's own
and are reproducible with `selftest.py`. Everything else is the general form of
failures 17 to 27, which happened on real films.

Engine: `scripts/comp.py`. It needs numpy, OpenCV and SciPy in this skill's own
environment, never the bot's, and it is run with `~/.venvs/post/bin/python`.

---

## 0. The order, and why it is the order

    cadence  ->  track  ->  quad  ->  aspect  ->  key  ->  warp  ->  verify

**Cadence first, before anything is tracked.** A generated plate is very often a
higher rate conformed down by dropping pictures, and the lurch that leaves is
real motion the composite has to reproduce. Track it as noise and the content
slides against the real bezel on exactly that beat.

**Quad after track, not instead of it.** A track says how the surface moved. An
outline says where its edges are on one frame. They answer different questions
and the second cannot replace the first.

**Aspect before the artwork is laid out**, because the artwork has to be authored
at the panel's anisotropy, and finding that out after the artwork exists means
re-authoring it.

**Verify last, on the DELIVERED file**, and anchored in the plate.

---

## 1. Tracking

### Ground: what ECC is and why it, rather than a least squares residual

`comp.py track` solves by Enhanced Correlation Coefficient maximisation
(Evangelidis and Psarakis, *Projective image alignment by using ECC
maximisation*, VISAPP 2008; the same authors' TPAMI 2008 paper is the long
form). The objective is

    E(p) = ||  i_r/||i_r||  -  i_w(p)/||i_w(p)||  ||^2

on ZERO MEAN, normalised intensity vectors, which is why it is invariant to a
change of gain and a change of offset in the picture. That matters on a real
plate: the lamp flickers, the exposure ramps, a cloud passes, and an ordinary l2
residual chases the brightness instead of the motion. The paper's own comparison
is against forward additive Lucas-Kanade, and ECC wins on speed and on
probability of convergence, including when the motion model is richer than the
true motion.

The paper's own warning is the one that matters in practice: convergence is
critically affected by the initialisation when the images overlap by little.
`track` therefore initialises each frame from the PREVIOUS frame's answer while
solving against the ORIGINAL reference frame. Chaining frame to frame instead
accumulates error with nothing in the result to say how much.

### Never smooth with a model that cannot represent the motion

A cubic fitted per corner across a clip has four degrees of freedom for the
whole film. Real plate motion is usually a SETTLE, and a cubic cannot represent
a settle: it deletes the motion and substitutes a monotonic slide. Measured cost
on one job, 6.91 px worst error, and the client saw it by eye.

`_track.smooth` refuses `method="poly"` outright, and refuses to run at all
without a true time vector. Smoothing is safe only when the model can represent
the true motion; otherwise it is invention.

**Measured here.** A damped oscillation, `10 e^(-t/6) cos(t/2)`, plus noise: a
Savitzky-Golay filter of window 7 and order 2 against true time tracks it to
0.79 px worst. The residual it reports is against the RAW values, never against
its own output.

### Take the rigid motion from the object itself

ECC alignment on a mask over the object's body, then carry a reference frame
quad along it. Nothing fitted through time means nothing real can be smoothed
away.

**Measured here.** Twenty frames, per detection noise of 0.4 px. Using each
frame's raw detection: 0.448 px mean error. Pulling every frame's detection back
through its OWN homography and averaging: 0.155 px. Nothing in that average
varies with frame number, so it is not a fit through time. On a real job the
same step cut scatter from 0.61 px to 0.08 px.

`comp.py holdout` is the gate: rebuild the shape with frame k left out, predict
frame k, compare with what it measured itself.

### Pick the model from the plate's dominant motion, and MEASURE which

If the object travels, affine may hold. If it is LIFTED toward the lens, the
keyed region grows while its centre barely moves, and affine cannot represent
that at all: the solve has to be a homography.

`track --model auto` cross validates on the correspondences: fit on four fifths,
measure on the fifth left out, rotate, and take the SIMPLEST model whose held out
error is within one standard error of the best. A model that is too rich fits
the noise and its held out error goes UP, which is the only way to tell over
modelling from real motion.

**Measured here**, on the synthetic screen comp, held out median error:

    translation  15.58 px      affine       8.31 px
    euclidean    14.51 px      homography   2.12 px

The plate genuinely needs a homography, and the numbers say so rather than
somebody's judgement saying so.

**When the model cannot be measured, `track` REFUSES and asks.** It does not
default. A motion model is a fact about this plate, not a constant, and the same
rule applies as everywhere else in this skill: a standard's constant may default,
a project's fact may not.

### The solve settings are a measurement too

The often quoted figure that ECC at half resolution costs about 0.13 px was
measured on one job on one region. `track` measures it here, on this plate,
by solving a handful of sample frames at every setting on a ladder and comparing
each against the full resolution answer at the region's own corners.

**Measured here**, on the synthetic screen comp:

    scale 1.0  blur 3   cc 0.953   cost vs full   0.000 px   <- chosen
    scale 1.0  blur 5   cc 0.990   cost vs full   0.664 px
    scale 1.0  blur 1   cc 0.878   cost vs full   8.127 px
    scale 0.5  blur 3   cc 0.987   cost vs full   6.199 px
    scale 0.5  blur 5   cc 0.975   cost vs full  15.680 px
    scale 0.25 blur 3   cc 0.662   cost vs full 295.314 px

Half resolution cost SIX PIXELS on this region, not 0.13. Taking the published
figure on trust would have shipped it.

Note also that the correlation coefficient does not rank the settings: 0.990 at
blur 5 is the highest cc in the table and it is not the best setting. cc says
whether a solve found the object at all; it does not say how precisely.

### Two independent measurements agreeing is what certifies a track

One measurement plus a threshold certifies nothing. `track` therefore solves a
sample of frames twice by routes that share no assumption, dense photometric
(ECC) against sparse geometric (SIFT correspondences plus MAGSAC++), and the
certificate is the disagreement in pixels at the region's own corners.

MAGSAC++ (Barath et al., CVPR 2020) is used where OpenCV has it, because it is
markedly less sensitive to the inlier threshold than plain RANSAC, and the right
threshold depends on the plate's grain, which nobody knows in advance.

Three things about that certificate, each of which cost a debugging session
while it was built:

**The reference frame is never certified.** Comparing the reference with itself
is two ways of computing the identity, and it always agrees to 0.000 px. A
certificate that reports one frame at 0.000 px is reporting nothing.

**A feature solve is only a measurement if it has redundancy.** Four points
determine a homography exactly, which is precisely why four points do not
MEASURE one: with no redundancy there is no residual, so nothing says whether
the answer is right. `min_support` demands three times the model's degrees of
freedom, so 24 inliers for a homography. Below that the route is declared
unavailable rather than believed. Skipping this produced a certificate reading
321.98 px on a track that ground truth put at 1.72 px, which is the worst kind
of check: one that fails correct work.

**A weak route and a strong route are never averaged.** If the strong route ran
on any frame, that is the certificate; the weak one is reported beside it and
labelled.

### The blind spot every feature based check has

A surface with no texture is invisible to a feature detector, so "all the
correspondences fit one motion" can be perfectly true while the surface you
actually meant to track goes somewhere else entirely. That is failure 19 in its
general form, and a green panel inside a tracked box is exactly this case.

`track` measures it: gradient energy inside the region, against the plate's OWN
noise floor rather than an absolute, and reports the flat fraction. Against an
absolute this test is useless, because every real plate has grain and grain has
a gradient: a flat green panel covered in grain reads as full of texture, which
is the opposite of the truth.

The practical consequence, which is also standard planar tracking practice: track
a shape LARGER than the screen, so the solve has the bezel and the surround to
hold on to. But only where that surround is RIGID with the screen. A phone body
is; the room behind it is not.

**Measured here.** Tracking a box that included both the panel and the room
behind it chose `translation` from the background's features and landed 24.23 px
median from ground truth. The correspondences were 81 per cent inliers to a
single planar motion, so the one surface test could not see the problem: the
second body brought no features at all. The certificate caught it at 65 px. That
is the whole argument for layered checks.

### A collapsed solve, detected from the warp alone

Not a temporal smoothness test, which would be an assumption about the motion.
These are things a projective view of a rigid quadrilateral simply cannot do:

- turn its winding around (a mirrored solve is looking at the BACK of the panel,
  and a homography search can land there with a respectable correlation on a
  symmetrical shape);
- cross its own edges;
- change area by an order of magnitude;
- land most of a picture away from the picture.

`warp_plausible` refuses all four, per frame, and a refused frame is marked
UNSOLVED and is deliberately NOT used to initialise the next one. One bad solve
accepted becomes the starting point for every frame after it: on the synthetic
plate, accepting a single bad fallback took a track from 2.5 px to 330 px.

---

## 2. Occlusion, and the difference between a bite and a corner

Hulling a keyed region stops a finger biting into an EDGE from pulling a least
squares fit inward, and that reasoning is sound only for a MID EDGE occluder: the
bridge lies along the straight edge the bite came out of, so nothing is invented.
When the occluder removes a CORNER, the bridge is a chord across the gap and the
fit follows the chord.

**Declare a frame's edge UNMEASURED unless its own scanlines say otherwise.**
`comp.py quad` casts scanlines perpendicular to each nominal edge and finds the
sub pixel crossing of the matte; a scanline that never straddles the level
returns NOTHING rather than a wrong answer, which is how an occluded stretch
declares itself.

Four conditions, all necessary: enough scanlines returned a crossing; they fit a
line; they span enough of the edge; and **each END of the edge is reached**. The
last one is the one people leave out and it is the one that matters. A corner
removal leaves an edge that still spans most of its length, still fits a line
beautifully, and has simply stopped short of the corner everyone is about to read
off it.

The number `quad` reports per corner is how far past the outermost real sample
the corner is being read. That is inset independent and it is the honest
quantity: a corner is a measurement only as far as there is data reaching toward
it, and anything beyond that is extrapolation from a fitted line.

**Measured here.** On a 400 px edge, a 52 px bite out of the MIDDLE leaves all
four corners MEASURED. A 68 px occluder over a CORNER leaves exactly that corner
UNMEASURED, read 28 px past the last real sample against an expected 13 to 23.

For a hidden edge, the object is rigid, so place the corners from the population
and the frame's own measurements, and **prove it by hold out or do not ship it**:
`comp.py holdout` re-measures the shape with each frame left out, predicts that
frame, and compares with what it measured itself.

---

## 3. Curved surfaces

A curved panel is concave toward the camera, so its ends stand nearer the lens
than its middle, and exactly one projected edge bows INTO the shape. Any
convexifying step replaces that edge with its chord: measured 41 to 48 px of
error on the bottom edge alone on a real job, while the other three read 0.0.

Emit an ORDERED closed ring for anything that fills or masks. `ring_from_mask`
fits each edge in its own frame, reports its bow, and samples the ring along
those fits, so a bowed edge stays bowed.

A flat panel is immune, because its projected quad is convex and a hull is a
no-op, so this fault only ever appears on curved glass. **`quad` therefore
reports the hull cost on every mask**, measured on the MASK'S OWN CONTOUR rather
than on the ring it rebuilt: rebuilt from straight fits the ring is convex by
construction, so asking it what a hull would cost always answers zero, and the
one shape that needs the warning never gets it.

Two numbers, because one of them lies: the deepest point inside the hull, and how
MANY points are more than 2 px inside. A jagged, anti aliased outline puts a
handful of points a pixel inside its own hull and that is not a curved panel; a
real bow puts a long run of points well inside it.

**Measured here.** A flat panel: 1.0 px over 0 points. A panel bowed by 22 px:
22.4 px over 28 per cent of the outline.

Cast a polygon to int32 and it truncates, dragging the whole shape up to a pixel
toward the origin; measured at 0.50 px of centroid shift on a 30 px square. Fill
with rounded sixteenth pixel coordinates and a shift, which is
`fill_poly_subpixel`.

---

## 4. Aspect: two different questions, and only one of them is always answerable

### The image anisotropy R, which the outline always determines

    R = |dP/du| / |dP/dv|          area weighted over the panel

This governs the SHAPE of anything drawn on the panel. Lay the content out at R.
A circle authored in the source then renders at `w/h = R / (canvas aspect)`,
which is 1 when the canvas aspect IS R.

**Measured here.** Force the solve through assumed aspects of 1.30, 1.60, 1.78,
2.20 and 2.60 on the same outline: every one fits the boundary to 5.5e-05 px, and
R comes back 1.7058 for every one. R is an invariant of the outline. The assumed
aspect is not, and the solver trades it against yaw.

### The rectangle's TRUE aspect, which sometimes it does not

**Ground.** Because the shape is known to be a rectangle in space, one
perspective view of its four corners determines BOTH the camera focal length and
the rectangle's aspect, assuming square pixels and a known principal point. The
method is Zhang and He's; it is stated in *Note-taking with a camera: whiteboard
scanning and image enhancement* (ICASSP 2004) and *Whiteboard scanning and image
enhancement* (Digital Signal Processing 17(2), 2007), with the derivation in the
accompanying Microsoft Research technical report MSR-TR-2003-39, which is no
longer publicly reachable. The implementation in `_geom.rectangle_aspect` was
therefore derived from the standard construction and VALIDATED against synthetic
ground truth rather than transcribed:

    k2 = ((m1 x m4) . m3) / ((m2 x m4) . m3)      k3 = ((m1 x m4) . m2) / ((m3 x m4) . m2)
    n2 = k2 m2 - m1                                n3 = k3 m3 - m1
    f^2 = -[ (n2x - u0 n2z)(n3x - u0 n3z) + (n2y - v0 n2z)(n3y - v0 n3z) ] / (n2z n3z)
    aspect^2 = (n2' w n2) / (n3' w n3)             w = A^-T A^-1

where n2 and n3 are the vanishing points of the two edge directions.

**Measured here.** On a synthetic 16:9 panel projected at f = 1400 px with real
perspective: aspect 1.7778 against a true 1.7778, focal 1400.0 against a true
1400. On a 2.35 panel at f = 2200: exact. On a square panel: exact.

**And this is where the earlier note in this file was too strong.** It said the
aspect is a gauge the outline cannot pin. That is true of the plate it was
written on and it is not true in general. Three cases, and `rectangle_aspect`
returns which one it is in:

- **DETERMINED.** Both vanishing points are finite and close enough that the
  solve has grip. The number is real. It still comes with the band that half a
  pixel of corner noise moves it over.
- **FOCAL_FREE.** One family of edges stayed parallel in the picture, so its
  vanishing point is at infinity, orthogonality holds for EVERY focal length, and
  the view says nothing about the lens. Supply the lens and the aspect follows
  exactly; without it the aspect is free.
- **UNDETERMINED.** Both vanishing points ran off. The view is effectively
  affine and the outline carries no information about the true aspect at all.
  Measured: a 16:9 panel at f = 9000 with 0.02 rad of tilt puts its nearest
  vanishing point 136 pictures away, and the aspect can be anything.

The last case is the one that produced the original lesson, and the real point of
that lesson survives intact: **a solve can be right on the boundary and wrong in
the interior, and a green key hides it completely.** Whatever the verdict, verify
on the DELIVERED file against something whose true shape you know. Round UI
elements are ideal. Best of all, look for a reference the film already contains,
such as the same screen's closeup elsewhere in the cut.

---

## 5. Mattes and keys

### Ground: constant colour matting is UNDERSPECIFIED, not merely hard

Smith and Blinn, *Blue Screen Matting*, SIGGRAPH 1996, prove it. The matting
equation `Cf = Co + (1 - a) Ck` is three equations, one per primary, in four
unknowns, the three foreground primaries and alpha. There is an infinity of
solutions and no algorithm can choose between them without an extra assumption:

> "A complete solution requires Ro, Go, Bo, and alpha. Thus we have three
> equations and four unknowns, an incompletely specified problem and hence an
> infinity of solutions, unsolvable without more information."

Every keyer therefore ships an assumption, and the useful question is not "is the
key good" but "where does this plate break the assumption I am keying it with".
The paper names the working assumption behind the whole colour difference family
as the **Vlahos Assumption**, `Bo <= a2 Go` with roughly `0.5 <= a2 <= 1.5`, and
gives the First Vlahos Form abstracted from the patent as

    alpha = 1 - a1 (Bf - a2 Gf)

clamped to 0 and 1. The paper's Solution 2 shows why it works as often as it
does: a solution exists whenever `Ro = a Bo + b alpha` or `Go = a Bo + b alpha`,
which covers grey (a = 1, b = 0) and covers flesh, which across all races holds
roughly the ratio `[d, 0.5d, 0.5d]`.

`comp.py key` reports its own violation map on every run: which foreground pixels
carry more backing colour than the assumption allows. Those pixels cannot be
separated and tuning will not fix it, because the information is not in the
frame. Roto them, relight them, or accept the loss, and say which.

### Ground: the exact case, when it is available

Smith and Blinn's Theorem 3: if the same uncomposited foreground is known against
two backings whose colour coordinate sums differ, the problem stops being
underdetermined and has ONE answer.

    alpha = 1 - (sum(Cf1) - sum(Cf2)) / (sum(Ck1) - sum(Ck2))
    Co    = Cf1 - (1 - alpha) Ck1

The backings do not have to be constant, or clean, or even the same hue; they
only have to differ. In a studio the cheap version is one pass with the cyc lit
and one with it dark. `comp.py triangulate` implements it, and marks as UNSOLVED
rather than guessing any pixel where the two backings happen to be identical.

**Measured here.** Alpha recovered to 1.9e-07 and the foreground colour to
1.2e-07, against ground truth, including through a soft edge.

### Screen correction: measure the backing level, do not dial it

The First Vlahos Form's `a1` is a knob for the backing's level. Replace it with a
measured FIELD and the key stops eating one side of the cyc to clear the other.
`key --method difference` estimates the backing's own level per pixel from the
pixels that are unambiguously backing, or exactly from a `--clean-plate` where
one exists. This is what Ultimatte's Advantedge does with a clean plate input.

### Two keys unioned beat either alone

On a green stage: a difference key keeps a whole silhouette including tyres
sitting in their own shadow but drags a ragged smear where the shadow falls on
the floor; a residual against a FITTED backing colour model gives a clean body
but eats anything picking up green bounce, because bounce really is the backing's
colour. Plain chromaticity fails the same way as the second.

The model key fits the backing as a low order polynomial field in x and y over
seed pixels, then asks of every pixel whether it is that field scaled by some
brightness:

    k     = (S . F) / (F . F)
    resid = |S - k F| / (|F| max(k, kmin))

A pixel that is only the backing in shadow has a small k and a tiny residual,
which is how a body's own shadow on the floor stays background while the body
does not.

**Measured here.** A green jacket panel on a foreground: the difference key gives
it alpha 0.003 and eats it; the model key gives it 1.000; the union gives it
1.000 and still clears the backing at 0.0015. `key --method union` reports the
area where the two disagree, because that region is where somebody has to look.

### The stage and the panel are different films

A green STAGE fills the frame edges, so the backing is whatever touches the
border and anything enclosed is the subject. A green PANEL sits inside the shot
and touches no border, and the border rule finds nothing at all. Getting this
wrong silently inverts the matte. `key_backing_model` looks at where its seed
actually is and prints which rule it used.

### A garbage matte is not a nicety

A key is a statement about a BACKING, and anything else in frame near that
colour, a green exit sign, a plant, a stripe on a jacket, joins the backing as
far as the key is concerned. `key --garbage` or `--region` restricts the seed,
the level estimate and the component search. Without one, `key` says so.

**Measured here.** On the synthetic screen plate the key found 51267 of 51316
true backing pixels, missing 49 and gaining 6, with a garbage matte. Without one
it found the room instead.

### Ground: despill, and its two costs

Every despill is the same shape: clamp the key channel to a limit built from the
other two. The named forms, in the conventions Ben McEwan sets out in
*Deconstructing Despill Algorithms*, transposed to a green backing:

    average        limit = (r + b) / 2
    double blue    limit = (2b + r) / 3
    double red     limit = (b + 2r) / 3
    blue limit     limit = b            the harshest, and the darkest
    red limit      limit = r

Two costs, both reported by `comp.py despill` rather than left to be discovered.

**It removes light.** McEwan's own note is that despill results make the
background turn dark. `--preserve-luma` puts the removed energy back into the
other two channels, split so the pixel's luminance returns to what it was, and
never back into the key channel, which would undo the despill.

**It shifts hue**, and on a green stage the casualties are the yellows: a yellow
is red plus green, and clamping its green turns it orange. This is MEASURED, not
warned about: the actual hue rotation in CIE Lab, in degrees, between before and
after. Saying "delta E 6" does not tell anybody which way a colour went.

**Measured here**, on a yellow badge at `[0.75, 0.70, 0.08]` with `limit_min`:
73.1 degrees of hue rotation toward orange and a luminance loss of 0.443.
`preserve_luma` returns the luminance to within 1e-6 and leaves the rotation,
which is the honest trade: the light comes back, the hue does not.

Every despill number must be measured INSIDE the matte. Over the whole frame the
answer is always "most of it", because the backing is nothing but backing colour
and despilling it is exactly what despill is for. `despill` without `--alpha`
says so instead of pretending.

### A keyed matte hides track error from every check

The alpha comes from the plate's own green, so the composite is clipped to the
exact green region no matter where the track thinks the quad is. "No plate green
survives" and "nothing outside moved" both read zero on a badly tracked comp.

**A mask cannot audit itself.** Any check on a composite must be anchored in the
PLATE. See section 8.

---

## 6. Premultiplied alpha, and blending in light

### Ground: premultiplied, because that is the form the algebra is written in

Porter and Duff, *Compositing Digital Images*, SIGGRAPH 1984, define the
compositing operators on colour components already multiplied by their alpha.
Smith and Blinn say the same about the matting equation: "Derivations with
non-premultiplied alpha are not so elegant." The practical rule, which is what
this skill enforces:

**Premultiplied while it is being FILTERED, unpremultiplied while it is being
COLOUR CORRECTED.** Blurring straight RGBA drags whatever colour is sitting in
the transparent pixels into the visible edge. Grading premultiplied RGB
multiplies the correction by the alpha, so the edge gets a different correction
from the core.

### Ground: blending happens in LIGHT, not in code values

An encoded value is a perceptual code and averaging two codes is not averaging
two quantities of light. This is not a subtlety.

**Measured here.** A 50/50 mix of teal `[0, 0.6, 0.6]` over red `[0.9, 0, 0]`,
done on sRGB code values, comes out **55 per cent darker in luminance** than the
same mix done in linear light. The visible signature is a dark seam wherever two
different hues meet across an edge; the classic demonstration is that mixing teal
with red, magenta with green or blue with yellow gives dark grey in a gamma space
where it should give a light neutral.

The two mistakes compound. Premultiplying in code values and THEN decoding is
wrong by a factor: measured, the red channel comes out at 0.30 of the truth. So
`Image.as_linear` undoes the premultiplication BEFORE the transfer and reapplies
it after, and `require_same_transfer` refuses to blend two arrays that do not
agree about what a number means.

### The four notch, and why a client calls it four spots

Softening or resampling an RGBA overlay must be done on PREMULTIPLIED colour:
blur `rgb*a` and `a`, then divide back out. And PAD the working copy first, or
the blur has nowhere to fall off to and reads the pad instead.

The signature is diagnostic. A mark that fills its own canvas edge to edge,
pasted into a black pad, gets a dark notch at exactly four points, top, bottom,
left and right, where it meets the pad. A client reading it will say "dark spots
top, bottom, left and right", not "a dark ring".

**Measured here**, with a blur of sigma 3 on a 128 px mark padded by 24: all four
contact points fall to **0.692** of the mark's own value, while a point eight
pixels in from a corner reads 0.996. Premultiplied and padded, all four read
1.000.

`comp.py verify notch` runs exactly this on real artwork: it detects whether the
alpha reaches the canvas edge, pads it the way a compositor would, and measures
what a straight blur would do. Blurring artwork in isolation cannot show the
fault, because a blur of a mark against its own reflected edge has nothing
foreign to drag in. The pad is where the foreign colour comes from.

---

## 7. Making an insert belong

### The horizon ratio: the same real size in two frames of a moving shot

For a level camera, the ground plane gives, for ANY object anywhere in frame and
INDEPENDENTLY of where the camera is:

    image_height / (y_base - y_horizon) = object_height / camera_height

so an object's height in pixels is `(y_base - y_horizon) * object_height /
camera_height`. Feeding one ratio into both frames is the only checkable meaning
of "the same size in both shots". Matching pixel heights between frames is wrong,
because the camera moved.

**Do not try to register the two frames first.** Feature matching and dense flow
both lock onto bokeh discs and wet road reflections, which are virtual images and
move unlike the ground plane. The horizon ratio sidesteps registration entirely.
Take the horizon from the vanishing point and cross check it against eye lines: a
taller person's eyes plot above it, a shorter person's below.

### Look match is measured, not dialled

Level as a RATIO to a reference inside the plate; defocus from a real edge at the
object's depth; grain from the plate's own residual; plus haze and bloom, because
a plate that blooms its highlights makes a non blooming object read as a sticker.

**Grain.** `comp.py grain` takes the plate minus a denoised copy of it, which is
the technique Ben McEwan sets out in *Re-Graining Your Comp Using Existing Plate
Grain*: extract by denoising and subtracting, add back with a Plus, and keymix
the original grain back over the areas that already have it so it does not
double. It is ADDED, not multiplied: it was measured as a difference and it goes
back as one.

Grain on a real plate is DENSITY DEPENDENT. The residual in a deep shadow is not
merely weaker than the residual in a highlight, it is structurally different, and
a flat grain plate laid over an insert reads as video noise rather than as the
plate. `grain` therefore reports sigma per channel per luminance band.

**Measured here**, on a synthetic plate: 0.0113 in the 0.05 to 0.15 band against
0.0268 in the 0.35 to 0.70 band, in the red channel, a factor of 2.4 across the
range.

**Light wrap.** The background is blurred HARD before it is laid on the edge, so
what lands there is light and not detail; a sharp wrap paints a ghost of the
background onto the subject and reads as a transparent edge. It is confined to a
band just inside the matte by the difference between the matte and a blurred copy
of it, and it is ADDED, because light adds.

---

## 8. The checks, and every one anchored in the plate

`comp.py verify` has five, and the first thing each of them prints is what it
does NOT prove.

### ring: did the REAL bezel move?

Key the screen, take a ring just OUTSIDE the keyed boundary, and prove the real
bezel's linear luma did not move. Read against the FILE'S own generation floor,
never against zero.

This proves the composite stayed inside its panel. It proves nothing about where
the content sits inside it.

### content: where did the content actually land?

Two outlines, per frame, neither of which uses the track:

- the BACKING, measured on the PLATE by keying it;
- the CONTENT, measured on the COMP as the region where the composite differs
  from the plate at all.

Comparing those catches two DIFFERENT faults and separates them. A constant
offset is a REGISTRATION error: the insert sits six pixels off the panel in every
frame including the first, so a drift check calls it perfect because nothing is
drifting. A growing difference is a TRACKING error. `verify content` reports
both, and says which one it is looking at.

Three things it learned the hard way:

**The threshold that decides what counts as composited is a MEASUREMENT.**
Everything outside the region was re-encoded but not touched, so what it differs
by IS this pair of files' own generation floor. Measured on the synthetic pair
that floor was 0.049, and the threshold is set eight times higher. Set at the
floor instead, the outline is codec noise: the check's own spread rose to 10 px
and it could no longer separate a good composite from one 6.3 px out.

**The verdict is on the MEDIAN and on the registration, not on the worst frame.**
A single frame's outline carries this check's own spread, and judging a film on
the worst reading of a noisy measurement fails correct work. The worst frame is
reported beside it as something to go and look at.

**Do not point a second measurement at the region the track was solved on.**
Re-solving the same pixels with the same model against the same reference is not
a second measurement, it is the same measurement typed twice, and it agrees to
0.000 px. `verify content --body-mask` refuses when it recognises the track's own
region.

**Measured here.** A correct composite: median 1.89 px, registration 0.50 px,
PASS. The same composite with the track offset by 6.3 px: median 8.20 px,
registration 3.75 px, FAIL, and correctly diagnosed as registration rather than
tracking. `verify ring` PASSED on both.

### channels: red and blue swapped

Whenever content crosses between an OpenCV path (BGR) and an ffmpeg raw path
(usually RGB), find the most saturated NON-green element in the asset and look at
it at 1:1 in the RENDERED frame. A swap is a no-op on any neutral and on anything
whose colour lives in the green channel, which covers most UI. A mean colour or a
histogram cannot see it: the set of channel values is unchanged.

`verify channels` picks that element automatically, carries it through the track
into the rendered frame, and reports the distance to the artwork's colour as
written against the distance if swapped. Where the two hypotheses are not
separated it says AMBIGUOUS rather than choosing.

### notch: the premultiplied fringe

Section 6.

### rank: can this check separate these versions at all?

Cross pair every version with every reference. If the spread a single version
shows ACROSS references is as large as the spread across versions for a fixed
reference, the check's own noise is bigger than the difference it is being asked
to rank. It bounds and it cannot order, and saying so is the correct answer.

---

## 9. The plate's own cadence

A generated plate is often a higher rate conformed down by dropping pictures, so
the scene lurches on a fixed beat. That lurch is real motion the comp must
reproduce, or the content slides against the real bezel on that beat. Detect it
BEFORE tracking, smooth against TRUE TIME rather than frame index, and never
score a track against a smoothed copy of itself.

`comp.py cadence` measures how far the picture MOVED between frames by phase
correlation, not the mean absolute frame difference. A pixel difference
saturates: double the displacement and a textured scene gives well short of
double the difference, so the beat gets squashed into the noise. A displacement
is linear in the time step, which is the whole point.

Then it detrends. A real shot speeds up and slows down, so a small step during a
fast passage can be larger in absolute terms than a lurch during a slow one, and
the beat disappears into that trend. Divide each step by a running median of its
neighbours and what is left is the beat, scored by a t like statistic rather than
by demanding the two populations never overlap, which no real plate manages.

**And a cadence lives in the SIZE of a step, so the step has to be bigger than
the measurement's own error.** Phase correlation resolves about a fifth of a
pixel of whatever raster it was handed, so a quarter size copy of a plate that
drifts 1.6 px per frame is measuring nothing. `cadence` notices and redoes it at
full resolution rather than reporting the noise. If the picture still moves less
than 1.5 px per frame it returns UNMEASURABLE and says why: either the shot is
locked off, or only PART of the frame moves and the measure is reading the still
part, which is what `--region` is for.

**Measured here.** A 30 fps plate conformed to 24 by dropping one picture in five
was read as a 2.07x lurch every 4 frames at 52 standard errors of separation,
implying "about 30.397 fps conformed to 24.000". The same plate at its native
rate returned NATIVE, best candidate 1.07x.

---

## 10. What is delegated, and what is deliberately not here

**Roto.** There is no rotoscoping engine here and there should not be. But the
route out is specific, and the first thing to get right is that it is two models,
not one.

**A mask is not a matte.** A segmentation model returns a decision per pixel:
foreground or not. A composite needs alpha, a FRACTIONAL coverage at every edge
pixel, because hair, motion blur, defocus, smoke and glass are genuinely part
foreground and part background. Drop a binary mask into a comp and you get a
cardboard cutout whose edge boils frame to frame, and no amount of feathering
downstream recovers the coverage that was thrown away at the decision. So the
pipeline is always: something that says WHICH pixels, then something that says
HOW MUCH. Segmentation, then matting.

**Route A, Resolve Studio Magic Mask.** The cheapest route on a machine that
already runs Resolve. On a `TimelineItem`: `CreateMagicMask(mode)` where mode is
`"F"` (forward), `"B"` (backward) or `"BI"` (bidirectional), and
`RegenerateMagicMask()` to re-solve after adjusting strokes.

**It needs one gesture at the screen, and the API cannot supply it.** Measured
here 2026-08-23 with a live Studio licence and the external API connected:
`CreateMagicMask("F")` on a real clip returned False in 0.0 seconds, while
`Stabilize()` and `SmartReframe()` on the same item in the same session both
returned True. The verb PROPAGATES a stroke, it does not paint one, and with no
stroke on the clip there is nothing to propagate. Failure 42.

The route that does run unattended is the Fusion node, not the timeline verb.
Fusion's registry carries a `MagicMask` tool (confirmed present, 622 tools). Its
`Strokes` input has datatype `MagicMaskStrokes`, which Python cannot construct,
but the node serialises with `SaveSettings` to a plain text `.setting` file
carrying a `MagicMaskStrokes { }` block verbatim, and `LoadSettings` reads one
back. So a stroke painted ONCE becomes a template whose coordinates are editable
as text, and the node's own `TrackForward` and `TrackReverse` inputs run the
propagation with nobody watching. Budget one human gesture per shape, not one
per shot, and never per frame.

Two more cautions. It is a STUDIO function, and Blackmagic's own note in the
scripting README is that an API call referencing a Studio function from the free
version returns False. Not an exception, not a message: a False that a naive
script will read as "no mask this frame" and carry on. Any wrapper around this
must treat False as a hard stop and check the edition first, which
`resolve_api.py status` does by asking the product its own name. And Magic Mask's
output is a mask that Resolve then softens in its own graph; taking it out of
Resolve as alpha means rendering it out, not reading it back through the API.

**Route B, segmentation into matting, outside Resolve.** For the WHICH half, the
current model is **SAM 3** (Meta, released 2025-11-20), which changed the shape
of the problem: it does promptable CONCEPT segmentation, so a text prompt or an
image exemplar returns EVERY instance of that concept in the shot, tracked, not
one object per click. **SAM 3.1** (2026-03-27) is a drop in replacement adding
Object Multiplex, a shared memory that tracks up to 16 objects in one forward
pass, quoted at 32 fps on a single H100 against 16 before, and about 30 ms for a
single image carrying more than 100 objects on an H200. Video latency scales
with object count and stays near real time to about five concurrent objects.
Weights are gated on Hugging Face behind a contact form; the base checkpoint is
0.9B parameters. Licence is Meta's own SAM License, which permits commercial use
with carve outs for military, ITAR, nuclear and weapons work. Clean for
advertising and film.

Anything in this skill or in a job folder that still says SAM 2 is two
generations stale and should be read as SAM 3.1.

**The HOW MUCH half is where the licence bites.** The strongest video matting
models with memory propagation are MatAnyone (CVPR 2025) and MatAnyone 2
(CVPR 2026). Both are published under the **S-Lab License 1.0, which permits
non commercial use only**; commercial use requires written permission from the
authors, which is a request they field routinely. That is a fact about the
weights, recorded here so that nobody rediscovers it after a job has shipped,
and it is a business decision rather than a technical one. Permissively licensed
alternatives, weaker but unencumbered, are BiRefNet, ViTMatte, SeC and Robust
Video Matting.

**Route C, no route.** If the shot has no backing, no Studio licence and no GPU,
say so and price the roto as roto. That is a legitimate answer and it is cheaper
than a boiling edge on a delivered master.

**What this skill contributes to any of the three** is everything downstream of
the matte: the edge treatment, the premultiplication discipline (`_pix.over`,
and failure 26 for what an unpremultiplied blur does at the canvas edge), the
grain match, and every check in `comp.py verify`. The matte arrives from
elsewhere; the composite is proved here.

**Lens distortion.** Not implemented. The standard model is Brown-Conrady, radial
`k1, k2, k3` plus tangential `p1, p2`, and the pipeline question is whether to
undistort the plate and composite flat, or to composite distorted and warp the
insert. Planar trackers are robust enough that most screen inserts need neither.
When a job does need it, solve it with a calibration and record the coefficients
in the job's own notes; do not guess them.

**3D camera solve.** Out of scope. Everything here is planar.

**The cut, the grade, the type and the sound.** `video-editing` and
`davinci-resolve`, `colorgrade`, `supers.py`, `audio.py`. This department hands
over to them and does not reimplement any of them.
