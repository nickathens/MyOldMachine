# Failures found on real footage, and what they cost

Every entry here was measured on a real file, not reasoned about. If a grade
comes out wrong, start here.

## 1. Grey world white balance destroys a brand

**Symptom.** A corporate brand film graded with the first version. Shots
built on the brand yellow came back green. Shots on the brand pale blue came
back warm and washed. Visible instantly on the contact sheet.

**Cause.** The illuminant was estimated with a shades of grey estimator, the
Minkowski norm of order 6 over the whole frame. Every estimator of that family
assumes the average of the scene should be neutral. On a shot genuinely
dominated by one colour that assumption is false, and the estimate describes the
subject rather than the light. Correcting toward it then removes the subject's
colour, which on brand work is the entire point of the shot.

**Fix.** Estimate the illuminant only from pixels that could plausibly have been
neutral: the least saturated quarter of the usable pixels, averaged in linear
light. Report a confidence alongside it, and when even that quarter is itself
strongly coloured, skip white balance entirely rather than guess.

**Detail that mattered.** Saturation has to be measured in code space, not in
linear. Measured in linear the same visible tint reads far higher, because
linear values span a much wider range, and every shot then looks too coloured to
trust. With the linear measure, confidence came out at zero on every single test
file including ordinary interiors.

## 2. Rendered graphics are not camera footage and must not be balanced

**Symptom.** Same film. Even with a better illuminant, 18 of 21 shots were
hitting a safety cap and the consistency verdict refused to improve.

**Cause.** The film is mostly motion graphics on a brand field. Those shots have
no white balance to correct, no exposure error, and a black point of 0.55
because nothing in frame is dark. Comparing them to a live action interior and
calling the difference an inconsistency is a category error, and acting on it
damages one or the other.

**Fix.** A flatness measure decides whether a shot is camera original or
rendered. Fraction of pixels whose immediate neighbours match to within half a
code level. Camera footage carries sensor noise everywhere so almost nothing is
truly flat; rendered graphics are flat over most of the frame.

**Calibrated, not picked.** The film's brand motion graphics read 0.26 to 0.72.
The live action interior inside the same film reads 0.09. AI generated footage
reads 0.10 to 0.17. Threshold set at 0.22.

**The tolerance matters more than the threshold.** At 1.5 code levels instead of
0.5, compression smoothing pushes camera footage up to 0.35 and the separation
disappears entirely.

**Result.** Graphics shots are now left completely untouched by the balancer.
On the same file the black point spread across the judged shots fell from 0.470
to 0.036 code, and the brand colours come back pixel identical.

## 3. Matching lightness through the luma percentiles does not match lightness

**Symptom.** On the ground truth harness, a* and b* matched to within 2 units
across six segments while mean L still spanned 9 points. That reads as one shot
being flatly brighter than the next, which is exactly the fault shot matching
exists to remove.

**Cause.** Exposure and levels work on luma percentiles. The medians agreed to
0.03 code. But the median of the luma and the mean lightness are different
statistics, and for anything with a non trivial histogram they move apart.

**Fix.** Match mean L explicitly in the matcher, alongside a and b, capped at 25
percent.

## 4. A colour only cut is invisible to shot detection

**Symptom.** A test file built from six identically framed segments that differ
only in grade was detected as one single shot.

**Cause.** Content based detection compares picture content between frames.
Between two identically framed shots that differ only in colour there is almost
no content difference, so it correctly reports no cut.

**This is not a bug in the detector.** It is a real limit, and it applies to any
real edit that cuts between two takes of a locked off frame.

**Fix.** `--cuts` accepts explicit cut frames. Use it whenever an EDL exists or
the cuts are known.

## 5. Shot detection drops a real cut that arrives too soon after a false one

**Symptom.** A test file with cuts every 36 frames was detected with cuts at 26,
72, 98, 144, 170. Three of five boundaries were 10 frames early.

**Cause.** The test material was a looping clip with its own internal content
change around frame 26. The detector fired there, and then dropped the real cut
at 36 for being closer than the minimum shot length of 12 frames.

**Consequence for real work.** A fast internal movement near a cut can steal the
boundary, and the shot is then graded from partly the wrong material. Nothing in
the output flags this. Check the shot boundaries in the report on anything fast
cut, and supply `--cuts` if they look wrong.

## 6. A seeded colour grow finds the rim of a glassy object, not the object

**Symptom.** The generated DCTL for a glass lens produced a hollow ring where
the disc should have been solid.

**Cause.** The pale interior of a glass lens is too desaturated to match the
bright rim on colour.

**Fix, and the reason the whole design works.** Once the spatial window is
holding the area, the colour gate can be loosened a very long way, because
nothing else is inside the window to go wrong. Re measure the gate over
everything inside the window rather than only inside the grown region.

**Second failure inside the fix.** Loosening the value floor along with the
saturation floor let the dark denim behind the lens into the matte, because
denim shares the blue band and only brightness told them apart. The matte grew
from 84,000 pixels to 108,000 and visibly bled. Loosen saturation, hold value.

**Measured result.** 84,542 matte pixels, zero pixels anywhere else in the
frame, 16,046 of them partial at the soft rim. With the window switched off the
same colour gate takes 104,197 pixels of which 13,829 are elsewhere in the
frame. That gap is the entire argument for a DCTL over a LUT, on real footage,
aimed automatically from one seed point.

## 7. A hard matte costs half the colour move at the edge

Ground truthed on a synthetic disc with known fractional coverage, graded
through true coverage and through a binary matte, compared against the
analytically correct result.

Soft matte: rim error 0.0 levels. Hard matte: rim error 20.4 levels, which is 50
percent of the full colour move.

This is why a segmentation model cannot be the deliverable on its own. It
answers where the object is. It does not answer what the edge should look like,
and the edge is where amateur work announces itself.

## 8. Resolve rejected a DCTL that the Metal harness compiled happily

The first LensIsolate compiled in `dctl/dctl_host.swift` and ran on this
machine's GPU. Resolve Studio 21 answered "Error Processing DaVinci CTL" and
refused to load it. So a harness pass is necessary and not sufficient: the
harness maps `__TEXTURE__` to a struct that Metal will pass to a function,
Resolve's own translator will not.

Diagnosed by auditing the file against the thirteen sample .dctl files
Blackmagic ship in
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/DaVinciCTL/`,
because there is no published grammar. Two constructs appear in none of them:

- `__TEXTURE__` passed into a helper `__DEVICE__` function. It was there for a
  9 by 9 local standard deviation that separated rendered graphics from real
  texture. Dropping it cost nothing measurable once the window was aiming, and
  made the shader faster.
- Alpha output. `DaVinciCTL/README.txt` section 7 says alpha only works through
  the ResolveFX DCTL plugin, and the alpha version still failed, so it is out.
  `Show Matte` displays the matte instead.

Only a float returning helper survived, because `AlphaCircularWindow.dctl`
demonstrates one.

The rewrite loaded first time, screenshot confirmed, all 16 controls with their
compiled defaults. The rule that came out of it is in the skill: write only
shapes Blackmagic themselves demonstrate. `tests/test_colorgrade_skill.py` pins
both constructs so they cannot come back unnoticed.

If a generated DCTL is ever rejected again, load Blackmagic's own
`GainDCTLPlugin.dctl` from that same folder as a control. If theirs also fails
the problem is the install or the route, not the generated code.

## 9. The highlight rolloff was not protection and every look clipped

**Symptom.** Every attempt at a stronger grade blew about five per cent of a film
to flat white, and it read as the footage having no headroom.

**Cause.** `_soft_shoulder` lerped its compressed curve against the identity.
The compressed curve is asymptotic to 1, but the identity is not, so any amount
below 1.0 put an unbounded slope straight back into the output. Measured at knee
0.80: amount 0.35 passed 1.0 for any input above 1.034 and reached 2.95 at input
4.0. Only amount 1.0 was bounded at all, and **every look in the library shipped a
value between 0.2 and 0.6.** `_soft_toe` had the identical defect downward, so a
"soft" toe still crushed: at amount 0.15 the output went negative for any input
under −0.016.

The job it was found on worked around it by pushing `highlight_rolloff` to 0.91
and then 1.00, which is the tell: a parameter that only works at its maximum is
not doing what its name says.

**Fix.** `amount` moves the knee instead of blending the result. Amount 0 leaves
the signal alone, amount 1 rolls off from the knee, anything between rolls off
from a knee that fraction of the way up. Output stays under 1 for every finite
input at every amount, the slope at the knee is 1 so there is no kink, and amount
1.0 reproduces the old curve exactly.

**Measured across all ten library looks**, on two real frames and a dense
lattice: mean difference 0.454 code levels of 255, worst look 1.310, and below
output 0.90 the mean difference is 0.1 to 0.56, so the change is confined to the
top end where the old curve was clipping. `neutral` is bit exact, which it has to
be. Pixels pinned at pure white fell from 9.5 per cent to 2.0 on average, nothing
clips more in any of thirty cases, and `clean_commercial`, `kodak2383`,
`teal_orange`, `sunlit` and `warm_doc` went to exactly 0.000 per cent.

**A second, unlooked for result.** Removing the clip crease improved LUT bake
accuracy, because a 3D LUT cannot represent a crease. Worst dE2000 at 33 cubed:
`noir` 2.39 to 0.76, `bleach_bypass` 1.61 to 0.68, `clean_commercial` 1.30 to
0.82, `warm_doc` 1.05 to 0.89. Both `noir` and `bleach_bypass` now bake to a 33
cube where they previously needed 65. `sunlit` got slightly worse, 0.80 to 0.97,
still well inside budget.

## 10. hue_shifts gated in one colour space and rotated in another

**Symptom.** Four hue holds aimed at measured brand colours did nothing at all,
and the graded film printed numbers identical to no holds. That very nearly read
as "the holds do not help", for entirely the wrong reason.

**Cause.** `apply_look` selected pixels with the HSV hue and rotated the Lab hue
angle, in one call, with nothing naming either. The two are different numbers for
the same colour and differ by a different amount for every colour: teal reads
189.20 in HSV and −151.21 in Lab, skin 15.18 and 52.62, sky blue 227.08 and
−76.28. Centres measured in Lab therefore selected colours that were not in the
picture.

**Fix.** Each `hue_shifts` entry takes `"space": "hsv"` or `"lab"`. Default is
`hsv`, which is what the library looks were authored against and is bit exact for
them. An unknown space raises rather than guessing. Both hue conventions are now
reported side by side by `cgseries.py primaries` so the two can be seen not to
match.

## 11. Rebuilt frames were built in the wrong colour space and the film flashed

**Symptom.** That film, delivered 8 August. The colour was accepted. The
client came back with flicker at 0:16, 0:34, 0:48, 2:04 and "many more places".
Nothing looked wrong on a still.

**Cause.** The frames that filled the stutter were extracted with OpenCV's
`VideoCapture`, which converts yuv420p to BGR with the **bt601** matrix. The
film is tagged **bt709**. Measured on frame 384 that is 2.79 code levels mean
and 26 at worst, mostly green. Every rebuilt frame was therefore built, and
spliced back, in a colour space the frames either side were not in. In the
delivered file frame 376 sat at green 229.6 between neighbours at 226.0. The
affected shots carried one rebuild every three frames, so the offset arrived at
8 Hz. Correct picture, wrong colour, on a regular pulse.

Even a matched matrix does not save it. A yuv to RGB to yuv round trip on the
same film shifts luma by 0.84 of a level and U by 0.53. That is a bias, not
noise, so it lands on every rebuilt frame and on none of the others.

**Fix.** The picture is never converted. RGB is used only to estimate motion,
which yields a vector field and touches no output pixel. The warp and the blend
run on the film's own yuv420p planes, luma at full size and the two chroma
planes at half size with the flow halved to match. `cgyuv.synth` is that path,
and `selftest_frames.py` pins it: a frame synthesised at t=0 must come back byte
for byte identical to the frame it was built from.

**Measured after.** Whole frame colour offset against the two neighbours went
from 2.183 levels median on rebuilt frames, against 0.127 on real ones, to
**0.116 against 0.115**. Rebuilds thrown clear of both neighbours went from 64
of 85 to **0 of 151**.

**Detail that mattered.** Flow can still be estimated from the wrongly converted
stills. Their colour is wrong by a constant that both frames of a pair share,
and the estimator matches structure, so the field is unaffected. Only the output
path has to be clean.

## 12. A solved problem that lives in a job folder is not solved

**Symptom.** Failure 9 had already been hit on the previous film a day earlier,
diagnosed, fixed, and written up. The write up is in that job's own folder, in a
file whose docstring says in as many words that alternating replaced and
untouched frames "would read as a flicker". The next film repeated the fault
exactly and a delivery was rejected for it.

**Cause.** The fix was written next to the film it was found on. Nothing carried
it anywhere a later job would look. A per job folder is where evidence lives,
not where method lives.

**Fix.** The method moved into this skill: `cgflow.py`, `cgyuv.py`,
`cgframes.py`, this file, and `07_frames.md`. The rule is that a repair is not
finished when the symptom is gone at the site it was found. It is finished when
the same class of mistake is impossible at every site, and something automated
proves it.

**How to tell it is happening again.** A fix that names one shot, one film, one
call site or one frame range is the shape to distrust. Ask what else shares the
mechanism before closing it.

## 13. Two opposite repairs look identical in a list of frozen frames

**Symptom.** A shot with frozen frames can need either of two repairs, and doing
the wrong one leaves the fault in while costing sharpness.

**Cause.** A shot with **holes** runs at the right rate with individual frames
stuck, and wants each hole filled from the two real frames either side. A shot
with a **broken cadence** was retimed by repeating frames rather than by
interpolating them, so it advances at perhaps 18 unique pictures a second inside
a 24 fps film, in a repeating long short short pattern. Filling its holes leaves
a residual wobble at the period of the original stutter, because the surviving
frames are not evenly spaced in time either: with M survivors shown over N slots
at unchanged speed, exactly N minus M of the M minus 1 steps span two originals.
In a list of frame numbers the two faults are the same list.

**Fix.** `cgframes plan` separates them by density and regularity of the repeats
and prints its decision per shot with the numbers behind it, so a wrong call is
visible before anything is built. A retime rewrites every slot in the shot,
including moving the survivors to their true positions, and is kept or reverted
as a whole shot. A hole is judged and kept alone.

**Detail that mattered.** On one film the ticket shot moved enough for the
cadence to be read reliably and was rebuilt fully. The sofa shot barely moved,
the same reading was unreliable there, and every real frame was left exactly
where the editor put it. Same film, same symptom, opposite repairs.

## 14. Four instrument errors, each of which said the job was fine

**Symptom.** On that rebuild, four separate measuring mistakes each
returned a clean result on work that was not clean.

**The four.**

1. The single frame pop sweep scaled by the **delivered** shot's median, which
   the fault itself had inflated from 1.3 to 3.1 at 0:16, so it reported the
   flicker as normal. Scale on the source, never on the file under test.
2. The sweep judged on raw frame difference, which flagged 93 **correct**
   repairs: filling a frozen frame is supposed to raise the local difference.
   Judge where a frame sits **between** its neighbours instead. A frame on the
   path scores near 0.5, a frame the flow pushed elsewhere scores above 1 no
   matter how clean it looks alone.
3. The real frame floor included shot cuts, which put its 99th centile above 10
   levels, so everything passed. Exclude the frames either side of every cut
   before taking a centile from real frames.
4. Softness was measured at native 4K, where grain dominates and a resampled
   frame keeps its grain. It disagreed with the finished render. Measure at
   viewing scale, near 1920 wide.

**Fix.** All four are closed in `cgframes.py` and `cgyuv.Ruler`. The general
rule underneath them: a test that reads clean on a file known to be bad is not a
test. `cgframes verify --against` exists to run the instrument on the rejected
file and print both columns side by side, and it warns when both read clean.

**Measured.** Validated that way, the pop instrument flags 64 of 85 rebuilds on
the rejected version and names 0:16, 0:34 and 0:48 unprompted.

**A fifth, found by that warning.** The colour instrument first led on a count
of rebuilt frames past the 99th centile of real frames, which is how it was
written on that job where the fault was 17 times the real median. Run
against a test clip deliberately rebuilt through the bt601 round trip, that
count stayed at zero while the medians read 0.170 against a real 0.054. A
handful of real outliers is enough to lift a 99th centile above a genuine fault.
The instrument now leads on the ratio of the two medians, with a cut of 1.5 that
comes from what a rebuild is rather than from a percentile: an average of two
frames can only be flatter than a real frame. The count stayed as a supporting
line. Nothing found this except running the test on a file known to be bad.

## 15. Two different scalers is not a measurement

**Symptom.** The sharpening solver asked for about four times the amount
actually needed, driving every rebuild to its cap.

**Cause.** The rebuilt frame was scaled with OpenCV's area filter and compared
against real frames scaled by ffmpeg's bicubic. The two disagree by roughly 40
per cent on Laplacian variance all by themselves. Nothing was wrong with either
scaler, only with using both.

**Fix.** One ruler, one path, for everything being compared. `cgyuv.Ruler.small`
is that path and every reading goes through it.

## 16. A bare luma plane is a full range format

**Symptom.** Every rebuilt frame read as far softer than it was, and the
sharpening drove to its cap again after failure 13 was fixed.

**Cause.** The luma was handed to ffmpeg on its own as `gray`. Gray is a full
range format, so the 16 to 235 expansion the tagged path applies was skipped and
the variance read 1.355 times low.

**Fix.** A frame is always handed over whole, as tagged yuv420p carrying the
file's own range and matrix, never as a lone plane. And before anything is
measured, `cgyuv.Ruler.check` puts a real source frame through the per frame
ruler and requires it to reproduce the value the sequential scan already holds
for that frame, to within 1 per cent. If the two paths disagree the run stops
rather than producing numbers about the path.

## 17. On a split screen, every measurement has to be scoped to one panel

**Symptom.** The split screen film is two separate pictures side by side for 34 of its
47 seconds. From 12 to 17 seconds the right panel ran at 18 pictures a second
while the left ran at 23.5. It reads as broken while nothing looks frozen, and
every measurement built on the previous two films was wrong on it, because each
one averaged two different pictures together.

**Fix.** Find the divider, work out where along it a switch is safe, and keep
every measurement and every repair to one panel at a time.
`cgframes --panel x0,x1` scopes the repair, and `cgyuv.paste_panel` rounds both
edges to even so the chroma planes cut on the same pixel as the luma. The other
half of the frame comes through byte identical.

**Detail that mattered.** 54 stalled frames were repaired one panel at a time.
The 95 held frames inside the two on screen cards were deliberate and every one
was left. Frozen is not the same as wrong.

---

## 18. A deliberate hold scores better on the cadence test than a broken cadence

**Symptom.** None visible, which is the point. A shot containing a 20 frame hold
was classified as a retimed shot and planned to move 48 real frames that nobody
had touched. Had it run, a title settling or an actor being still would have
been replaced by invented movement, and the frames the editor chose would have
been thrown away to fix a fault that did not exist.

**Why the test could not catch it.** The cadence test asks two questions: are
the repeats dense, and do they arrive on a regular beat. A solid run of frozen
frames answers both better than a real rate conversion does. Every gap inside a
run is exactly 1, so the spread of the gaps is exactly 0.00, a more perfect beat
than any real conversion produces. Measured: density 0.26, gap spread 0.00,
verdict "dense and regular, this shot was retimed". Tightening the regularity
threshold makes this worse, not better, because the hold is at the good end.

**The fix, and where it has to live.** In detection, before the cadence test
ever sees the frames. No frame rate conversion produces long runs: 24 from 18
repeats one frame at a time, 24 from 12 every other frame, 24 from 8 in pairs. A
run longer than three frames is a hold, not a fault. `census` sets those frames
aside and PRINTS THEM, because a hold that is silently dropped looks exactly
like a fault the tool failed to find.

**How it was found.** Not by reading the code, which looked right. By carrying a
rule across from a different branch of the same skill, noticing the shipping
code did not implement it, and building the case on real footage to see what
actually happened. The rule had been written down once and lost on the way into
the working tool. See entry 12.

---

## 19. The recommended setting for 4K was wrong for this job

**Symptom.** The frame builder invented motion between frames that were
identical. Fed a pair of frames with nothing moving at all, it returned a
picture off by 0.883 code levels on average and 177 at worst, with the error
WORST IN THE CENTRE of the frame rather than at the edges, so it was invention
and not a padding artifact. That is the same order of colour shift as the RGB
round trip that entry 11 exists to prevent.

**Cause.** RIFE is normally run at scale 0.5 on 4K material and that advice was
taken at face value. It is correct advice for its purpose, which is
interpolating across LARGE motion, where a coarse pyramid is needed to follow a
big displacement. Frame repair works between ADJACENT frames, where the motion
is small, and there the coarse pyramid has nothing real to lock onto and
produces movement that is not there.

**Fix.** Full resolution. Off by 0.015 levels instead of 0.883 at 1080p, 0.0095
instead of 0.794 at 4K, better on all three holdout scores on frames with real
rotation and zoom, and FASTER, because the padding unit is 64 over the scale and
a smaller unit pads less: 0.61s against 1.80s per 4K frame.

**The general lesson.** A setting recommended for a task is not recommended for
your task. The control that caught it costs nothing to run: feed the tool two
identical frames, where the right answer is known exactly, and see whether it
gives it back.

---

## Where the rest of the failures live

The picture faults found on a split screen series are in `05_picture.md`: per
panel measurement, the three condition stall rule, the gap ratio that separates
dropped frames from a rate conversion, the holdout scoring that rewards blur if
you let it, and the localized colour repair. `07_frames.md` is the frame repair
method as it actually ships, and entries 11 to 17 below are its failures. The
series matching failures are in `06_series.md`: matching the recipe instead of
the result, pivot placement on a bimodal film, masks that select the wrong
thing, and synthetic probes that fall outside the gamut.

