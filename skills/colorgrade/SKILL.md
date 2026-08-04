# colorgrade

Colour grade a whole video without a human at the screen. Send in a file, get
back a graded file, a before and after contact sheet, a consistency report, and
one .cube LUT per shot that a colourist can drop straight onto the clip in
Resolve.

Two tracks, and they answer different questions.

**Track one, the whole picture.** Cut the video into shots, measure every shot,
balance the shots to each other, lay one look over the top, check the result is
actually consistent, render. This is the common case and it is fully unattended.

**Track two, one object.** Change the colour of one thing in frame without
touching anything else that shares its colour. This ships as a DCTL, not a LUT,
because a DCTL is handed the pixel coordinate and a LUT is not.

Use the ffmpeg based `video-editing` skill for mechanical cuts, and the
`davinci-resolve` skill when the result must live inside a Resolve project.

## Setup

Needs its own Python environment, never the bot's:

```bash
python3 -m venv ~/.venvs/colorgrade
~/.venvs/colorgrade/bin/pip install numpy pillow scipy scenedetect opencv-python-headless
```

ffmpeg and ffprobe must be on PATH. Everything below assumes
`PY=~/.venvs/colorgrade/bin/python` and that you are in the skill directory.

Check the maths before trusting a result:

```bash
$PY scripts/selftest.py          # no video needed, must end "0 failures"
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
`04_sources.md` the primary documents every claim traces to, and `00_library.md`
the grading guides, kept outside the repo because of their size.
