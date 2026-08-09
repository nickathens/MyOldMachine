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

## Where the rest of the failures live

The picture faults found on a split screen series, and the frame repair work, are
in `05_picture.md`: per panel measurement, the three condition stall rule, the
gap ratio that separates dropped frames from a rate conversion, the holdout
scoring that rewards blur if you let it, and the localized colour repair. The
series matching failures are in `06_series.md`: matching the recipe instead of
the result, pivot placement on a bimodal film, masks that select the wrong thing,
and synthetic probes that fall outside the gamut.
