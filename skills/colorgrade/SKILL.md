# colorgrade

Colour grade a whole video without a human at the screen. Send in a file, get
back a graded file, a before and after contact sheet, a consistency report, and
one .cube LUT per shot that a colourist can drop straight onto the clip in
Resolve.

Four tracks, and they answer different questions.

**Track one, the whole picture.** Cut the video into shots, measure every shot,
balance the shots to each other, lay one look over the top, check the result is
actually consistent, render. This is the common case and it is fully unattended.

**Track two, one object.** Change the colour of one thing in frame without
touching anything else that shares its colour. This ships as a DCTL, not a LUT,
because a DCTL is handed the pixel coordinate and a LUT is not.

**Track three, the picture itself.** Frames that do not advance, a shot that
judders, one panel of a split screen running slower than the panel beside it,
and a colour step that lands inside a shot with no cut. These are not colour
problems and no amount of grading fixes them. Find them, rebuild them, prove the
rebuild, splice it in. **Do this track before colour**, always: rebuilding
frames afterwards means rebuilding them from source, which silently drops any
colour repair that lands on them. It can share the grade's render pass.

**Track four, matching a series.** Landing a new film where its already approved
siblings landed, which is not the same thing as giving it their look values.

Use the ffmpeg based `video-editing` skill for mechanical cuts, and the
`davinci-resolve` skill when the result must live inside a Resolve project.

## Setup

Needs its own Python environment, never the bot's:

```bash
python3 -m venv ~/.venvs/colorgrade
~/.venvs/colorgrade/bin/pip install numpy pillow scipy scenedetect opencv-python-headless
~/.venvs/colorgrade/bin/pip install torch torchvision      # track three only
```

ffmpeg and ffprobe must be on PATH. Everything below assumes
`PY=~/.venvs/colorgrade/bin/python` and that you are in the skill directory.

Check the maths before trusting a result:

```bash
$PY scripts/selftest.py          # colour maths, no video needed
$PY scripts/selftest_tools.py    # picture tools, builds its own test clip
$PY scripts/selftest_frames.py   # frame repair, builds its own clip
```

All three must end "0 failures".

The frame rebuild in track three wants RIFE. Nothing else does, and it falls
back to RAFT without it, but the fallback is materially worse and has failed the
colour verdict on real footage where RIFE passed:

```bash
bash scripts/setup_rife.sh       # ~80 MB of MIT licensed weights
```

## Track one: grade a whole video

```bash
$PY scripts/cg.py grade IN.mp4 --look kodak2383 --out OUT.mp4
```

That one line does all of this:

1. finds the shot boundaries
2. samples frames from every shot and measures them
3. decides whether each shot is camera original or rendered graphics
4. balances the camera shots to the video's own centre of gravity
5. matches the residual, measured on the already balanced picture
6. lays the look over every shot identically
7. re measures, and raises the match if the shots still do not agree
8. bakes one LUT per shot, choosing the LUT size by measuring its error
9. renders in a single decode and a single encode, audio copied through
10. writes a contact sheet, a report, and the LUTs

Step 9 is one straight chain of lut3d filters, each switched on over its own
frame range, so peak memory scales with the frame and not with the runtime: a
feature costs the same as a trailer. Measured on a 45s 2560x720 piece with 30
shots, 0.63 GB peak. Size any memory cap by resolution, and remember that a
service unit with `OOMPolicy=stop` takes the whole bot down when one child
breaches the cap, so a long grade is worth running in its own scope.

Look first, then decide:

```bash
$PY scripts/cg.py analyze IN.mp4              # measure only, change nothing
$PY scripts/cg.py looks                       # list the look library
$PY scripts/cg.py grade IN.mp4 --look warm_doc --no-render   # sheet and LUTs only
```

### The flags that matter

`--look NAME` one of the library below, or a path to a look json. `neutral`
means balance and match only, no creative move.

`--normalize match` (default) pulls every shot toward the video's own centre.
This preserves whatever the piece already looks like and only removes drift
between shots. `--normalize full` pulls every shot toward absolute neutral
targets, which is right for mixed AI generated clips with no shared intent and
wrong for anything with a deliberate look. `--normalize off` applies the look
and nothing else.

`--reference N` matches everything to shot N. By default the grader picks the
shot closest to the centre, weighted toward longer shots, because a long shot
carries the piece and should not be the one that moves.

`--cuts 120,340,512` supplies the cut frames instead of detecting them. Needed
whenever detection is untrustworthy, and always when the change between shots
is colour only: content based detection compares picture content, and between
two identically framed shots that differ only in grade it correctly sees none.

`--match-strength 0.65` how much of the residual gap to close per shot. On the
ground truth harness 0.45 leaves 1.41 dE and 0.65 leaves 0.97, and it saturates
there.

`--cap-exposure 1.5` `--cap-wb 0.18` the safety limits. Every correction is
capped, and every cap that binds is printed. A wrong reading can slow the
grader down but it cannot wreck a shot.

`--lut-size 0` measures the bake error and picks 33 or 65 automatically.

### Reading the report

The grader prints consistency before and after, in dE2000. A difference of 1.0
is roughly the point where two flat patches become distinguishable at all.
Under 3.0 across a cut is called consistent, 3 to 6 is called close, above 6 is
called visibly jumping.

It also prints every correction that hit a cap. That list is the useful part:
if half the shots are capped, the footage is not a candidate for automatic
balancing and the report says so rather than quietly producing mush.

## The look library

Parametric, not baked. Each look is a small json of tone, colour and gamut
values that is evaluated per pixel and only then baked to a LUT.

| look | what it is |
|---|---|
| `neutral` | no creative move at all, balance and matching only |
| `clean_commercial` | punchy, neutral, bright. Corporate and product |
| `kodak2383` | print film. Dense blacks, gentle highlight rolloff, warm mids, dye crosstalk |
| `teal_orange` | shadows to teal, skin held on the orange line |
| `bleach_bypass` | silver retained. Hard contrast, drained colour |
| `nordic` | cool, quiet, low saturation, lifted blacks |
| `warm_doc` | natural and warm. Interview and documentary |
| `vintage_70s` | faded print. Milky blacks, yellow green cast |
| `noir` | near monochrome, hard contrast |
| `sunlit` | golden hour. Warm highlights, long shoulder |

Bake any of them to a standalone LUT:

```bash
$PY scripts/cg.py lut --look kodak2383 --out kodak.cube --size 65
```

## Track two: change one object

```bash
$PY scripts/dctlgen.py FRAME.png --at 0.877,0.745 --hue-shift -25 \
    --out MyObject.dctl --preview proof.png
```

`--at x,y` is a seed point inside the object, in 0 to 1 from the top left. The
generator grows the object out from there, measures the hue band and the box
that contain it, bakes those in as slider defaults, and proves the aim on the
still before the file goes anywhere.

Into Resolve, and this is the only route that works: Effects, then OpenFX, then
ResolveFX Color, then drag the effect called DCTL onto the node, then pick the
file in the DCTL List dropdown. Dragging a .dctl from the LUT browser does not
work for a DCTL that carries UI parameters. Verified on a Resolve Studio 21
machine.

The file arrives with sixteen live controls, fifteen sliders and the Show Matte
tick, so the colourist finishes the aim by dragging rather than by asking for a
re render.

### Proving a DCTL without Resolve

`dctl/dctl_host.swift` compiles a .dctl as a Metal kernel and runs it over a PNG
on this machine's GPU. macOS ships the Metal compiler, so no Xcode and no
Resolve is involved.

```bash
swiftc -O dctl/dctl_host.swift -o /tmp/dctl_host
/tmp/dctl_host MyObject.dctl frame.png out.png --set showMatte=1
```

It writes the graded frame and the matte, and prints the compile time and the
frame rate. Read what it proves narrowly: a pass means the code is valid GPU
code and the pixels are real GPU output. It does **not** mean Resolve will
accept the file. Resolve's own translator is stricter than Metal's, and the
first LensIsolate passed here and was still rejected by Resolve. The rule that
came out of that: only use constructs that appear in the sample .dctl files
Blackmagic ship at `/Library/Application Support/Blackmagic Design/DaVinci
Resolve/Developer/DaVinciCTL/`. Two in particular are rejected. Passing
`__TEXTURE__` into a helper `__DEVICE__` function appears in none of their
thirteen samples, and writing alpha only works through the ResolveFX DCTL
plugin. `reference/03_failures.md` has the full account.

`GainDCTLPlugin.dctl` in that same Blackmagic folder is the control to reach for
if a file is rejected: it is theirs, so if it also fails, the problem is the
install or the route and not the generated code.

## Track three: the picture itself

Run this before grading. Start by asking whether the frame is one picture:

```bash
$PY scripts/cgpanel.py panels IN.mp4
```

If it finds a divider, **every measurement and every repair from here is per
panel**. On the film this was built for, whole frame cut detection found 17 cuts
and per panel found 23; whole frame stall detection found 14 frozen frames and
per panel found 54, and the real fault was one half of the screen running at 18
pictures a second while the other half ran at 23.5. Averaged over the frame that
is invisible.

```bash
$PY scripts/cgpanel.py cuts   IN.mp4 --band 0,1940      # cuts in one panel
$PY scripts/cgpanel.py gutter IN.mp4 --range 361,411    # where to switch, per shot
```

### Frames that do not advance

```bash
$PY scripts/cgframes.py repair IN.mp4 --work WORK --out OUT.mp4
```

Six steps, and each is also a command of its own so a job can be inspected part
way: `census`, `plan`, `build`, `gate`, `render`, `verify`. The working directory
is reused across them and every step is resumable.

Grading and repair belong in the same render. Pass the LUT to the repair and
there is one decode and one encode instead of two:

```bash
$PY scripts/cg.py grade IN.mp4 --look kodak2383 --no-render     # LUTs only
$PY scripts/cgframes.py repair IN.mp4 --work WORK --out OUT.mp4 --lut shot_00.cube
```

When the colour repair is several LUTs over different frame ranges, which is
what a film with a few replaced shots needs, pass the chain instead. Still one
decode and one encode:

```bash
$PY scripts/cgframes.py render --work WORK --out OUT.mp4 \
  --vf "lut3d=file=a.cube:enable='between(n,700,1018)',lut3d=file=b.cube:enable='between(n,2046,2243)'"
```

### The census reads two things, and either can call a frame frozen

The mean step, and how much of the frame moved. The second exists because the
first cannot see a frozen plate under a moving graphic: live action conformed
from 18 fps with an animated overlay running over it at 24 keeps the mean at
0.26 of the shot's typical step, and a whole shot of repeats was reported as
none. Under detection is not a safe failure here. Finding 20 of 36 repeats made
`plan` call a retimed shot a set of holes, and filling holes in a retimed shot
leaves the wobble the repair exists to remove. `03_failures.md` entry 20.

### The one rule

**A frame that goes back into a film never leaves the film's own colour space.**
Motion is estimated in RGB and returns a vector field. The warp, the blend and
the splice all happen on the native yuv420p planes. A yuv to RGB to yuv round
trip shifts the picture by around 0.8 of a code level even with the right
matrix, and with the wrong one by 2.79 levels mostly green. That is a constant,
so it lands on every rebuilt frame and none of the others, and a shot carrying
one rebuild every three frames then flashes eight times a second while every
still looks perfect. This rejected a delivery. `reference/03_failures.md` entry 11.

### Two faults, opposite repairs

A list of frozen frames is the same list for both.

**Holes**: the shot runs at the right rate with individual frames stuck. Fill
each one from the two real frames either side and leave everything else where
the editor put it.

**Broken cadence**: the shot was retimed by repeating frames, so it advances at
perhaps 18 unique pictures a second inside a 24 fps film. Filling its holes
leaves a wobble at the same period, because the survivors are not evenly spaced
either. The repair rewrites the whole shot and moves the survivors to their true
positions, and it is kept or reverted as a whole shot.

**A deliberate hold is neither**, and it is the dangerous one. Cards, end boards
and settling titles freeze on purpose, and a solid run of frozen frames does not
merely resemble a broken cadence, it SCORES BETTER on the cadence test than a
real one: every gap inside a run is 1, so the beat is perfectly regular. A 20
frame hold in real footage was read as density 0.26, gap spread 0.00, called a
retimed shot, and planned to move 48 real frames. So `census` rejects runs
longer than three frames before the cadence test sees them, and prints where
they are. No rate conversion makes long runs: 24 from 18 repeats one frame at a
time, 24 from 12 every other frame, 24 from 8 in pairs.

`plan` decides per shot and prints the numbers behind each call. Read that table
before building: calling a retime on a shot that only has holes throws away
frames somebody chose. Override with `--cuts` on census if the shot list is
wrong, which is the usual cause of a bad call.

### Generated video: the jump no census can see

Generated clips carry a third fault: every so often the picture genuinely
advances 3 to 4 normal frames of ground in one slot. Nothing repeats, so the
census is structurally blind to it, and the cadence retime can only space
survivors 1 or 2 slots apart, so it cannot repair it either. Measure travel
first, then plan by distance:

```bash
$PY scripts/cgtrack.py IN.mp4 W H            # tracked travel per gap -> IN.mp4.track.npz
$PY scripts/cgmotion.py WORK 73 --report     # sweep the window, keep the shot's arc
$PY scripts/cgmotion.py WORK 73              # then build/gate/render as usual
$PY scripts/cglocal.py IN.mp4 WORK LO HI     # one bad gap: retime a window, in 10 bit
$PY scripts/cgfidget.py OUT.mp4 GUIDE.mp4    # pace vs a real camera, jerk + cross track
$PY scripts/cgverify10.py IN.mp4 OUT.mp4 W H # 10 bit proven off pictures, not the tag
```

`cgmotion.py` rebuilds nearly every frame and does not yet correct the engine's
placement error, so measure its output with `cgfidget.py` against a real camera
clip and state the number; `cglocal.py` closes that loop and reached the camera
floor. The full method, the numbers behind it and the placement story are in
`reference/07_frames.md`; the faults it grew from are `03_failures.md` entries
22 to 24.

### The flags that matter

`--panel x0,x1` repairs one side of a split screen and leaves the other byte
identical. Every measurement is scoped to the panel too. A reading taken across
the divider is two different pictures averaged together.

`--cuts 120,340,512` supplies the shot boundaries. The census is scoped per shot,
so a wrong shot list gives a wrong census.

`--force-mode 0:rebuild` on plan overrides the hole or cadence call per shot,
for the case the printed numbers show is wrong: stalls arriving in pairs score
density 0.117 against the 0.15 the test wants, and their gap series alternates
1 and 23, which reads as irregular. State the override instead of loosening a
threshold. `03_failures.md` entry 22.

`--no-resharp` discards soft rebuilds instead of restoring their detail. The
default restores, which roughly doubles how many repairs survive the gate.

`--against BAD.mp4` on verify runs the colour instrument on a file known to
carry the fault, usually the rejected version, and prints both columns. A test
that reads clean on both proves nothing, and verify says so.

`--approved lo,hi` on verify checks that a range the client already signed off
comes out of this render as it came out of the one they signed off.

### Before committing on unfamiliar footage

```bash
$PY scripts/cgframes.py holdout --work WORK
```

Hides a real frame, rebuilds it, and scores the rebuild against repeating the
previous frame, which is what the stuttering file already does. If motion
compensation does not win here it will not win in the film either, and the
honest answer is to leave the stutter in rather than trade it for softness.

**Read all three scores, not the error.** Error alone prefers a blurry answer,
because a soft rebuild sits closer to the truth on average than a crisp one
whose detail is a pixel out of place, so a holdout scored on error picks the
softest method every time. That is how a rebuild once shipped 10 to 25 per cent
softer than its neighbours, reading as the subject twitching sideways for one
frame, which no measurement of motion can see. `detail` is gradient energy
against the real frame, where 1.0 is right and above 1 is usually ghosting
rather than sharpness, since a double edge carries more high frequency than a
single one. `placed` is correlation of the gradient map, which is the only one
that separates a crisp frame in the right place from a crisp frame in the wrong
place.

### Which engine builds the frame

RIFE where `scripts/setup_rife.sh` has been run, RAFT optical flow otherwise,
and the build step says which. They are not equivalent. RIFE returns a per pixel
fusion mask, so where the two warps disagree it picks one; RAFT averages them,
and averaging two warps that disagree by a pixel IS a two tap blur. On real
footage RIFE kept 32 of 32 rebuilds at 1.01 times the real colour offset, while
RAFT finished at 1.58 times and FAILED the colour verdict, or at 5.01 times with
sharpening off. If the build says RAFT, expect rejections and read them as the
fallback being honest. `CG_FRAME_ENGINE` forces one; forcing `rife` when it is
absent is an error rather than a silent downgrade.

**The timestep RIFE delivers is not the one you ask for**, and the tool now
corrects for that. Measured 2026-08-31: ask 0.1 and the picture moves 0.003, ask
0.9 and it moves the whole gap. Flat at both ends, so every gap that straddles a
source frame carries about a third of the ground it should, and NO DUPLICATE
COUNT OR STALL TEST CAN SEE IT because nothing is repeated. `solve_timestep`
inverts the measured curve; `correct_timestep=False` reproduces a pre 2026-08-31
build byte for byte. A plain 2x rebuild asks 0.5 and gets 0.520, which is why
this only ever bit fractional phase work: retimes, cadence rebuilds, slow motion.

**The retime smoother extends the ramp at the ends, it does not repeat the last
value.** Cumulative travel at a constant speed is a straight line, so padding it
`mode="edge"` flattens it inside the window at both ends and the smoother
reports the camera as nearly stopped there. Measured on a 10 px a frame ramp,
edge padding gave 5.20 at the head and tail against 10.00 in the middle: nearly
half the shot's speed, at exactly the two places a viewer reads as hesitation.
`03_failures.md` entry 27.

`reference/07_frames.md` is the full method.

### A colour step inside a shot

```bash
$PY scripts/cgfix.py iscut  IN.mp4 --at 479 --band 0,1940
$PY scripts/cgfix.py extent IN.mp4 --before 474,478 --after 479,483
$PY scripts/cgfix.py model  IN.mp4 --before 474,478 --after 479,483 --band 0,1940
$PY scripts/cgfix.py apply  IN.mp4 --out FIXED.mp4 --frames 479,510 \
    --before 474,478 --after 479,483 --band 0,1940
```

`iscut` settles the only question that matters first: a real cut and a colour
change look the same in a difference and want opposite treatment. Gradient map
correlation across a real cut reads near zero; across a colour change it reads
whatever its neighbours read, because the picture is continuous.

`model` measures the size on cells that are static in both states, so a moving
object cannot fake it, and tests what can express the change. It fits the delta
rather than the map, and it prints the part of the change that is not a function
of colour at all, which no correction can remove. Say that number out loud rather
than implying the fix is total.

## Track four: matching a series

```bash
$PY scripts/cgseries.py landing   NEW.mp4
$PY scripts/cgseries.py compare   NEW.mp4 APPROVED.mp4
$PY scripts/cgseries.py pivot     NEW.mp4
$PY scripts/cgseries.py primaries NEW.mp4 --cards 0,60 1000,1120
$PY scripts/cgseries.py mask      frame.png --hue 25 --out check.png
```

Match the **result**, not the recipe. Two films given identical look values
landed in completely different places because their footage was different, and
the second read flat. Carry the family's directions and re-derive every magnitude
from the new film's own measurements.

`pivot` is the mechanism under that. The engine pivots contrast at Cineon mid
grey; a film whose own mid sits well below that gets several times less work out
of an identical setting. It reports whether the film is bimodal and refuses to
give one answer when it is, because a two world film has its own median sitting
in the valley between the worlds where almost nothing lives.

`primaries` reads brand colours off title cards at full resolution, because a
card is flat known artwork and usually the only thing two films in a series
genuinely share. It reports both the HSV hue and the Lab hue, which are different
numbers for the same colour: `hue_shifts` gates in HSV by default and rotates in
Lab, so pass `"space": "lab"` on an entry whose centre was measured in Lab.

`mask` renders a mask in magenta so you can look at it. Do this before any claim
that rests on a mask. It settled five separate disputes on one series, every time
by showing that a mask everyone believed selected one thing selected something
else.

## What this does not do

It does not create nodes, draw windows, or build qualifiers inside Resolve.
Blackmagic's scripting API has no call for any of those, checked line by line
against their own developer notes. It hands over finished pieces and a person
wires them.

It does not replace Magic Mask. Resolve 21 Studio already tracks a clicked
object and caches the result as a traveling matte. For one shot on your own
desk that is faster than talking to this skill. The advantage here is running
across a whole timeline with nobody clicking, from a written description, giving
the same answer every time.

It assumes Rec.709 display footage. Log or raw material needs converting first,
and the grader says so when the file is flagged as anything else.

## Reference

Longer notes live in `reference/`. Read `01_method.md` first if you want to know
why the order of operations is what it is, and `03_failures.md` if something
came out wrong, since every entry there is a real failure that was measured on
real footage and then fixed. `02_looks.md` is the per look rationale,
`04_sources.md` the primary documents every claim traces to, `07_frames.md` the
frame repair method in full, and `00_library.md` the grading guides, kept outside
the repo because of their size.

`05_picture.md` is the companion to `07_frames.md` and covers the rest of track
three: split screens, the localized colour repair, and why two decode paths can
never be compared. `06_series.md` covers track four, and both are worth reading
before touching a film that has siblings.

One rule spans everything and is enforced in code. **Two arrays that came out of
different decode paths cannot be compared.** Splicing a numpy downscale into
ffmpeg's scaled output once put the two 11 code levels apart, invented a stutter
at every repaired frame, and reported a repair as making shots 48 per cent
rougher. `cgpanel.stream` returns the filter chain that produced every array and
`require_same_path` refuses the comparison. Use it even when the paths obviously
match.

Two entries in the failure log are not about colour at all. Number 12 records
that the fix for number 11 already existed, in the previous film's job folder,
and was not carried anywhere the next job would look, so the next job repeated
it and a delivery was rejected. Number 18 is the same shape from the other
direction: a rule that had been written down was missing from the code that
shipped, and only building the case on real footage showed it. A repair is not
finished when the symptom is gone at the site it was found. It is finished when
the same class of mistake is impossible at every site and something automated
proves it.
