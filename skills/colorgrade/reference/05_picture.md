# Picture faults: split screens, stalled frames, a colour step inside a shot

Colour is not the only thing wrong with a file that arrives to be graded. Three
picture faults turned up across one three film series, each of them invisible to
every instrument in the skill at the time, and each of them now has a tool.

Order matters and it is not negotiable: **repair the picture first, then colour
it.** Rebuilding frames after grading means rebuilding them from the source,
which silently drops any colour repair that lands on those frames. That happened:
four frames inside a corrected range came back ungraded, the file looked
completely normal, and it was caught only by reconciling a file count and asking
what twenty eight unexplained files were.

---

## 1. A split screen breaks every whole frame measurement

**Symptom.** Nothing looks wrong in the numbers, and the film plays badly.

**Cause.** Two independent pictures share the frame. Every measurement in this
skill averages over the frame, so a fault confined to one half is diluted by a
half that is behaving. On the film that produced this: whole frame cut detection
found 17 cuts and per panel found 23; whole frame stall detection found 14 frozen
frames and per panel found 54. The actual fault was the right hand panel running
at 18.0 to 19.8 unique pictures a second for five seconds while the left panel
beside it ran at 23.5. Averaged over the frame that is invisible.

**Rule.** On a split screen film, every measurement and every repair is per
panel. `cgpanel.py` finds the panels; nothing else should hardcode a column.

**Finding the divider.** Not by flatness in the averaged picture. That nominated
a 1755 column stretch of office wall, because a film that changes worlds averages
to flat almost everywhere. A divider **holds still while the frame around it
moves**, so the test is relative: per frame, per column, motion as a fraction of
that frame's own motion. A card where the whole frame is frozen scores zero
everywhere and is excluded by construction.

**Finding the seam to switch on.** Per shot, never nominal, and measured as the
gradient inside each frame averaged over frames, NOT the gradient of the temporal
average. A moving texture averages to flat grey over a shot, so the temporal
average nominates the moving picture as a place to cut and puts the seam 19
columns into the wrong half. Under about 1.2 code levels of gradient, a hard
switch cannot show.

**Two traps that announce themselves quietly.**

* A helper that indexes a film wide array by absolute frame number, handed a
  pre-sliced array, produces an empty mean, a nan and an argmin of zero. That put
  a panel boundary 84 pixels out and announced itself only as a numpy
  RuntimeWarning. Any such helper must assert its own result.
* ffmpeg does not refuse an odd crop width, it silently rounds down, and the
  caller then reshapes the buffer with the width it asked for. An 81 column band
  decoded as 97.8 frames. `cgpanel.snap_band` handles it once, centrally.

---

## 2. Stalled frames, and the three conditions

**Symptom.** A hitch. Frames that repeat instead of advancing.

**The rule that does not work.** An absolute threshold. It catches frames that
froze dead and lets through the half steps beside them. Rebuilding a dead frame
against a half step leaves two frames crawling at a quarter of the shot's motion
right before a cut, which is a worse hitch than the original. That shipped once.

**The rule that does.** Three conditions, all together:

1. the frame moves far less than the frames right **around it**, not less than
   the shot average
2. it is genuinely hold-like against the shot's own typical step
3. it is part of a run no longer than about three frames

Drop the third and the rule wants a quarter of a film, including a settled close
up where the actor is simply still. Rebuilding that invents movement that was
never shot, which is worse than leaving the fault in.

**Deliberate holds are not faults.** Cards, end boards and settling titles hold
on purpose. Always look at what the tool flagged before rebuilding it.

---

## 3. Filling holes and rebuilding timing are different jobs

They look identical in a list of stalled frames and they want opposite repairs.

* **Frames merely dropped.** The survivors still sit on their true instants, so
  a two frame gap carries twice the movement of a one frame gap. Fill the holes,
  leave every real picture where the editor put it.
* **A frame rate round trip.** Every picture sits on a wrong instant, so a two
  frame gap and a one frame gap carry the same movement. Nine pictures over
  twelve frames sit at 0,1,3,4,5,6,8,10,11 when they belong at 0,1.33,2.67,4 and
  so on: up to two thirds of a frame out, twice a second. Rebuild the timing.

**The test is the gap ratio**, two frame gaps against one frame gaps. Near 2 means
fill. Near 1 means retime. An earlier version measured how far the survivors sat
from even instants and read 0.53 frames of wobble on a shot that had merely
dropped frames, calling for a retime it did not need; the gap ratio read 1.72 on
the same shot and called it correctly.

A second guard is needed with it. A rate conversion loses a large fixed share of
the frames, a quarter for 24 to 18. A shot that kept 96 per cent of its frames
cannot be one however its gap ratio reads, and calling it one would move every
real picture in it to fix two stalls.

---

## 4. Mean error against a held out frame rewards blur

This is the most transferable thing in this file.

A soft rebuild is closer on average to the truth than a crisp one whose detail is
a pixel out of place. So a holdout scored on error alone **systematically selects
the softest method**. That is exactly how a rebuild shipped whose frames measure
10 to 25 per cent softer than their neighbours, landing every twelve frames,
twice a second. To the viewer that reads as the subject twitching sideways and
back for one frame. No measurement of motion can see it, because nothing moved:
median optical flow over the head was smooth to a 0.21 pixel floor with no frame
more than 1 pixel off it.

**Score three numbers, not one.**

| number | what it catches |
|---|---|
| error | how far off it landed |
| detail, gradient energy against the real frame | blur |
| placed, gradient map correlation with the real frame | ghosting and misplacement |

The third is needed because Laplacian variance alone reported rebuilt frames
holding **178 per cent** of the real frame's detail. A double edge carries more
high frequency than a single one, so ghosting reads as extra sharpness.

Run on real footage, hidden frames, the three numbers separate the methods
cleanly, and note that averaging beats repeating on error while being 11 per cent
softer, which is the whole point:

| method | error | detail | placed |
|---|---|---|---|
| repeat the frame before | 6.466 | 100.6% | 0.4006 |
| average the two | 4.955 | 88.7% | 0.4888 |
| motion, one picture (RIFE 4.25) | 2.974 | 106.4% | 0.5449 |

**The mechanism.** Warping both neighbours and blending them is what makes the
blur: two warps that disagree by a pixel average into a two tap blur. Use a
network that produces one picture. RIFE 4.25 measured error 2.078 against 2.306,
detail 99.9 per cent against 95.2, edge placement 0.7883 against 0.7665, and five
times faster than the warp-and-blend it replaced.

**Keep the picture in its own colour space.** RGB goes to the network so it can
estimate motion; what comes back is a vector field and a fusion weight, and those
are applied to the file's own y, u and v planes. In fastmode 4.25 has no
refinement net, so that reproduces its own output exactly, and every untouched
column stays byte identical.

**Frames at the end of a shot have no picture after them.** They cannot be
interpolated. Respread the shot's last real pictures so the last one lands on the
last frame: nothing invented, only real pictures moved.

---

## 5. A colour step inside a shot, on part of the frame

**Symptom.** The colour changes at one frame, in one half of a split screen, with
no cut there. The editor's cut on the OTHER half carried it across.

**Cut or colour change?** Correlate the gradient map across the suspect frame. A
real cut in the same panel reads about +0.01. A colour change reads whatever its
neighbours read, because the picture is continuous. Measured: +0.843 across the
fault against +0.842 and +0.946 either side, and +0.007 at a real cut in the same
panel. The synthetic version of the same test in `selftest_tools.py` reads +0.786
against a neighbourhood of +0.787, and −0.005 at a cut.

**How big, measured where nothing moves.** A rotating disc or a title fading in
contaminates any whole-frame number. Divide into cells, keep only cells still in
both states and not flat noise, measure on those. The fault read 5.06 code levels
against a floor of 0.29 and 0.73 measured at two ordinary frames in the same film.

**How far it reaches.** Measure it. The change covered the picture AND the
divider bar, out to a column well past the seam. Assuming the panel edge would
have left a visible strip.

**What model can express it.** A hue selective change cannot be written as a 3x3
matrix, and that is testable rather than assumable: held out, a matrix left 2.05
levels and a smooth 3D fit left 1.13.

**Fit the DELTA, not the map.** A ridge term pulls the solution toward the zero
function. On a delta that is "change nothing", which is the correct prior
everywhere the fit has no data. On a map it is "output black", and that put a 2.3
level blue lift into pure black on a film whose black anchor was a headline
result.

**Say what cannot be fixed.** About a fifth of that change was not a function of
colour at all, confirmed on best content matched cells: 1.04 levels of residual
against a 0.14 floor. No colour correction of any kind removes that. Report it.

---

## 6. Two decode paths are not comparable, ever

Splicing a numpy box downscale of the Y plane into ffmpeg's scaled gray put the
two 11 code levels apart, invented a stutter at every repaired frame, and
reported that a repair had made two of three shots 48 per cent **rougher**.
Measured through one decode, every shot got smoother.

This is the same error as comparing a grading harness's own output against an
encoded file. `cgpanel.stream` returns the filter chain that produced every
array and `require_same_path` refuses the comparison across two. Use it even when
it is obvious the paths match.

Related, and worth its own line: **decode once**. Decoding per shot makes ffmpeg
walk the file from the start each time. On a 4K piece with twenty five shots that
is twenty five full decodes and it takes longer than the grade.
