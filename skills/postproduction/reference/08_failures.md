# Failures found on real films, and what they cost

Every entry here happened. Each one was measured on a real file on a real job,
found late, and fixed. None of them is reasoned about from first principles, and
that is the point: the whole set exists because the same faults kept being met
by people who already understood the theory.

Client names, file paths and brand details are deliberately absent. This
repository is public and the lessons survive the anonymisation intact.

If something in a delivery is wrong and you do not know why, start here.

---

## 1. The filename claimed a resolution the file did not have

**Symptom.** A whole review set of twenty items, each named for its content and
its format. Nobody in the chain had raised a resolution problem.

**Cause.** Every file was 1280x720. Several were named for a different subject
entirely: items named for one room showed three different rooms, because the
naming had drifted from the playlist and the playlist was what everyone meant
when they said "video 12".

**Fix.** `spec.py probe` before anything else, and `spec.py claims` on the name.
Go by playlist number and picture, never by name.

**The general form.** A name is a claim that travels further than the file and
gets edited on the way. A file cannot lie about its own raster.

---

## 2. A hash proves the pixels and says nothing about the colour tags

**Symptom.** An unrelated check broke: asking for frame 795 of a delivered
master returned nothing, while frame 794 returned a picture.

**Cause.** ProRes written from a PNG sequence with no `-colorspace` comes out
UNTAGGED. Spliced into a film tagged bt709 with `-c copy`, the delivered master
changed its colour metadata partway through, at the join. Every frame hash
matched. Every picture check passed. A player, or a colourist's tool, is
entitled to read those frames differently from the rest of the film.

The visible symptom was not a colour symptom at all: ffmpeg reconfigures its
filter graph where the stream parameters change, and that restarts the `select`
filter's own frame counter, so a missing frame is what it looks like.

**Fix.** Three checks, and they are now `prove.py tags`:

1. Encode every spliced segment with the tag the film carries, matched to the
   file being joined, never assumed.
2. Compare the segment's tag with the film's BEFORE concatenating, and the
   assembled master's after.
3. Walk the whole delivered length with something that can only survive one
   configuration, probing either side of every join.

**The general form.** Container and stream metadata are part of the deliverable.

---

## 3. A verifier demanding zero measured the codec, not the film

**Symptom.** Seven checks failed on a master that was correct.

**Cause.** The supers compositor re-encodes a whole ink SPAN to change part of
a picture, so every pixel in that span carries one codec generation, not just
the pixels that changed. On ProRes 4444 that costs about 0.2 of 1023 code levels
everywhere in the frame. A verifier that demanded the untouched regions be
identical was measuring the encoder.

**Fix.** The number to compare against is not zero, it is the frame's own
GENERATION FLOOR, measured on a region the change cannot reach. Settle it by
measuring a stream copied frame (exactly 0) against a re-encoded one, and read
every signal as a multiple of that floor. Real ink came in at 186x to 405x the
floor. `prove.py floor` does this measurement.

**The floor has a SHAPE, not just a size.** ProRes encodes in slices 16 rows
tall and picks one quantiser for the whole strip, so new ink anywhere in a strip
re-quantises the rest of that strip and nothing outside it. A later check that
masked the type's bounding RECTANGLE found three levels of 255 beside the type
and failed a correct film; masking the type's own SLICE ROWS gave exactly 0 of
65535, which is a stronger statement than the tolerance it replaced.

**When a check fails on a file you believe is correct, find the mechanism and
let the mechanism set the boundary. Do not widen the tolerance.**

---

## 4. A new check that had never been run on an approved file

**Symptom.** Twice, a check written on the night of a delivery failed a correct
master.

**Cause.** Once it was a threshold below the picture's own generation floor.
Once it was a demand that every frame carry one uniform colour tag, when the
build the client had already approved had carried two kinds since its own
splices.

**Fix.** Run any new check against the last file the client accepted. If it
fails there, the check is wrong, not the film. Make the pass condition "matches
the delivered predecessor", not an absolute the lineage never met.

---

## 5. The expensive check that gets skipped

**Symptom.** A verifier patched and re-run without the slow part.

**Cause.** Three framemd5 walks over 5 GB of ProRes is twelve minutes, which is
exactly the pressure that tempts you to patch a check and skip the re-run.

**Fix.** Cache on (path, size, mtime). The corrected run then finished in
seconds and left one clean artifact instead of a hand merged one. `prove.py
frames` caches by default.

---

## 6. A spliced master's timeline is broken at its joins

**Symptom.** Two splices in a row came out wrong, and the frame COUNT was right
both times.

**Cause.** The concat demuxer does not leave a uniform timeline. On one set of
masters the last frame of each stream copied piece came out ONE TICK long
(duration 1 of 512) and every packet after each join sat 511 ticks early. The
video track ended up 74.250 s against 74.292 s of pictures, one frame short,
while all 1783 pictures were present.

- The recipe `-ss (N/24 + 1/48)`, which had worked on every earlier master, is
  1.5 frames past frame N's start there and lands on N+1. The master came out
  two frames short.
- The obvious repair, the midpoint of pts[N-1] and pts[N], lands INSIDE frame
  N-1 when frame N-1 is one tick long, so every copied frame was a frame EARLY.
  The count was right; only a frame md5 battery caught it.

**Fix, and it is four parts.**

1. Read the file's OWN packet timestamps and seek to the midpoint of pts[N] and
   pts[N+1]. Every ProRes and DNx frame is a keyframe, so a copy starting at
   that instant starts on frame N whatever the timeline does. This is
   `prove.py seek`.
2. PROVE the cut without decoding: a piece's packet SIZE sequence must equal the
   matching slice of the source's. This is `prove.py packets --compare`, and it
   catches one early and one late in seconds.
3. Rewrite the output's timeline onto a clean grid so the next version can cut
   it with plain arithmetic: `-bsf:v setts=ts='N*512':duration=512`.
4. When you must decode to look, anchor a contiguous run against a boundary the
   build already knows. Hashing a seeked frame against the file's own framemd5
   does NOT work: the two decode paths choose different pixel formats. Never
   anchor against another decoder either; two decoders disagree by 1.5 to 2.3
   levels purely from YUV to RGB conversion.

**Also.** `select=eq(n,N)` on a long single generation master will happily
decode every frame up to the target: one careless run wrote a 26 GB raw file.

---

## 7. A changed frame list that was typed rather than derived

**Symptom.** On version five of a film, nobody could say with confidence which
frames had moved.

**Fix.** Derive the prediction from the build. Hash every input layer of the new
build against the previous build's; the predicted set is exactly the frames
where a layer differs, or exists in one build and not the other. Then require
the delivered file's per frame hashes against the previous master to be exactly
that set. On one version: 233 predicted, 233 found, 1857 of 2090 bit identical.

**Why it beats a typed list.** A tool that moved something unintended shows up
in BOTH accounts, so the check can only pass when the two agree. A typed list
works only while the person writing it remembers everything they touched, and
that is precisely what fails on version five.

**Prove it on a short render first.** Render a few hundred frames spanning the
first expected change and require bit equality before it and a difference at
exactly the predicted frame. One minute, and it catches a wrong path or a stale
cache before a twenty minute render.

`prove.py predict`, then `prove.py expect`. Note that expect FAILS on a constant
offset rather than absorbing it: layer files numbered from 1 against film frames
numbered from 0 is a mapping to confirm on purpose, not to guess.

---

## 8. An additive change that could not be proved additive

**Fix, and it is the strongest single proof in this document.** When a revision
ADDS something to a delivered film (a shadow under type, a graphic on a screen,
an overlay), build every changed segment TWICE in the same pass: once with the
new thing switched OFF, once with it on. Require the switched off encode to be
BYTE IDENTICAL, by sha256, to the segment the delivered film is actually made
of. Abort the render if it is not.

It collapses "I only added X" from an argument about how the build was
constructed into a proof that the decode, the colour transform, the composite
and the encoder all reproduce the delivered film exactly. Anything that had
drifted (a different ffmpeg, a stale coefficient, a seek one frame off, a
changed raster) breaks the equality before a frame ships. On one revision: six
sha256 equalities across five blocks, all held, for the cost of one extra
parallel encoder per segment.

**It only works if the delivered build's own segment files are still on disk.**
Do not sweep a working segment folder after a delivery, even when the master is
cleared.

---

## 9. An element that was built, written up as delivered, and never composited

**Symptom.** A smoke test over 344 frames the revision was NOT touching: 309
matched the delivered master and 35 did not.

**Cause.** Those 35 frames carried an element the client had asked for, that was
built, that had a before and after sheet sent for it, and that never reached the
compositor. A check over the layer FILES passes: the files exist.

**Fix.** Run the smoke test over a span you are NOT touching, not just the one
you are. And for an additive element, prove it with a NULL RENDER: re-render the
span with the layer removed and require byte identity with the delivered master.
That proves both that it was absent before and that the element is the only
difference now.

**Related trap.** A renderer that inventories its input directories once at
startup cannot be protected by a preflight over that inventory. An empty
directory is invisible to both.

---

## 10. A check whose own noise was larger than the difference it was ranking

**Symptom.** A delivered film check read 0.48 px for the new track and 0.32 for
the old, implying a regression.

**Cause.** Pairing each film with BOTH tracks, including the mismatched
combinations, gave 0.48 / 0.45 / 0.32 / 0.37: a spread of 0.16 px against a
0.20 px difference between the tracks. The check could not tell them apart at
all.

**Fix.** Not deleting the check, and not loosening its threshold until it
passes. Rewrite it to run its own calibration and report that it BOUNDS but
cannot RANK, and move the ranking to a measurement that has real ground. Say the
limit out loud in the delivery.

---

## 11. A hard linked build folder rewrote the delivered version

**Symptom.** A delivered track file was replaced by a bad refit, silently.

**Cause.** Each new version of a film was set up as `cp -al` from the previous
one, so every file in the working folder was the SAME INODE as the delivered
version's. Opening one with mode `w` truncates it THROUGH THE LINK.

**Fix.** Break the links before the first write. Do not audit writers one by
one: the rule had already been applied to the JSON writers written by hand, and
the damage came from a tool that was only being CALLED, whose one line of
`json.dump(..., open(path, "w"))` replaced the previous version's delivered
track file.

    cd <new_version> && mv work work.hl && cp -R work.hl work && rm -rf work.hl

`archive.py unlink` does this and reports what it broke. Read only trees such as
plates and sources can stay linked.

**Two habits that make it survivable.** Copy any file you are about to re-solve
before you run, and hash whatever should NOT have changed afterwards.

---

## 12. Notes recorded an intention and were read back as a fact

**Symptom.** A status file said twice, in two different sections, that a
version's masters had been cleared. Both 7.2 GB files were still on disk with
their original creation timestamps. The wrong claim was then repeated into a
project summary and read back as fact for a day.

**Cause.** Notes are written at the moment a decision is made, not at the moment
it is carried out.

**Fix.** Before repeating or acting on a note that says something was deleted,
sent, rebuilt or cleared, look at the artifact. When the note and the disk
disagree, correct the note in place, say plainly that it was wrong and for how
long, and only then do the thing it claimed.

---

## 13. Re-encoding straight over the version somebody was comparing against

**Symptom.** An approved deliverable was destroyed by the next encode.

**Cause.** The encode wrote into the same output folder.

**Fix.** Copy the existing deliverable into a dated archive folder BEFORE
re-encoding. Every previous attempt is the undo history. `archive.py stage` does
it and writes a hash ledger at the same time.

**And the recovery rule.** It was recoverable only because the whole build was
deterministic: reverting two code changes regenerated a master that hashed to
exactly the value the build notes had recorded. That hash match is the only
thing that made the recovery honest rather than a claim. Recording the sha of
every delivered file is what makes this possible at all.

---

## 14. A build script that no longer reproduced what it shipped

**Symptom.** Re-running a content builder's own resample of a source animation
produced light beams and confetti instead of the settled lockup it had shipped.

**Cause.** The script's frame indices no longer landed where they landed on the
night, because the build had been edited after that step ran. The build notes
even said the composite started on one frame while the script said another.

**Fix.** A build folder holds two different kinds of truth. The delivered
artifact is what the client saw; the script is an ACCOUNT of how it was made,
and the account drifts the moment anyone edits a constant, swaps a source or
fixes a later step. Determinism is a property of a run, not of a file.

When a revision only moves, resizes or recolours something already delivered,
take the DELIVERED frames as the source, copy them somewhere untouched, and
transform those. Only re-run a generator when you have first proved it
reproduces a frame it already shipped, byte for byte.

---

## 15. Clearing old masters, and the two traps that nearly took a live file

The instruction was "delete old masters from all films but be ready to go back
at any moment". The second clause is the SPEC, not a sentiment: it converts
delete into delete only what you can prove you can restore.

**Trap one: a version number in a path is a LABEL, not a fact about what is
live.** A master sitting in a folder named for an old version was the live
graded master for the two versions after it, and the only supers free copy on
disk. It was found by reading a NEWER version's own hash file, which checksums
it as a DEPENDENCY. Liveness lives in the dependency records, not in the folder
name.

**Trap two: resolve symlinks before believing a dependency graph.** A first scan
that filtered relative links reported "depends on nothing". Resolved properly,
the live tree was 2454 symlinks with 2332 landing inside the previous version:
that version's plates and frames ARE the live film's. Delete only the output
masters, never the folders, and re-check for broken links AFTER deleting.

**The three gates, in this order.** `archive.py sweep` enforces them.

1. Re-hash the LIVE masters and require them to match their record. If what you
   are keeping is not what you think it is, what you are deleting is not
   redundant, it is the last copy.
2. Hash each condemned file BEFORE deleting it. One file's recorded hash was
   EMPTY; hashing it on the way out was the last moment its restore could ever
   be made checkable.
3. Check each restore path EXISTS before destroying what it restores.

Keep every viewing copy and proof sheet: past builds stay watchable, and only
the rebuildable and provable part goes. Where a build encoded a layer both ways,
the "off" encodes may be byte identical to the previous delivery, and then a
rollback is a concat stream copy with no render at all. Mark those KEEP.

---

## 16. A cached solve that did not know its tool had been fixed

**Symptom.** A client spotted a wobble by eye and named the timecode. The right
edge of a screen had been flapping in and out 31 times in 88 frames for four
versions, and the fix had been sitting in the same folder for three of them.

**Cause.** A fault in a shared edge fit was found and fixed while working on one
group of shots, those shots were re-solved, and the job moved on. The other
shots kept their old track files. A solve written to disk is a cached ANSWER; it
does not know the tool that produced it has since been fixed.

**Fix.** When the fix is to a shared measurement, re-measure every consumer with
the new tool and table old against new before deciding what to re-solve. That
turns "what else is affected" from a guess into a list. Then either re-solve
them or write down, in the build notes, which ones you knowingly left on the old
answer and why.

**Only re-solve what the job needs.** Re-solving everything moves screens the
client has already approved. Fix what was named, report the rest with numbers,
and keep the untouched artefacts byte identical so the isolation check can prove
nothing else moved.

---

## 17. A global polynomial deleted the motion it was smoothing

**Symptom.** A client said "the phone is moving slightly and the screen inside
is not".

**Cause.** A cubic fitted per corner across 42 frames has four degrees of
freedom for the whole clip. Real plate motion is usually a SETTLE, not a drift,
and a cubic cannot represent a settle, so it deleted the motion and substituted
a monotonic slide. Measured cost: 6.91 px worst error.

**Fix.** Take the rigid motion from the object itself: ECC alignment on a mask
over the device body, then carry a reference frame quad along it. Nothing fitted
through time means nothing real can be smoothed away.

**Smoothing is only safe when the model can represent the true motion.
Otherwise it is invention.**

---

## 18. A keyed matte hid the track error from every check

**Symptom.** Every check passed on a comp that was visibly sliding.

**Cause.** The alpha comes from the plate's own green, so the composited screen
is always clipped to the exact green region no matter where the track thinks the
quad is. The outline is perfect even when the artwork inside is sliding. "No
plate green survives" and "nothing outside moved" both read zero on a badly
tracked comp.

**Fix.** Measure the CONTENT position against the device body, in a window where
the artwork is not animating (a hold, or before a sweep starts). A composite
check must be anchored in the PLATE, never in the model the build used.

---

## 19. The reference region was not rigid with the tracked object

**Symptom.** A correct new track was made to look 3 px wrong.

**Cause.** Measuring "phone motion" from everything outside the screen quad
captured table, sofa, arm and background, so the median was CAMERA motion: it
reported 4.18 px of rise where the phone rose 1.38 px.

**Fix.** A tight bezel annulus (the quad dilated 75 px minus the quad dilated
9 px) made two independent methods agree to 0.2 px. "Not the subject" is not
"the subject's body".

**The same trap in a new costume.** A slip check was run over "the still tail",
but the client's own screen recording drifts 1 to 4 plate px per frame to its
very last frame, so it was measuring their camera and read 1.80 px. Find a
region that is motionless BY MEASUREMENT (396 of 812 rows frozen to within 0.349
of a level) and measure there: 0.14 px.

---

## 20. A convex hull straightened a curved screen

**Symptom.** A client saw it instantly and named it correctly: a curved screen
composited as a straight one.

**Cause.** A curved panel is concave toward the camera, so its two ends stand
nearer the lens than its middle. Project its outline and exactly ONE edge, the
bottom, bows INTO the shape. `cv2.convexHull`, used to make an unordered edge
set fillable, replaces that edge with its chord. Measured: top, left and right
edges 0.0 px error, bottom 41 to 48 px, mean 27.7 px, growing as the camera
pushed in. The content was painted down across the bottom bezel.

**Fix.** Emit an ORDERED closed ring for anything that fills or masks. Keep the
independent runs sampler only for the nearest neighbour residual, where order
does not matter.

**All 23 checks passed on the broken build**, because every one of them masked
the panel with the same hulled polygon the composite filled. A mask cannot audit
itself. Key the screen from the plate, take a ring just OUTSIDE that keyed
boundary, and prove the real bezel's linear luma did not move: broken build
+58 L, correct build +0.2 L.

**Also.** Casting a polygon to int32 truncates, dragging the whole shape up to a
pixel toward the origin. Fill with rounded sixteenth pixel coordinates and a
shift. And a FLAT panel is immune, because its projected quad is convex and the
hull is a no-op (measured 0.0005 px over 55 frames). The fault only ever appears
on curved glass.

---

## 21. A hull bridged a corner it had never seen

**Symptom.** A tracker that fitted to 0.94 px on the plate it was written for
gave 8.66 px bottom edge rms on a new one, with the screen height wandering 910
to 1063 while the phone moved 0.6 per cent, and 105 px of corner shake.

**Cause.** Hulling the keyed green stops a finger biting into an EDGE from
pulling the least squares fit inward, and that reasoning is sound only for a MID
EDGE occluder: the bridge lies along the straight edge the bite came out of, so
nothing is invented. When the occluder removes a CORNER, the bridge is a chord
across the gap and the fit follows the chord.

**Fix.** Measure the edges nothing covers, per frame, sub pixel. Declare a
frame's edge UNMEASURED unless its own scanlines say otherwise. The object is
rigid, so on the frames where the edge IS visible its projected extent divided
by an unoccluded dimension is a constant; on the hidden frames place the corners
from that frame's own measurements times that constant. The per frame wobble
stays entirely in that frame's own data; only the object's SHAPE comes from
elsewhere, which is what rigid means.

**Prove it by hold out or do not ship it.** Re-measure the constant with each
visible frame left out, predict that frame, compare with what it measured
itself: 0.57 px mean and 1.86 px worst, against clean edges fitting to 0.3 px.

---

## 22. A solve was right on the boundary and wrong in the interior

**Symptom.** A client said the screen content looked stretched, twice, across
two versions. Residual green was zero in the version that was visibly stretched.

**Cause.** Fitting a monitor's outline gives you a pose AND an aspect, and THE
ASPECT IS NOT MEASURABLE FROM AN OUTLINE. The solve reported the panel as
2.2802:1 and the content was laid out at that, so everything came out squashed
to 0.69 of its width.

The quantity that governs the shape of anything drawn on the panel is its IMAGE
ANISOTROPY: how many pixels one unit of u buys against one unit of v.

    R = |dP/du| / |dP/dv|      the content canvas aspect must equal R
    a circle in the source renders at w/h = R / (canvas aspect)

On that shot R was 1.5712, constant to four decimals across a locked off take,
against a claimed physical 2.2802: hence 1.5712/2.2802 = 0.69.

**The aspect is not identifiable and R is.** Refitting the same boundary with
the aspect FORCED anywhere from 1.30 to 2.60 kept the boundary residual
respectable across 2.1 to 2.6, and gave R = 1.55 to 1.58 for every single one. R
is an invariant of the outline; the aspect is a gauge the outline cannot pin,
and the solver trades it against yaw.

**Fix.** Compute R from the fitted surface, area weighted over the panel, and
set the content viewport's aspect to it. Verify on the DELIVERED file with
something whose true shape you know: round UI elements are ideal. Look for a
reference the film already contains, such as the same monitor's closeup
elsewhere in the cut; that ends the argument better than any solve.

---

## 23. A plate's judder was real motion, and three versions smoothed it away

**Symptom.** A screen comp slid against the real bezel on a fixed beat, worst
where the camera was fastest and gone where it stopped.

**Cause.** The generated plate was 30 fps conformed to 24 by dropping every
fifth picture, so the room's step on every fourth frame was 2.09x its
neighbours: 1.2473 true ticks per frame against 30/24 = 1.25. Three versions
failed because each modelled the camera as smooth and the measurement as noisy,
and each tuned the smoother harder. The hardest smoother produced the most
visible fault.

**Fix, four parts.**

- Detect it BEFORE tracking. Track features across the plate, mask off the keyed
  region, and test the per frame step for a periodic double. Pick the phase by
  which choice makes the speed smoothest; do not assume it.
- Smooth against TRUE TIME, not frame index, and evaluate at each frame's own
  tau. Same code, different x axis: the noise goes and the lurch stays.
- Never score a track against a smoothed copy of itself. A local quadratic
  follows a four frame sawtooth happily: that metric read 0.044 px on a track
  sliding 8.
- A delivered film check must measure the CONTENT, not the boundary. The
  composite's outer edge is cut by the key, so it sits on the plate's edge
  whatever the track does. Ask instead whether the content lurches with the room
  (room 2.09x, the bad film 0.93x, the fixed one 1.72x).

---

## 24. A relight delta left an inverted ghost of the page it replaced

**Symptom.** All 22 verifier checks passed on a render with a legible negative
ghost of the old page under the new one.

**Cause.** The proven substitution for changing a screen's BRIGHTNESS is
`src_assembly + (comp_new - comp_old)`, and it cancels the assembly's h264 only
while the artwork stays put. Swap the page and that codec noise is stranded on
artwork that no longer exists, and it comes back INVERTED, because h264 pulls a
dark glyph toward its surround. Measured: sd 5.1 levels, p1 to p99 of -14 to
+17, correlation -0.61 against the old page.

**Fix.** Inside the screen mask, TAKE the new comp. Keep the delta only OUTSIDE
it, where the light the screen throws back on the actor lives and where the two
comps differ by a smooth lift rather than by content. Feather the mask 1.5 px.

**It was found by cropping the delivered frame and looking at it at 1:1.** After
any picture swap, look first, then write the check that would have caught it.

---

## 25. Fitting a client mock into a screen of a different shape

Never squash it into the aspect and never crop its margins. Scale to the WIDTH
so the design keeps its own side margins, and find the missing height at the
BOTTOM, below where the content ends. Continue the page's own background wash
rather than repeating its last row: a row repeated down a column turns its
horizontal noise into vertical stripes (1.93 levels of persistent column
structure against the file's own 0.45 to 0.67). Smooth the seed row across,
carry a fitted slope down, put the grain back per pixel.

**Hold the chrome.** Composite the film's own status bar and home indicator onto
the new page and erase the mock's. A generated mock's clock is a different size
and usually has no home indicator at all, so under a push transition the clock
jumps and the home pill flashes out.

**Reuse the solved track.** If the device did not move and only its content
changed, read the existing quads and re-run the comp. Nothing about the
registration should be re-derived.

---

## 26. An unpremultiplied blur darkened an overlay at exactly four points

**Symptom.** A client described "dark spots on its top, bottom, right and left"
of a mark composited onto a wall. Measured: minus 27 levels at the compass
points.

**Cause.** Softening or resampling an RGBA overlay must be done on
PREMULTIPLIED colour (blur `rgb*a` and `a`, then divide back out). Done straight,
it drags whatever colour sits in the transparent pixels into the edge. The
signature is diagnostic: a circular mark that fills its own canvas edge to edge,
pasted into a zero pad, gets a dark notch at exactly four points, where the disc
meets the pad. Everywhere else its neighbour is the canvas's own surround, so
the rest of the circumference is clean.

**Second place the same mistake hides.** The working copy of the artwork must be
PADDED before it is blurred, or the blur has nowhere to fall off to and the edge
stays hard and clipped.

**Measure it** as compass versus diagonal darkening of the plate under the
mark's edge, on the delivered file. Do not chart absolute brightness around the
mark (it reads whatever is behind it) and do not chart before minus after at the
same pixels (a sub pixel move of a high contrast edge swings tens of levels).

---

## 27. Red and blue swapped, and every check agreed

**Symptom.** One gold trophy element came out blue. Nothing else looked wrong.

**Cause.** OpenCV reads BGR, ffmpeg rawvideo pipes are usually RGB, and one
pipeline routinely carries both. The swap is a no-op on any neutral (equal
channels) and on anything whose colour lives in the green channel, which covers
most UI: white cards, black text, grey chrome, brand greens and teals. The card
in question was pale mint fill, dark navy type, green outline and a green tick,
and every one of those survives the swap looking correct.

**Fix.** Whenever content crosses between a cv2 path and an ffmpeg path, find
the most saturated NON-green element in the asset (a gold, an orange, a red, a
warm skin tone) and check it by eye at 1:1 in the RENDERED frame, not in the
flat asset. If the asset has no such element, add a temporary red patch, render
one frame, and look. A mean colour or a histogram will not tell you: swapping
red and blue leaves the set of channel values unchanged.

---

## 28. White ink on a pale film, and a luma check that could not choose

**Symptom.** Supers that read perfectly on a dark film became hard to read on a
pale one, and the obvious measurement said both inks were failing.

**Cause.** What hides a glyph is ground the same COLOUR as the ink, not ground
the same brightness. Measured in CIE Lab on a delivered master, white ink lost
17 to 98 per cent of its glyph surround on five of eight blocks while a brand
turquoise never dropped below dE 39. A luma only check calls both failures.

Reproducible in one line with this skill: on a pale ground the WCAG contrast
ratio of white is 1.27 and of that turquoise 1.29, indistinguishable, while the
Lab distances are 6.8 and 26.2, four times apart.

**Fix that keeps the look.** A soft black shadow scaled to the TYPE, not to the
frame: offset 0.04 x size, blur 0.14 x size, 70 per cent. Invisible on dark
shots, load bearing on pale ones. Pick the dose by rendering three or four at
1:1 on the WORST grounds and looking.

`supers.py contrast` for the two colour case, `supers.py audit` for the real
measurement against a frame.

---

## 29. Supers geometry copied instead of derived

A supers family is two edges and two steps: the last line's em box bottom on one
y, the widest line's ink right edge on one x, a size and a pitch. Written down
as pixel numbers it describes one job in one typeface. DERIVED from the font's
own metrics it survives a change of face, of raster and of copy.

Derive it, then CHECK the derivation against blocks that were already delivered
and approved before using it anywhere. On one family, solved from the font, the
anchors came out exactly on the four recorded values.

---

## 30. A downscaled preview put the work on trial in a form that could not show it

**Symptom.** A full size master and a small preview were sent together, with the
small one recommended for viewing.

**Cause.** The judgement is made from what can be seen, so a downscaled 8 bit
preview puts a fault, or a fix, in front of somebody in a form that cannot show
it. It also reads as the quality bar quietly dropping.

**Fix.** Never reduce the picture size or the bit depth of a deliverable. If a
lighter file is genuinely useful, keep the SAME frame size and the SAME bit
depth and only compress it. On a 10 bit source that means hevc_videotoolbox,
main10, p010le, high bitrate, tagged hvc1: measured 22 MB at 61.65 dB against
the lossless master, better than ProRes 4444 at sixty times the size, and it
plays natively. Prove the bit depth on the DELIVERED file rather than trusting
the pixel format tag: `spec.py depth`.

---

## 31. Two independent periodic faults in generated clips

Generated clips carry two faults at once and only distance measurement finds
both:

- a CADENCE fault, about 22 real pictures per second held out to fill 24 slots;
- a TELEPORT fault, the picture genuinely advancing 3 to 4.7 normal frames of
  ground in one slot, every twenty frames or so.

Hole filling patches only the first and leaves every real frame pinned, so the
picture still crawls then lurches. A cadence retime fixes the stick but can only
space survivors 1 or 2 slots apart, so a third of every teleport survives. Only
distance based placement fixes both.

**A third shape exists and neither planner covers it:** an exact 30 to 24
decimation, where travel per gap reads 14, 14, 14, 28 unbroken. When the
decimation is exact the plan is integer arithmetic and every fourth slot passes
through bit exact.

**Constraints that are not negotiable.** The frame rate stays identical: same
fps, same frame count, same duration, constant frame timing, verified ON THE
DELIVERED FILE. The clips are graded and cut against the edit, so any rate change
breaks the conform.

**On a locked off shot every camera tracker reads zero, so it measures nothing
at all there.** Two encoding traps found alongside: `libx264 -qp 0` is not
lossless in 10 bit, and colour flags on an encode from raw silently range
convert every pixel.

---

## 32. A long render died because it was attached to a session

**Symptom.** An encoder log reading "Immediate exit requested" and a broken
pipe, which looks exactly like an encoder fault and is not one.

**Cause.** The render was launched from a session that later stopped, and the
stop took the whole process subtree. The identical chain had survived the same
event the day before only by accident, as an orphaned shell, so the failure
looked new when it was always latent.

**Fix.** Launch detached at the MOMENT of launch, with marker files for done and
failed plus a log, so any later reader gets the outcome without owning the
process. Deciding to detach after a render has died is a decision that arrives
one render too late, and the launcher is four lines. On macOS there is no
`setsid`: a setsid based launch fails silently and looks exactly like a hung
job.

**A second cause on the same machine.** A nightly scheduled reboot killed
anything mid render at 05:00, attached or not. Before launching anything that
will still be running then, either finish before it or pause that night's
reboot, and say so.

**An all-intra master is a resource, not just an output.** ProRes has no inter
frame prediction, so a delivered master can be cut on ANY frame with `-c copy`
and every kept frame stays bit identical. Replacing two shots in a 1162 frame
film cost 164 rendered frames and 54 minutes instead of a full re-render. Cut
the new spans on the SHOT boundaries, not on the old render's fixed frame grid,
or the grid pulls in neighbouring shots whose intermediates are long gone.

---

## 33. Nothing is delivered until the send returns

Three comparison videos were built correctly and reported as sent while no send
call had ever been made. Building the deliverable and delivering it are two
separate acts, and only the second one is what was asked for.

Never write the word sent before the send has returned success for that exact
path.

---

## 34. A stills upscaler was run down a clip, and every still frame looked superb

**Symptom.** Nothing, on any frame. The stills sat beside the source and were
plainly better: sharper edges, cleaner lettering, real texture where the source
had mush. It was only in motion that the surfaces crawled.

**Cause.** The model has no memory. Every frame is upscaled independently, so
the detail it invents is a different invention each picture. There is no setting
that fixes this, because it is not a strength setting, it is the model's shape.

**Fix.** `upres.py temporal` and the `temporal` block of `upres.py verify`.
Measured on a real encode: the neutral resample reads +0.0005 excess detail
incoherence, the per frame model reads +0.60 on a scale where 0.707 is detail
invented from nothing on every frame.

**The general form.** A fault that only exists between frames cannot be found by
looking at frames. Every check on a moving picture must include at least one
measurement that spans time, and a review that consists of stills is blind to a
whole class of damage by construction.

---

## 35. A whole review set was named 4K and none of it carried 720

**Symptom.** Files named for a delivery resolution, all playing at that
resolution, and a client asking why the picture looked soft.

**Cause.** The files had been enlarged from a much smaller source. The header
said 3840x2160 truthfully, because the container really did hold that many
pixels; the pixels just held no detail above a quarter of that. `spec.py probe`
confirms the raster and cannot see this at all, because the raster is not the
lie.

**Fix.** `upres.py effres`, which reads the picture's own spectrum. On a real
encode it read the 960x540 source as CARRIES at knee 1.00 and its Lanczos 2x
enlargement as SHORT at 0.566, naming 960x540 as the source raster.

**The general form.** Failure 1 is the same lie told by the filename; this is
the version the file tells about itself. There are two different questions about
resolution and only one of them is in the header. A file can be honest about its raster and
still carry nothing in it, and enlarging such a file again multiplies the
softness rather than curing it.

---

## 36. A flow sign convention made every candidate boil equally

**Symptom.** A new temporal check reported that a control, a clean enlargement
and a deliberately boiling candidate all had roughly the same enormous warping
error. Nothing could be ranked, and the natural reading was that the measurement
was simply too noisy to be useful.

**Cause.** The optical flow was being applied backwards. OpenCV's `calc(f, g)`
returns the field F with `f(y,x) ~ g(y+Fy, x+Fx)`, so the field that resamples
`src` into `dst`'s frame is `calc(dst, src)`, not `calc(src, dst)`. On an exact
seven by three pixel translation with known ground truth: the right way round
leaves 0.0017 mean absolute error, the wrong way round leaves 0.0824, and 0.0824
is indistinguishable from two unrelated frames.

**Fix.** It was found only by building a flow field that was KNOWN rather than
solved, and measuring the statistic against that first. With the true field the
control read 0.00000 error; with the solved field it read 0.09450. That gap is
the bug, and no amount of staring at candidate numbers would have shown it.

**The general form.** A sign error in a comparison does not look like a bug. It
looks like a difficult subject, and it removes the instrument's sensitivity
without removing its output. Before trusting any new measurement, run it once
against an input whose answer you constructed, not one you solved.

---

## 37. A threshold on an unbounded ratio was a threshold on the shot

**Symptom.** A temporal check tuned on one clip flagged a correct enlargement on
another, and passed a faulty one on a third.

**Cause.** The statistic was the candidate's warping error divided by the
control's, and the control's is simply how trackable the shot is. Measured on a
clean synthetic pan the control's error was 0.000069 and a per frame model read
**669 times** it; the same fault on a hand held plate reads about three times
it. Same fault, same file, different verdict.

**Fix.** A statistic bounded by construction. Detail incoherence normalises the
warped difference by the residual's own magnitude, so for two independent fields
it is `1/sqrt(2) = 0.7071` whatever the amplitude. The verdict runs on the
EXCESS over the control's own value, where 0.021 and 0.085 mean the same thing
on every shot. The ratio is still printed, because when the floor is meaningful
it is the more familiar number, but it does not decide anything.

**The general form.** Before setting a threshold, ask what the denominator is.
If it varies with the material rather than with the fault, the threshold is
measuring the material.

---

## 38. A detail check that fits inside its own subject's roll off

**Symptom.** An effective resolution measurement said a merely soft frame had
been enlarged 2x, and said a frame blurred past all measurement carried its full
raster.

**Cause.** The power law was fitted over 0.06 to 0.30 of Nyquist. A real lens
has already begun rolling off inside that band, so the law being extrapolated
was the softness itself: the tool was measuring the blur through the blur. On a
frame blurred so far that the fit band held nothing but the roll off's own tail,
the fit came back flat and the extrapolation followed it, so nothing ever fell
below it and the verdict was CARRIES.

**Fix.** Narrow the fit band to 0.03 to 0.16 and guard it with the CURVATURE of
the log-log fit, not just its scatter. A straight line fits a curve over a short
span with small residuals, so scatter alone passes the broken case. Curvature in
dB per decade squared: every honest case inside 3.1, the unmeasurable one 254.

**The general form.** The same shape as never measuring a texture through the
filter that generated it. A model fitted inside the region it is meant to
predict cannot detect anything happening in that region.

---

## 39. The star field the recipe deleted while every metric approved

**Symptom.** A night sky upscaled with a proven recipe came back with the faint
stars gone. PSNR, grain deviation and edge gain all read well.

**Cause.** A star is a one or two pixel point source sitting on grain, which is
exactly what a detail model reads as noise. The structure mask pre-blurs luma at
sigma 1.5, which flattens a point source, so the mask never rose above about
0.25 on a star, and that quarter vote was enough for the model to denoise the
sky. Against a plain Lanczos ceiling of 72.3 per cent of the faintest third
retained: raw ESRGAN kept 13.5 per cent and invented 290 peaks, the stock hybrid
kept 51.2 per cent, a guarded hybrid kept 71.5 per cent.

**A brightness gate is the wrong fix.** Measured background luma: sky 27.5,
olive trees 24.3, stone arches 40.4 DN. The dark foreground that most wants
detail is as dark as the sky.

**Fix.** Edge continuity, with the point sources killed by a 5 px median FIRST.
Without the median a dense band of faint stars scores exactly like texture. And
`verify` reports detail per luminance band rather than globally, because the
model's SIGN flips with the material: the same recipe on the same day invented
texture on a cloud sky at 1.86x and deleted a star field at 0.61x.

**The general form.** Read a master against its OWN neutral ceiling, never
against an absolute number: a noisy star field round trips at 43.10 dB where a
smooth cloud frame reaches 56.91, so 38.60 dB is near ceiling on one and poor on
the other. Same trap as failure 3.

---

## 40. The fidelity gate that condemned an honest enlargement, and the twin that let one through

**Symptom, first half.** `upres.py verify` struck a lossless Lanczos enlargement
of a real graded 1080p job at 7.1 dB below the neutral ceiling, reporting
"whatever it added is not what was there". The candidate had added nothing: it
was the source, enlarged, encoded losslessly.

**Cause.** The reading was taken in RGB. The source was 4:2:0, so its chroma
lives at half raster; ffmpeg enlarged in YUV and reconstructed that chroma its
own way, while the control enlarged the decoded RGB. The deficit was almost
entirely the two chroma reconstructions disagreeing. Three files isolate it:
4:2:0 to 4:2:0 reads 7.07, 4:2:0 to 4:4:4 reads 7.42, 4:4:4 to 4:4:4 reads 0.73.
It is the SOURCE's subsampling that does it.

**Fix.** Gate on luma. The same honest file reads 0.76 dB there and the real
faults still read 8.6 to 9.8, so the fault is an order of magnitude clear of the
nuisance instead of buried under it. The RGB number is kept as evidence beside
it; colour is watched by the colour tag and colour drift checks, which is where
it belongs.

**Symptom, second half.** At the SAME raster the same check printed "against a
ceiling of nan dB" and passed. The control is the source there, so every control
reading is infinite, the mean of no finite readings is a nan, and every
comparison against a nan is False: the strike could not fire. Restore at size is
this department's own stage 3 job, so this was not a corner.

**Fix.** UNPROVEN with the reason, never a pass, and the decision moved into a
pure function so it can be tested with no file at all.

**The general form, and it is the department's own rule turned on itself.**
Before setting a threshold, ask what else moves the number. If a nuisance moves
it further than the fault does, the threshold is measuring the nuisance: here the
chroma path moved it 7.07 dB where the fault moves it 8.6, so the gate was
closer to a coin toss than to an instrument. That is failure 10 and failure 37
again, in a check written after both. And a reading with nothing to read against
is not a pass: reading a master against its own neutral ceiling is failure 3, and
this is what happens when that ceiling does not exist.

**One more trap in the same check.** An exact reduction is necessary and not
sufficient. A nearest neighbour enlargement reduces back to the source exactly,
beating every real resampler, and is unusable; the detail bands are what catch
it, at 1.27 to 1.45 against the control. Never read this check alone.

## 41. A Studio setting refused the value it documents, because of its type

Measured on this machine 2026-08-23, against a live DaVinci Resolve Studio
21.0.4 licence, from an external Python process.

**Symptom.** `project.SetSetting('superScale', '2')` returned **False** and the
setting stayed at 1. Every other project setting in the same API takes a string,
`GetSetting` hands one back, and the call raises nothing: a False return is the
only sign anything happened.

**Cause.** This one setting is an enumerated integer. The string is refused
silently. `SetSetting('superScale', 2, 0.5, 0.3)`, the 2x Enhanced form with
sharpness and noise reduction as floats, returned True on the same clip in the
same session, and `GetSetting` then read back `2`.

**Fix.** Pass the integer, and read the setting back afterwards rather than
trusting the return value. Both forms are now in `10_resolution.md` with the
measured result beside each.

**The general form.** A False return from a Studio API is documented to mean
"you are on the free edition", so it reads as a licence problem and sends you
looking at the wrong thing entirely. It also means a wrong argument type. Never
diagnose an edition from one refused call: `resolve_api.py status` answers it
once, by asking the product its own name.

## 42. The AI mask call that cannot make a mask

Measured on this machine 2026-08-23, Studio licence active, external API
connected, on a real clip cut into a timeline.

**Symptom.** `TimelineItem.CreateMagicMask("F")` returned **False**, instantly,
0.0 seconds. `Stabilize()` and `SmartReframe()` on the same item in the same
session both returned True, so the licence and the connection were not the
problem. The README's own note against the call points at the Studio and AI
prerequisites section, which lists neither Magic Mask nor a missing Extra.

**Cause.** The call does not create a selection. It tracks one that already
exists. Magic Mask is a stroke the artist paints on the subject, and the API
verb propagates that stroke forward, backward or both. With no stroke on the
clip there is nothing to propagate, and the honest answer is False.

**Fix, and the route that does work.** Fusion carries a `MagicMask` tool in its
own registry, 622 tools, confirmed present. Its `Strokes` input has datatype
`MagicMaskStrokes`, which cannot be built from Python, but the node serialises
to a plain text `.setting` file that carries a `MagicMaskStrokes { }` block
verbatim. So a stroke painted ONCE at the screen becomes a template whose
coordinates are then editable as text and loadable with `LoadSettings`, and the
node's own `TrackForward` and `TrackReverse` inputs run the propagation with
nobody watching. One human gesture per shape, not one per shot.

**The general form.** "Scriptable" and "autonomous" are different claims, and a
function list cannot tell them apart. Before promising a step runs with nobody at
the screen, call it on a real clip and look at what it returns. An AI verb in an
API is usually the second half of a gesture, not a replacement for it.

---

## 43. A colour flag that converts instead of tagging, and the control that measured ffmpeg

**Symptom.** A depth check that passed on one machine failed on another with no
change to the code between them. The check builds an 8 bit test pattern, writes
it into a 10 bit ProRes container and asserts that the detector calls the result
promoted. On Linux it read 0.9098 of its luma on the multiple of 4 lattice. On
the Mac the identical command read 0.3175, and the same detector then called the
same content native 10 bit.

**Cause.** The clip was written with `-colorspace bt709 -color_range tv`, which
reads like metadata and is not. Where the source carries no colour tags, ffmpeg
inserts a scaler between the untagged input and the tagged output, and that
scaler performs a real colour matrix conversion. A matrix multiply lands every
sample off the 4x lattice, so the promotion signature the check was written to
find had been destroyed by the act of writing the file. The two machines differ
only in ffmpeg version: the older build tagged, the newer one converts.
Isolated by building the same clip five ways. With no colour flags at all,
0.9098. With `-color_range tv` alone, 0.9098. With `-colorspace bt709` present,
0.3175, whether or not the range flag is there. Tagging the SOURCE with
`setparams` and keeping both output flags, 0.9098 again, and 594 distinct codes
on both machines, which is the Linux reading to the digit.

**Fix.** Tag the source, so the conversion is a nothing and the promotion stays
the pure multiply it claims to be. Then build the negative control from raw
bytes rather than from a filter, so the property under test is in the file
rather than in what ffmpeg decided to do. And assert the SEPARATION between the
two controls, not just their verdicts: labels alone still pass if a future build
moves both readings together.

**The general form.** A flag that looks like metadata may be an instruction. If
a control's value changes when nothing about the subject changed, the control is
measuring the tool chain, and every threshold calibrated against it is really a
threshold on that machine's build. Ask what the file went through between the
content and the measurement.

---

## 44. The flat frame accused of being 8 bit

**Symptom.** A genuine 10 bit master read as 8 bit content in a 10 bit
container, decisively, with the verdict "on a lossless path that is decisive".
The frame in question was a slate.

**Cause.** Two rules decide the depth. The first counts distinct codes: a
promotion is injective, so 8 bit content promoted losslessly still carries at
most 256 of them. The converse does not hold and was being used as if it did. A
flat card, a black frame, a title card or a lockup carries a handful of codes
honestly, and the rule read that as promotion. The second rule, the lattice
fraction, fails the same way from the other side: a single sample value that
happens to be a multiple of 4 sits on the 4x lattice 100 per cent of the time.

**Fix.** Two guards, and they catch different files. A promotion multiplies
every code by the step, so every code is a multiple of it: where the greatest
common divisor of the codes is not, no promotion can have produced them however
few they are. That catches the card at code 513 and does nothing for the card at
512. The second guard is an evidence floor, 16 distinct codes, below which the
tool returns `measurable: false` and declines to answer at all. Sixteen codes
all landing on the lattice by luck is a 1 in 4^15 event; one code landing on it
is a coin with one side.

**The general form.** A test whose evidence can run out needs to know when it
has. Both rules here were happy to answer from a single sample, and the answer
was an accusation against a deliverable. Where the material carries no
information, the honest verdict is that the question is not measurable on it,
which is a different output from either yes or no.

---

## 45. The engine declined, and the line above it answered anyway

**Symptom.** The depth reading on a flat 10 bit card, with the evidence floor
from failure 44 already in place and working, printed this:

```
card.mov: declares 10 bit, carries 10 bit.
  only 1 distinct code, too few to tell promoted content from native.
  lattice 4x (source 8 bit): 100.0 per cent of samples, chance level 25 per cent
```

Three lines, and the first and the third both answer a question the second one
says cannot be answered. The header hands back the declared depth as though it
had been measured. The lattice line reads 100 per cent against a 25 per cent
chance level, which is the shape of damning evidence, and it is one sample
value landing on a multiple of four.

**Cause.** The floor was added to the engine and not to the renderer. The
engine returns `measurable: false` and leaves `effective_bit_depth` at the
declared value, because a missing number would break a caller that does
arithmetic on it. The printer read that field without reading the flag beside
it. Nothing in the suite covered the printed line, so the whole rendering path
was free.

**Why it matters more than a cosmetic slip.** `reference/07_delivery.md` sends
the operator to `spec.py depth` to prove the depth of a delivered master rather
than trust the pixel format tag. The default reads three frames from frame 0,
and the first frames of a real film are a slate, a black frame or a title card.
So the default invocation, at the delivery gate, on ordinary material, lands on
exactly the case that cannot be measured and printed a clean bill for it.

**Fix.** The header prints UNPROVEN in the slot where the carried depth goes,
and the lattice line carries "too few codes to be evidence". Both are pinned by
tests on the rendered text, which needed no clip: the printer takes a plain
dict, so the checks run offline and on a bare interpreter with them.

**The general form.** When an instrument learns to say "I cannot tell", every
surface that reports it has to learn the same word on the same day. UNPROVEN is
not a pass, and a renderer that prints the declared value into the measured
slot converts one into the other silently. Grep for every reader of the field
the new flag qualifies, not only for the callers of the function.

---

## 46. The interpolator did not deliver the phase it was asked for

**Symptom.** A held-frame slow motion was rebuilt at true fractional phases, so
every slot was a distinct picture and no frame was repeated. It still pulsed,
gently, at the rate of the original source frames.

**Cause.** The frame interpolator's timestep is not the fraction of the move it
delivers. Measured on three source pairs of the shot:

    asked  0.0  0.1  0.2  0.3  0.4  0.5  0.6  0.7  0.8  0.9  1.0
    got   .003 .003 .147 .275 .403 .520 .639 .760 .903 1.00 1.00

Flat at both ends. A slot asked for 0.93 of a pair lands on the next source
frame outright, and the following slot, asked for 0.29 of the NEXT pair, has
already travelled 0.27. So every gap that straddles a source frame carries about
a third of the ground it should.

**Fix.** Measure the curve on the material and invert it: ask for the timestep
that DELIVERS the phase wanted. On that shot it took the moving panel from three
effectively frozen gaps and a worst step of 2.17x median to zero and 1.95x. Put
the inversion in the tool, not in a note: the lesson was written down and the
tool was still passing the raw value the same hour.

**The general form.** A control input is a request, not a measurement. Before
building anything on a parameter, measure what the machine does with it across
its range. **No duplicate count or stall test can see this fault** because
nothing is repeated and every frame is distinct: only tracked displacement per
gap shows it.

---

## 47. One flow field across a split screen

**Symptom.** A rebuilt split screen showed one subject's motion bleeding faintly
into the other subject's half.

**Cause.** A single optical flow field was estimated across the whole frame. The
two halves are unrelated pictures that happen to share a raster, so the field
smeared motion across the divider.

**Fix.** Find the divider's own centre in the picture rather than assuming the
midpoint, warp each panel independently, and composite. On that frame the left
content ended 9 px before centre, a static gap ran 15 px, and the right panel
started 7 px after.

**The general form.** A frame is not necessarily one scene. Anything that
estimates a global field over the picture, flow, exposure, grain, stabilisation,
needs to be told where the seams are.

---

## 48. The check broke itself and reported the disk empty

**Symptom.** A post-deletion verification loop reported that all eight surviving
4K masters were zero bytes, which would have meant the cleanup had destroyed
every live head on the job.

**Cause.** The loop read each manifest line into a shell variable named `path`.
Under zsh that name is bound to the interpreter's own `PATH`, so the first
iteration replaced the search path with a filename and every `stat` afterwards
was not found. The size lookup had a `|| echo 0` fallback, which turned "command
missing" into "file is empty".

**Fix.** The files were listed directly before the report was believed; they
were intact. Rename the variable, and call absolute binaries inside verification
loops so a broken environment fails loudly.

**The general form.** A tolerant fallback inside an instrument converts its own
breakage into a confident measurement about the thing it was pointed at. Reserve
fallbacks for values, never for whether the tool ran. When a check reports a
catastrophe, confirm the catastrophe by another route before acting on it.

---

## 49. The seek that had been one frame late for as long as anyone had looked

**Symptom.** None. That is the entry. Every still pulled by frame number out of
every master on this machine was the frame after the one it was named for, and
nothing downstream noticed, because a frame that is one late is still a frame
and every count still agreed.

**Cause.** Two faults, both measured 2026-08-31 against ffmpeg 9.0.1, on
numbered clips built so a frame can be identified from its own pixels.

**One: packet order is not display order.** `ffprobe -show_packets` hands them
over in DECODE order. On any long GOP file with B frames the Nth packet is not
the Nth picture, so indexing that list by frame number reads a different
frame's timestamp. Asking a 48 frame h264 file for frame 0 computed a seek that
landed on frame 2.

**Two: the midpoint rule is a measurement of a decoder, not a rule.** Seeking to
a time strictly inside frame N now returns frame N+1, on ProRes and on h264
alike, at every frame tested. On the LAST frame the midpoint lies past the end
of the picture and the seek returns nothing at all. Measured on the pre-fix
code, four frames asked for on each of two codecs, eight of eight wrong:

    long GOP    asked 0, 7, 12, 31   ->   got 2, 8, 13, nothing
    all intra   asked 0, 7, 12, 31   ->   got 1, 8, 13, nothing

**How it survived.** The self test check on it read `start < seek < end`. It
asserted the ARITHMETIC of the number the function returned and never once
looked at the picture that came back, so it passed throughout. This is the
department's own recurring fault wearing a different hat: a true number about a
different quantity than the one asked.

**Fix.** Timestamps are sorted into display order, and the file reports when
that mattered. The seek is no longer derived and asserted: candidates are
generated, each one is TRIED, and the one whose picture matches frame N walked
from the HEAD of the file is the answer. `verified` says whether that happened,
and where no candidate lands the tool refuses rather than returning a plausible
number. `floor` refuses outright on an unverified seek, because a generation
floor read off the wrong frame is a confident measurement of the wrong picture.

The head walk is the only thing on the machine entitled to say which picture
frame N is, because it never asks the decoder to jump. It runs at a tiny raster
and hashes the bytes: that answers "is this the same picture", never "what level
is it", so it stays an identity instrument and never a colour one.

**The general form.** A recipe that reads off a machine is a measurement with a
date on it, and the machine gets updated underneath it. Anything of the form
"the seek for frame N is", "the flag that means", "the timestep that gives" has
to be re-proved against the machine in front of you, by the tool, every time,
and cheaply enough that nobody is tempted to skip it. See failure 46, which is
the same fault in the interpolator.

---

## 50. The sound decided how long the film was

**Symptom.** A master went out one frame shorter than the film the client
already had, and every check in the build passed.

**Cause.** The renderer muxed finished picture against the delivered master's
PCM with `-shortest`. The audio was 51.578688 s; 1238 frames at 24 fps is
51.583333 s. Four milliseconds. `-shortest` cut the PICTURE to the sound and the
master came out at 1237 frames.

**Why nothing saw it.** Every frame check in that build compares index to index,
and a film that is one frame short passes all of them on the 1237 frames it does
have. Nothing in a bit identity proof asks how many frames there are supposed to
be. The hash taken afterwards is a confident record of a truncated film.

**Fix.** `prove.py length FILE --against PREVIOUS.mov` (or `--frames N`), run
BEFORE the master is hashed. It strikes on a changed frame count or a changed
rate, and on a single file it flags the fingerprint the fault leaves: the
picture ENDING BEFORE ITS OWN SOUND. Reproduced in the self test by muxing one
clip against one four millisecond short PCM two ways: `-shortest` gives 47
frames and sits 0.90 of a frame short of its sound, a plain mux gives 48 and
sits 0.10 of a frame long.

Never use `-shortest` on a mux whose picture is the deliverable. Mux with no
length flag. If a container really needs equal lengths, pad the audio; never
trim the picture. A four millisecond mismatch is normal and harmless. Letting it
decide the picture length is not.

---

## 51. A client's returned sound was our own mix, driven into the ceiling

**Symptom.** A cut came back from a client with what looked like a new mix on
it, louder and fuller.

**Cause.** It was our mix at a fitted gain of exactly 1.98981, which is
+5.98 dB, clipped flat wherever it hit the top. 65,611 samples sat on the
ceiling in 26,076 consecutive pairs, against 408 samples and no runs in ours.

**Why the meter said nothing.** A true peak number cannot answer this. A file
driven into a limiter and flattened reads a perfectly compliant peak, and a
clean AAC decode overshoots on a few hundred isolated samples and is fine. The
two are told apart only by whether the samples at the ceiling are CONSECUTIVE.
No picture check and no duration check can see it either.

**Fix.** `audio.py clipping FILE` counts samples at the ceiling and the RUNS of
them, per channel and together, decoding to 32 bit float so the instrument
itself clips nothing. Every channel is counted separately as well as together,
because a fault living in one channel is invisible in a sum.

**And the reason it was our own mix that mattered.** Fit `theirs = k * ours` on
the span both files share, EXCLUDING samples already at the ceiling, and the
divergence point falls out sample accurate. On that job it landed on the same
frame their picture changed, so the answer was our own audio up to that frame
and their new tail from it, divided back by the fitted gain. Their tail had zero
clipped samples. Measure the ceiling before carrying a client's sound into a
master, and say plainly that their version was louder and why it was not used.

---

## 52. The ceiling meter would have killed the machine on a feature

**Symptom.** None on any file this suite ever ran. `audio.py clipping` answered
correctly and quickly on every fixture and on every clip of a two minute film.

**Cause.** It handed ffmpeg's entire 32 bit float decode back through
`capture_output=True` and then copied it again into an `array`, and then walked
it one sample at a time in Python. Measured on this Mac, three files, dead
linear: **14.2 bytes of peak memory per SAMPLE** (0.829 GB for 57.6 M samples,
1.233 for 86.4, 1.637 for 115.2). A 90 minute 5.1 master at 48 kHz is 1.56 G
samples, so about **22 GB** of peak on a 24 GB machine, and `SKILL.md` offers
this as a MASTER GATE. The CPU side was the smaller half here (28 M samples/s)
and the larger half on the Linux box (7.9 M/s, 3.3 minutes for that master).

**Why nothing caught it.** Every fixture is half a second long. A resource fault
is invisible to a check that only asks whether the number is right, and the
number was always right.

**Fix.** The decode is streamed in blocks and never held whole, with numpy where
it is installed and a block at a time standard library path where it is not,
because the rest of the sound department is standard library and a hard numpy
import would change which interpreter can run it. Peak is now flat: six times
the samples cost 1.00 times the peak, measured.

**What a run counter loses when you cut the input into blocks.** A run of
samples on the ceiling that straddles a block boundary is two runs to a counter
that forgets, so the count nearly doubles: 1474 against the true 716 on the
fixture. The carry is checked by reverting it and requiring a DIFFERENT answer,
and the whole thing is checked at block sizes of 1, 13 and 997 frames against
the same file read in one block.

---

## 53. A proof that costs O(N) has no way to be afforded, so it gets skipped

**Symptom.** `prove.py floor` on a late frame of a long master appeared to hang.

**Cause.** The seek proof walks the file from the head, decoding and digesting
every frame before the one asked for, and `generation_floor` pays it TWICE
because it reads two files. Measured 31 Aug 2026: 1308 frames/s on 1080p ProRes
422 HQ and 346 on 4K on this Mac, 147 on 1080p on the review machine. So a late
frame of a 90 minute 4K master is about 6 minutes here and about 2 hours there,
each way. `seek` had `--no-verify`; `floor` did not plumb it and refused
outright on an unverified seek, so there was no way to ask for a number at all.

**Why the walk existed.** The picture is the only evidence a seek has, and a
derived seek was wrong on this machine at every frame tried.

**Fix.** The fault the walk catches is a property of the DECODER and the FILE,
not of the frame index: the old midpoint rule missed 4 of 4 frames on both a
long GOP and an all intra file, on two machines and two ffmpeg majors. So it is
measured where it is cheap and the LABEL at the target is proved a different
way: a short head walk picks the winning rule at a frame whose picture is unique
there, the timestamp read back off that same picture is required to be that
frame's own (which validates the timestamp instrument on this file), the rule is
then applied at the target and its timestamp required to be the target's, and a
three frame local run is required to put the target's picture one frame after
frame N-1's. Head walk up to frame 300, calibrated past it, `--walk` forces the
walk, and every answer says which it used. Flat 0.28 s against 0.32 to 1.57 s
over frames 200 to 2999, same seek at every depth.

**The controls.** A decoder that is right at the head and late at the target has
to be REFUSED rather than calibrated around, and so does one that is late
everywhere; both are stood in for and both gates fire. A head that is all one
picture cannot calibrate anything, and the answer there falls back to the walk
and says so.
