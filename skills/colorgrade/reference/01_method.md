# The method, and why the order is what it is

## Normalise, then match, then look. Never any other order.

This is the standard colourist order and it is not arbitrary.

**Normalise** puts every shot into the same reference frame: exposure sitting
where it should, the neutral axis actually neutral, black and white points in
sensible places. Until that is done, two shots cannot be compared, because a
difference might be light, exposure, or lens.

**Match** removes what is left between shots. It is a small correction by
definition. If it is large, the normalise step failed and the match is papering
over it.

**Look** goes on last and goes on identically to every shot. That is what makes
a piece feel like one piece. A look applied before matching bakes each shot's
individual error into a different place on the tone curve, and then no amount of
matching brings them back together.

Sources for the ordering, all read rather than recalled: the Blackmagic Colorist
Guide chapter on grouping, which puts balancing in pre clip group mode and the
per shot work in clip mode; and the practitioner writing on shot matching, which
says the same in plainer words, build the look on the strongest clip and match
everything else to it.

## The two pass structure, and the bug that forced it

The match must be measured on the ALREADY BALANCED picture, not on the original.

This was found by measurement, not by reading. The first version computed the
mechanical balance and the residual match from the same untouched numbers and
stacked them. Both were then correcting the same error, and the second
correction overshot. On the ground truth harness that pushed the residual spread
between segments from 8.9 dE up to 14.5 dE, which is worse than doing nothing to
half the shots.

The fix is the obvious one once seen: balance, re measure, then match what is
actually left. That is literally what a person does at a scope.

## Prediction must be measurement

The grader decides whether to iterate by predicting what the grade will do
before rendering. The first version predicted analytically, by pushing the
measured mean colour of a shot through the grade.

That is wrong, and wrong by a lot. Pushing the mean through a nonlinear function
is not the mean of the function's outputs. On the ground truth harness the
analytic prediction claimed 2.7 dE of residual where the rendered file actually
held 7.3. The iteration loop was optimising against a number that was not true,
so it stopped early and reported success.

The fix is to grade the sampled frames and measure the result. Eight small
frames per shot costs milliseconds. There is no reason ever to guess.

## Why the grade is code and the LUT comes last

The grade is a chain of operations evaluated per pixel, in defined spaces:
linear light for exposure and white balance, Cineon log for contrast and tone,
Lab for targeted hue work, display gamma at the end. Only after all of that is
it baked onto a lattice and written as a .cube.

Two things follow. The result ffmpeg renders and the result Resolve renders are
the same file of numbers, so there is no drift between preview and delivery. And
the LUT's error against the maths it came from is a measurable quantity rather
than an article of faith, which is what lets the grader choose the LUT size
instead of picking 33 because everybody picks 33.

## Measuring the LUT error in dE, not in code levels

The obvious metric is the largest difference in code value between the LUT and
the maths. It is the wrong metric.

Measured on clean_commercial: the worst code level error sits at output code
0.01 to 0.03, that is, in the very bottom of the shadows, where log space
contrast makes the transfer steep and a uniform lattice is coarse. Four code
levels there is invisible. Four code levels at mid grey is not.

dE2000 knows the difference. The grader budgets 1.0 dE, which is roughly the
point where two flat patches become distinguishable at all, and steps from a 33
cube to a 65 cube when the budget is exceeded. Where even 65 cannot meet it,
which happens for very hard contrast looks, it says so instead of hiding it.

## Gamut compression instead of clipping

A strong look pushes saturated colour past the edge of Rec.709 and sends a
channel negative. Clamping that at zero puts a crease in the transfer, and a 3D
LUT cannot represent a crease.

Measured: baking teal_orange to a 33 cube with a hard clamp gave errors up to 39
code levels of 255 on saturated purples. With ACES style gamut compression in
front of the clamp, the same look bakes to 8.5 at 33 and 2.9 at 65.

It is also simply better grading. A film print compresses rather than clips, and
so should this.

Compression is off for the neutral look, deliberately, so that a neutral grade
remains a bit exact identity. It necessarily touches very saturated colours that
were still legal, and that is a price only a look should pay.

## Every correction is capped

Exposure is capped at 1.5 stops, white balance at 18 percent per channel, the
black point at 0.10 code, the Lab match at 6 units, the lightness match at 25
percent. Every cap that binds is printed by name and by shot.

The reason is not timidity, it is that a wrong measurement should cost accuracy
and never cost the shot. And the list of bound caps is itself the most useful
output: when most of the shots are capped, the footage is not a candidate for
automatic balancing, and that is worth being told plainly.
