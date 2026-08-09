# Frame repair: the method, and why the order is what it is

Track three of this skill. A film arrives with the picture sticking: frames that
do not advance, a shot that reads as juddering, a panel of a split screen running
slower than the panel beside it. The job is to make it move properly without
anyone noticing a frame was touched.

Read `03_failures.md` entries 11 to 19 alongside this. Every rule below is there
because breaking it cost a delivery.

`05_picture.md` is the companion to this file and covers the picture faults that
are not frame repair: what a split screen does to every whole frame measurement,
a colour step that appears part way through a shot on part of the frame, and why
two decode paths can never be compared. Its sections 2, 3 and 4 cover the same
ground as this document from the detection side; where the two differ, the code
in `cgframes.py` and the method here are what actually ships.

## The one rule that outranks the others

**A frame that will be spliced back into a film never leaves the film's own
colour space.**

Motion estimation happens in RGB, because that is what a flow network wants. It
returns a vector field. Nothing else from that side of the fence is allowed
through. The warp and the blend run on the native yuv420p planes, and the
finished frame is written as raw yuv and spliced as raw yuv.

The reason is arithmetic, not taste. A yuv to RGB to yuv round trip shifts luma
by roughly 0.8 of a code level and U by 0.5 even when the matrix is right. Read
a bt709 file with a bt601 matrix and it reaches 2.79 levels mean, 26 at worst,
mostly green. That is a constant, so it lands on every rebuilt frame and on none
of the untouched ones. In a shot carrying one rebuild every three frames it
arrives eight times a second, and the film flashes green while every individual
frame looks perfect.

A converted frame among untouched frames is a flicker. There is no version of
this where the conversion is small enough to be safe.

## The two faults, which are opposite

Both show up as a list of frozen frame numbers. They take opposite repairs.

**Holes.** The shot runs at its stated rate and individual frames are stuck.
Fill each hole from the two real frames either side, at the fractional position
the hole sits at. Every other frame stays exactly where the editor put it. This
is the common case and the safe one.

**Broken cadence.** The shot was retimed by repeating frames instead of by
interpolating them, so it carries perhaps 18 unique pictures a second inside a
24 fps film. The repeats arrive on a beat. Filling the holes here leaves a
residual wobble at the period of the original stutter, because the surviving
frames are not evenly spaced in time either: with M survivors shown over N slots
at unchanged speed, exactly N minus M of the M minus 1 steps have to span two
originals, and the ones that do are the ones that moved most.

The repair is to throw the repeats away, recover where the survivors really sit,
and rewrite the whole shot. That means **moving the survivors too**. A plan that
only wrote the new frames would leave the real ones at their old positions and
the shot would come out worse than it went in.

**And a third thing that is neither: a deliberate hold.** A card, an end board,
a title settling, an actor being still. These are frozen frames that were meant
to be frozen, and the only correct repair is none.

They cannot be left to the cadence test to sort out, because a hold does not
merely resemble a broken cadence, IT SCORES BETTER ON THE CADENCE TEST THAN A
BROKEN CADENCE DOES. The test asks whether the repeats are dense and whether
they arrive on a regular beat. A solid run of frozen frames has every gap equal
to 1, so its gap spread is exactly 0.00, which is a more perfect beat than any
real rate conversion ever produces. Measured on real footage: one 20 frame hold,
21.7 per cent of the file, arrived at `plan` as density 0.26 and gap spread
0.00, was called a retimed shot, and planned to move 48 real frames that nobody
had touched, to fix a fault that did not exist.

So detection rejects it first, on run length. No frame rate conversion makes
long runs: 24 from 18 repeats one frame at a time, 24 from 12 every other frame,
24 from 8 in pairs. A run longer than three frames is a hold. `census` sets
those frames aside, says so, and prints where they are, because a hold that is
silently ignored looks exactly like a fault the tool failed to find.

`plan` separates the remaining two by how dense the repeats are and how regular
their spacing is, and prints the numbers behind each decision. Look at that table
before building. Calling a retime on a shot that only has holes throws away
frames somebody chose.

A cross check worth running when the call is close: the **gap ratio**, two frame
gaps against one frame gaps. Near 2 means frames were merely dropped and the
survivors still sit on their true instants, so fill. Near 1 means every picture
sits on a wrong instant, so retime. An earlier version of this measured how far
the survivors sat from even instants instead, read 0.53 frames of wobble on a
shot that had only dropped frames, and called for a retime it did not need; the
gap ratio read 1.72 on the same shot and called it correctly.

A retime is kept or reverted as a **whole shot**. Keeping the frames that passed
and dropping the ones that failed would leave gaps in a timeline that has already
been rewritten, which is a worse stutter than the one being repaired.

## The order of operations

1. **census.** One decode. Sharpness, frame to frame motion and span for every
   frame, at viewing scale. Then, per shot, the frames carrying less than a
   tenth of that shot's own typical motion. Scoped to the shot, so a genuinely
   still shot is not accused of stuttering.
2. **plan.** Holes or cadence, per shot, with the reasoning printed.
3. **build.** Synthesise, in the film's own colour space, on the GPU.
4. **gate.** Judge every synthesised frame against the film's own frames, put
   back the detail a rebuild costs, then decide what goes in.
5. **render.** One decode, one encode, the LUT in the same pass if the film is
   also being graded.
6. **verify.** Prove the colour is flat, prove the stutter is gone, prove the
   sections already signed off did not move.

`repair` runs all six.

## Judging a rebuild

Two readings, both taken on real frames as well as rebuilt ones, so the cut comes
from the film rather than from taste.

**Softness.** The frame's Laplacian variance over the mean of the two real frames
it was built from. A rebuild is a resampled frame, so it can only lose detail;
this is how much. The floor is the 1st centile of the film's own photographed
frames, taken **per shot** wherever a shot has at least twelve of them. A film
wide floor of 0.847 passed rebuilds at 0.890 into a shot whose own real frames
never fall below 0.971. That shot's sharpness is rock steady, so a soft frame in
it has nowhere to hide, while a shot that swings down to 0.748 on its own would
swallow the same frame whole.

**Position.** Where the frame sits between its neighbours: the mean of its two
neighbour distances over the distance between those neighbours. A frame genuinely
on the path scores near 0.5. A frame the flow field has pushed somewhere else
scores above 1 however smooth it looks on its own. The cut is the 98th centile of
real frames, taken only from runs of three consecutive photographed frames, since
a triple containing a frozen frame measures the freeze and not the film.

The neighbours are whatever the **delivered** file will hold at those slots, not
whatever the source holds. Inside a retimed shot the source neighbour is a frozen
repeat that will not be in the delivery at all.

Frames whose shot is not moving are reverted outright: there is no stutter to
remove, and a resampled frame can only be worse than the one that was shot.

## Putting the detail back

A rebuild judged only on softness throws away about half the repairs, which
leaves more stutter in than it should. Rather than bin the frame, restore what
it lost: an unsharp mask on luma only, the amount solved so the sharpness at
viewing scale matches the mean of the two real frames either side, and never
above them. The aim is to remove a difference, not to add sharpening the film
does not have. Chroma is untouched, being half size and carrying no detail worth
recovering.

Guarded on ringing against the real frames either side of that same frame, so a
halo cannot be traded for a number. On that film the median amount came
out at 0.085 and the film kept 151 repairs instead of 85.

## The rulers, and proving them first

Everything is measured at viewing scale, near 1920 wide. At native 4K the
Laplacian is dominated by grain, a resampled frame keeps its grain, and the
reading passes frames that are visibly soft once the picture is scaled to the
size it is watched at.

Everything being compared goes down **one** path. OpenCV area scaling against
ffmpeg bicubic disagree by about 40 per cent on Laplacian variance by themselves.

A frame is handed to that path **whole**, as tagged yuv420p carrying the file's
own range and matrix. A lone luma plane is a full range format, and passing one
skips the 16 to 235 expansion, reading 1.355 times low.

Before anything is measured, `Ruler.check` puts a real source frame through the
per frame path and requires it to reproduce the value the sequential scan already
holds for that frame, to within one per cent. If they disagree, the run stops.
A number produced by an unproven ruler is a number about the ruler.

## Proving the result

`verify` asks four things in the order they matter.

**Is the colour flat.** Whole frame colour offset of every frame against its two
neighbours, on the delivered file. Real frames give the floor, so no threshold
has to be invented. This is the instrument that would have caught the rejected
delivery before it went out.

The reading that decides is the **ratio of the rebuilt median to the real
median**, not a count of frames over a centile. A rebuild is an average of two
frames, so it can only be flatter than a real frame; anything past 1.5 times the
real median is carrying something the film does not have. That rebuild
read 1.01 when it was right and 17.2 when it was wrong.

The centile count is kept as a supporting line but it is too blunt to lead on.
Proved by rebuilding a test clip deliberately through the bt601 round trip: the
medians moved from 0.020 to 0.170 against a real median of 0.054, a plain
failure at 3.13 times, while the count of frames past the real 99th centile
stayed at zero on both files. A handful of real outliers is enough to put that
centile above a genuine fault.

**Was the test live.** Pass `--against` a file known to carry the fault, usually
the rejected version, and the instrument prints both columns. A test that reads
clean on both proves nothing, and the command says so rather than letting a pair
of zeroes look like success. That warning is not decoration: it is what caught
the centile count being blunt, on the first run against a deliberately broken
build.

**Is the stutter gone.** Frozen frame count, source against delivered, on the
same census rule.

**Did anything already approved move.** Pass `--approved lo,hi`. The sections a
client signed off must come out of this render as they came out of the one they
signed off. This is the check a rebuild is most likely to break by accident,
because a section that was previously reused gets re encoded now. On that job
the two approved sections matched to four decimal places on 14 of 15 sampled
frames, worst difference 0.42 of a level.

## Before committing on unfamiliar footage

`holdout` hides a real frame, rebuilds it from its neighbours, and scores the
result against the frame that was actually there. It compares motion
compensation against a plain blend and against repeating the previous frame,
which is what a stuttering file already does. The test crosses a wider gap than
the real work does, so what it reports is a floor on the quality, not a ceiling.

On one of these films motion compensation won on all 35 frames tested. If it does not
win here, leave the stutter in rather than trade it for softness. That is a real
answer, not a failure.

## What is left in, and said out loud

Some stutter belongs to the source and cannot be repaired cleanly. On one of these
films a shot at 2:04 stutters irregularly every five or six frames; what could
be repaired cleanly was, and the rest was left rather than forced at the cost of
visible sharpness. Frames that sit just the wrong side of a centile cut are named
in the report rather than quietly fixed or quietly dropped.

Held frames are not always faults either. One split screen film holds 95 frames inside
two on screen cards deliberately, and every one of those was left alone. Frozen
is not the same as wrong.

## Which engine builds the frame

There are two, and they are not equal.

**RIFE 4.25 is the default** wherever `scripts/setup_rife.sh` has been run. It
returns a flow AND a per pixel fusion mask, and the mask is the point: where the
two warps disagree, it picks one rather than averaging them.

**RAFT optical flow with a warp and blend is the fallback**, used when RIFE is
not set up. It warps both neighbours and averages them, and averaging two warps
that disagree by a pixel IS a two tap blur. That softness is not a side effect,
it is the method.

On a four second cut of real graded footage broken by a 24 to 16 to 24 round
trip, the difference is not a matter of degree. RIFE kept 32 of 32 rebuilds,
held 1.053 of the neighbours' sharpness, sat at 1.01 times the real colour
offset and took the stutter from 32 frozen frames to none. RAFT reached the same
frame count only after sharpening, and still finished at 1.58 times the real
colour offset: **VERDICT FAIL, the rebuilds carry a cast.**

The cast is not caused by the sharpening that compensates for the blur. Turn the
sharpening off and RAFT gets worse on every count: 18 of 32 rebuilds reverted as
too soft, 5.01 times the colour offset, and 27 of the 32 frozen frames still
frozen at the end. The softness and the cast are one fault, and sharpening only
trades some of one for some of the other.

So if the build step says it is using RAFT, expect the gate to reject work, and
read that as the fallback being honest rather than the footage being beyond
repair. Set up RIFE and run it again.

**RIFE runs at full resolution here, not at scale 0.5.** Scale 0.5 is the usual
advice for 4K and it is wrong for this job: that advice is for interpolating
across large motion, where a coarse pyramid is needed to follow a big
displacement, and frame repair works between adjacent frames where the motion is
small. The control is a pair of identical frames, since nothing moved the answer
must be that frame. At 1080p scale 0.5 came back off by 0.883 code levels on
average, worst 177, with the error WORST IN THE CENTRE of the frame rather than
at the edges, so it is invention and not a padding artifact. That is the same
order as the RGB round trip this whole track exists to avoid. Scale 1.0 came
back off by 0.015 levels. At 4K, 0.794 against 0.0095. On held out frames with
real rotation and zoom, scale 1.0 also won outright on all three scores. It is
also faster, because the padding unit is 64 over the scale and a smaller unit
pads less: 0.61s against 1.80s per 4K frame. There is no axis on which 0.5 wins.

`CG_FRAME_ENGINE` forces `rife` or `raft`. Forcing `rife` when it is not
installed is an error rather than a silent downgrade, so that a delivery built
by the fallback can never be mistaken for one built properly.

## Settings worth knowing

Flow is estimated at 1280 wide and scaled up to warp, ON THE FALLBACK PATH ONLY.
RAFT builds an all pairs
correlation volume that grows with the square of the pixel count, so 4K
estimation does not fit on an M series machine and is not needed for camera and
body moves. Measured against held out real frames: bilinear sampling kept 68.7
per cent of the detail of a real frame, bicubic 77.4 per cent for the same
accuracy and the same time, and lifting the estimate from 1024x576 to 1280x720
took it to 80.4 per cent. 1536x864 gave nothing back for half as much time again.
