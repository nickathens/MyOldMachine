# Seedance 2.5 (ByteDance)

Covers: `seedance2.5` (`seedance_2_5`)

Distinct model from `seedance` (Seedance 2.0) and `seedance1.5`, which keep their own guide
(`seedance.md`). Seedance 2.5 is the only model on this route that can **edit an existing
clip** or **extend one in either direction**, and it is the most expensive video model we can
reach. Read this before touching it.

Specs, prices, parameters and validator messages measured live **2026-08-19** on CLI 1.1.23 and
re-verified live **2026-08-23**. Six things changed under the previous revision: read section 1
first.

---

## Overview

ByteDance's Seedance 2.5, written for the route this repo actually reaches: the Higgsfield CLI
model `seedance_2_5`, exposed by the image-gen skill as `seedance2.5`.

Snapshot taken **2026-08-19**, re-verified **2026-08-23**. Correct this file when the route drifts;
nothing else in the repo carries these numbers except `SKILL.md`'s selection tables,
`minimax-h3.md`'s comparison and `scripts/generate.py`'s parameter map, which have to move with it.

### The whole model in six lines

1. Two or three clear sentences beat a paragraph. Dense prompts are the documented failure mode.
2. Slot order carries meaning: subject, action, scene, style, camera, audio.
3. Audio and captions are routed by bracket type, not by prose.
4. A reference does nothing until the prompt says what it governs and what to ignore.
5. Past about ten seconds, stop writing a moment and start writing staged time.
6. Quote every job before running it. Quotes are free, the model is the dearest on the route.

### What it is good for, and what it is not

Reach for it when the job needs **continuity you cannot get by stitching**: one take longer than
fifteen seconds, a character or a product that must survive the whole clip, a correction to a clip
you already have, or a tail extension on footage that exists. It is the only model on this route
that can edit inside a clip or extend one.

Do not reach for it to find a framing or test an idea. At 6.5 credits a second in 720p it is the
most expensive video model in the catalogue, and cheaper models will tell you whether the shot works.

---

## 1. What changed, and what the old guide got wrong

The previous version of this guide was measured on 2026-08-07 through 2026-08-10. Nine days later
the route has moved under it. Everything below was re-probed live on **2026-08-19** with CLI 1.1.23,
and every number in it re-verified on **2026-08-23**. Cost quotes are free, so all of it cost
nothing: run the probes yourself against your own account rather than trusting this page.

### Corrections, most expensive first

**1. 1080p exists now.** The old guide said "480p or 720p only. No 1080p, no 4K." The live schema
enum is `480p`, `720p`, `1080p`, and a 1080p job quotes and validates. 4K is still not exposed:
`--resolution 4k` comes back `Invalid values: resolution=4k (allowed: 480p,720p,1080p)`.

**2. 480p got cheaper.** It was 3.0 credits a second. It is now **2.5**. A 30 second draft fell
from 90 credits to 75.

**3. The video reference discount is gone.** The old guide's headline finding was a second price
tier: any job carrying a video file ran at 4.0 credits a second at 720p instead of 6.5. That tier
no longer exists. Measured today, an `omni_reference` job carrying a video clip quotes 32.5 for
five seconds, which is the plain text to video price to the cent. **Budget every mode at the same
per second rate.**

**4. What actually moves the price is duration, and one mode computes duration for you.** In
`video_edit` the `duration` parameter is ignored outright and you are billed for the length of the
source clip, with a four second floor. Proven by holding the clip and changing the request:

| Source clip | Requested duration | Quote at 720p |
|---|---|---|
| 2 s | 4, 5, 10, 30 | 26 every time (4 s floor x 6.5) |
| 8 s | 5 | 52 (8 x 6.5) |
| 8 s | 20 | 52 (8 x 6.5) |

`video_extension` behaves the opposite way and bills the duration you ask for: 32.5 for five
seconds, 65 for ten, off the same two second source.

**5. `bitrate_mode` is a real parameter now.** The old guide listed it as rejected, an unknown
param carried over from Seedance 2.0. It is in the schema as `standard` or `high`, default
`standard`, and it is genuinely parsed rather than swallowed: a bogus value is rejected with
`Invalid values: bitrate_mode=bogus (allowed: standard,high)`. It does not change the quote.

**6. The 4000 character prompt ceiling no longer fires.** This was the old guide's most quoted
rule. Prompts of 4001, 8000 and 40000 characters all quote without complaint.

Read that for exactly what it is, because the control the previous revision cited does not
reproduce. Re-run on **2026-08-23**: an empty prompt also quotes without complaint, 32.5 credits at
five seconds, so the quote path never looks at the prompt field at all. And the validator answers
with one error at a time, local enum checks ahead of server side range checks: send
`--resolution 4k` and `--duration 99` together and only the resolution error comes back. So what is
proven is narrow, that no length ceiling fires on the quote path. Whether `generate create` accepts
a 40000 character prompt is untested here, and testing it costs credits.

That does not make long prompts a good idea. Nothing here proves the model reads all 40000
characters, and the vendor's own advice is that dense prompts degrade the result. The rule changes
from a hard limit you must count to a craft limit you should respect.

### What held

Everything else survived re-probing: the mode rules and their exact wording, the 50 file and 30
image ceilings, keyframes legal only in `omni_reference`, the duration floor of 4 and ceiling of 30,
the aspect enum, and the silent mode trap. Omitting `--mode` still lets a video reference through
that `--mode t2v` rejects outright, because the rule is written against a parameter the CLI omits
when you do not pass the flag.

### The lesson worth keeping

This catalogue drifts server side while our tables are written by hand. Two of the six corrections
above were price changes with no announcement anywhere. Before any job over about fifty credits,
run the quote. It is free and it is the only current source of truth.

---

## 2. Hard specs and what it costs

Two columns, because they disagree and the disagreement matters. The vendor sells a model; we
reach one route into it, and the route decides what we can actually do.

| | Vendor claim | This route, measured 2026-08-19 |
|---|---|---|
| Duration | up to 30 s, 180 s in a beta long video mode | **4 to 30 s**, whole seconds, enforced |
| Resolution | up to 4K | **480p, 720p, 1080p**. 4K rejected |
| Aspect | 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, adaptive | same, default 16:9 |
| Audio | native, generated in the same pass | `generate_audio`, default on |
| References | 50 files, 30 image, 10 video, 10 audio | identical, enforced by the validator |
| Modes | text to video, reference, edit, extend | all four reachable |
| Prompt length | not published | **no ceiling found** up to 40000 characters |
| Languages | 11 for spoken dialogue | not separately tested here |

The 180 second figure is not a single take. It is repeated extension chained onto itself, still in
beta, and it is not exposed on this route. The 4K figure needs the same caution: several resellers
sell a 1080p and a 4K tier that are upscales of the same 720p render rather than a larger native
one. Whether the 1080p on this route is native or upscaled is not something the API will tell us,
and it costs 45 credits to find out by eye.

### The price model

One rate per resolution, applied to the effective duration. Nothing else moves the number:
not the mode, not `generate_audio`, not `bitrate_mode`.

| Resolution | Credits per second |
|---|---|
| 480p | **2.5** |
| 720p | **6.5** |
| 1080p | **9.0** |

Exactly linear, measured at 4, 5, 10 and 30 seconds. The full grid:

| Duration | 480p | 720p | 1080p |
|---|---|---|---|
| 4 s | 10 | 26 | 36 |
| 5 s | 12.5 | 32.5 | 45 |
| 10 s | 25 | 65 | 90 |
| 15 s | 37.5 | 97.5 | 135 |
| 30 s | 75 | 195 | 270 |

**Effective duration is not always what you asked for.**

- `t2v`, `omni_reference`, `video_extension`: you are billed for the duration you request.
- `video_edit`: `duration` is ignored and you are billed for the **length of the source clip**,
  with a four second floor. A 40 second edit of an 8 second clip is 52 credits at 720p, and asking
  for 20 seconds does not change it.

### Where it sits in the catalogue

At 6.5 a second in 720p it is the most expensive video model on this route, above `flux-video`
at 5.5, `cinematic3` and `cinematic3.5` at 5.0, `seedance` at 4.5 and `h3` at 4.0. In 1080p at 9.0
it is in a class of its own. The old escape hatch, the video reference discount that dropped it to
4.0, is gone.

**480p is a genuine draft tier at 2.5 a second**, which is where `happy-horse` and `seedance-mini`
live. Block the shot out there and commit once.

### Cost discipline

1. Find the framing on something cheap: `kling-turbo` at 1.5, `seedance1.5` at 1.2.
2. Draft the motion here at 480p, 2.5 a second.
3. Commit one roll at 720p, or 1080p if the client will see it full screen.

Three takes of a 10 second shot at 720p is 195 credits. The same three takes drafted at 480p is 75.

### Quoting, and the two traps in it

Quotes are free. Both measurement rounds behind this guide ran to an unchanged credit balance
across roughly forty probes each. Quote everything.

```bash
higgsfield generate cost seedance_2_5 --prompt "..." --duration 10 \
  --resolution 480p --mode t2v --json
```

**Trap one: the wrapper's `--cost` lies about anything with media in it.** `generate.py --cost`
never forwards `--start-image`, `--end-image` or video references, and it drops `--resolution` for
video models. So a wrapper quote for a keyframed or edited job silently comes back as a plain text
to video roll at the 720p default. Quote media jobs through the raw CLI.

**Trap two: resolution travels in `--extra` when you go through the wrapper.** The wrapper's own
`--resolution` flag is the image side and only accepts `1k`, `2k`, `4k`; argparse rejects `480p`
before anything reaches the API.

```bash
# through the wrapper, drafting at 480p
python skills/image-gen/scripts/generate.py "..." --video -m seedance2.5 --duration 10 \
  --extra '{"resolution":"480p"}' --cost
```

### The parameters, live

```
aspect_ratio     auto, 21:9, 16:9, 4:3, 1:1, 3:4, 9:16          default 16:9
bitrate_mode     standard, high                                  default standard
duration         integer 4 to 30                                 default 5
resolution       480p, 720p, 1080p                               default 720p
mode             t2v, omni_reference, video_edit, video_extension default t2v
extension_mode   forward, backward                               only with video_extension
generate_audio   boolean                                         default true
start_image      single image                                    omni_reference only
end_image        single image                                    omni_reference only
image_references array, up to 30 counting start and end images
video_references array, up to 10
audio_references array, up to 10, 50 files total
prompt           required
```

---

## 3. The six slot formula

ByteDance publishes an actual grammar for this model, and the **order of the slots** is what makes
intent legible to it. Only the first two are required.

```
Subject + Action or Event + Scene and Environment + Visual Style + Camera Movement or Cut + Audio
```

### The rule everybody breaks

**Filling every slot with dense detail makes the output worse.** Competing style and camera
instructions fight each other. Two or three clear sentences beat a long paragraph. This is the
vendor's own documented failure mode, not a matter of taste, and it is the exact opposite of the
advice that works on Seedance 2.0, where long timeline blocks are rewarded. Do not carry that habit
across.

Fill the slots you care about. Leave the rest empty and let the model choose.

### The first twenty words carry the most

Lock the subject and the core action at the front. Everything after is modification. A prompt that
opens with three sentences of lighting philosophy and reaches the subject in line four gets a
beautifully lit clip of the wrong thing.

### Describe a take, not a photograph

"A ceramic artist lifts the cup from the wheel and sets it on a shelf" is a take. "A ceramic artist
holding a cup" is a photograph, and the model will hand you a photograph that trembles slightly.

Every element that must move needs **its own verb, inside the action sentence**. A moving thing
named as a noun in the scene slot will freeze:

- Frozen: "A woman at the window, rain outside, a curtain."
- Alive: "A woman breathes slowly at the window while rain runs down the glass and the curtain
  stirs behind her."

Three independent motions, one sentence, fewer words than the frozen version.

### One camera instruction

One move per shot. "Slow push in from wide to medium." Not a push in that becomes an orbit and
ends on a rack focus. Stacking camera moves is the second most reliable way to get mush, after
stacking adjectives.

Camera vocabulary the model reads cleanly: slow push in, dolly in, pull back, tracking shot left,
crane up, steadicam follow, orbit, whip pan, static shot, locked off, top down, low angle,
handheld, gimbal, rack focus to the subject.

Say what the camera does in its own sentence, separate from what the subject does. Mixing them in
one clause is how a camera move becomes a subject move.

### One lighting keyword beats ten adjectives

Lighting is the single highest leverage word in the prompt. Pick one: golden hour, tungsten
practicals, chiaroscuro, soft box three point, neon drenched night, natural available light, harsh
under lighting, diffused overcast, volumetric beams through water.

If a generation comes back flat, change the lighting keyword before you change anything else.

### Colour in three layers

A trick from the tested guides that costs nothing and reads as deliberate grading rather than a
filter. State a base tone for the large areas, a secondary tone tied to the subject, and one or two
accents that recur.

> Desaturated cold blue green shadows and grey concrete, wet dark fur and a navy school jacket,
> a sickly fluorescent glow and a green exit sign.

### The worked example

```
A ceramicist in a linen apron lifts a finished bowl from the wheel and turns it slowly in the
light, in a cluttered studio at golden hour with clay dust in the air, warm 35mm film look with
soft halation, slow push in that settles on her hands. (sparse piano) <wheel slowing, clay scraping>
```

Subject, action, scene, style, camera, audio. Six slots, one sentence and a bracket line, one
camera move, three moving elements. That is the shape.

---

## 4. Audio, dialogue and on screen text

Audio is generated in the same pass as the picture, and it is routed by **bracket type**, not by
prose. These are literal syntax.

| Bracket | Channel | Example |
|---|---|---|
| `( )` | music and ambient beds | `(low cello drone, slow)` |
| `< >` | sound effects and Foley | `<door latch, rain on glass>` |
| `{ }` | spoken dialogue, heard | `{We should go.}` |
| `【 】` | on screen subtitle, seen | `【We should go.】` |

They are optional in the sense that plain description also works. They exist for the moments where
ambiguity costs you a regeneration: a line of dialogue drawn as a caption, a sound effect read
aloud by a narrator. Use them.

### Dialogue and subtitles are separate channels

Writing a line in `{ }` gives you audio with no caption. To have it both heard and burned in, write
it twice, once in each bracket. This surprises everyone once.

### Declare the language before the line

The braces hold **only the words that are spoken**. Everything about how they are spoken goes in
front of them, as plain prose:

```
Dialogue language: British English, warm and low.
{I thought I would never come back here.}
```

The published shape is: language, then regional variant or accent, then delivery, then speaker,
then the line. Eleven languages are supported natively for spoken dialogue: Chinese, English,
Spanish, Indonesian, Malay, Thai, Arabic, Portuguese, Vietnamese, Japanese, Korean.

### Two speakers

Name them, and pin the mouths:

```
Woman: {Are you ready?}
Man: {Let's do it.}
Her mouth moves only during her own lines.
```

That last sentence is not decoration. Without it, both faces tend to animate on every line.

### Music belongs in the prompt

This reverses the hardest rule from Seedance 2.0. That model synthesises audio from loose prose, so
its guide bans music, lyrics and score outright and permits diegetic sound only. On 2.5 music has
its own channel and belongs in `( )`. Carrying the 2.0 ban across throws away a control the model
has.

What survives from 2.0 is the description discipline, not the ban. Keep it to two or three sources.
Name what the scene itself makes.

### Foley discipline

Sound effects stay diegetic and short. `<wheel slowing, clay scraping>` is right. A paragraph of
per object Foley requests is a documented way to make the whole audio pass fail, and the failure is
not graceful: you tend to lose the good sounds along with the impossible ones.

A useful order when the shot has real sound design in it: room tone, then contact sounds, then
dialogue, then distant ambience, then music or silence.

### Silence is a choice you have to make

`generate_audio` defaults to on. Turning it off costs exactly the same, so mute it because you want
a silent clip to score yourself, never to save credits. If you want the sound of a room and no
score, say so: writing nothing in `( )` is not the same instruction as asking for silence.

---

## 5. References, identity and keyframes

Fifty files is the ceiling: up to 30 images, 10 video clips, 10 audio clips. The validator enforces
every one of those numbers, and it counts a start or end image against the image budget.

Files are addressed positionally by type, in upload order: `@Image 1`, `@Image 2`, `@Video 1`,
`@Audio 1`. Note the capital and the space. Lowercase `@image1`, which is the Seedance 2.0 form,
is not this model's syntax.

### The rule that makes references work

**A reference is inert until the prompt says what it governs, and what to ignore.**

```
@Image 1 defines the detective's coat and face; do not use its background.
@Image 2 defines the workbench and the window light; do not use its framing.
```

Without the second half of each line you inherit the reference's background, grade and framing by
accident, and then spend credits wondering why the studio wall keeps coming back.

### One job per reference

Give each file a narrow role, and never give two references the same job. The sharpest version of
this rule, from the tested guides: an image reference and a video reference must not both be
responsible for the same thing. Video references carry motion, timing and performance. Image
references carry look, identity and design.

```
@Video 1 controls only the timing and the weight of the movement.
Do not copy the skater, the clothing, the beach or the concrete from @Video 1.
@Image 2 controls only the product design details.
Do not copy the studio background or its lighting from @Image 2.
```

### Group subjects under role headers

For anything with more than one character, scattering tags through the prose is how identities
bleed. Collapse it into a block, and put the geography in the same line as the identity:

```
[Characters]
<Detective> corresponds to @Image 1. Left third, midground, facing screen right.
            Same face, coat and silhouette throughout. Do not use its background.
<Barman>    corresponds to @Image 2. Right third, behind the counter, turned inward.
            Same face and apron throughout. Do not use its background.

They hold their screen sides and never cross the centre.
```

Place, then identify, then move. Pinning position, depth and gaze before identity is the single
biggest lever against two characters melting into each other, and the explicit ban on crossing is
what stops the swap.

### Budget discipline

The ceiling is 50 files. The practical stability limit is far lower, and the vendor says so:
keep distinct subjects to **eight or fewer** with image references, and **five or fewer** once video
or audio references are in play. Past that, identities start bleeding.

Reference clips have their own bounds worth knowing: each between roughly 1.8 and 30 seconds, and
no more than about 30 seconds total per modality. Keep a motion donor clip short and one idea wide.

### Keyframes: start and end image

2.5 accepts `start_image` and `end_image`. The six slot formula never mentions them, but they are
what makes image to video work here. Two hard rules, both measured against the live validator:

**They are legal in `omni_reference` only.** In the default mode the API refuses outright:

```
generate cost seedance_2_5 --prompt "test" --start-image f.png
    Error: start_image and end_image are only allowed for mode 'omni_reference'
```

**The wrapper will not set the mode for you.** Through `generate.py`, a start image needs
`--extra '{"mode":"omni_reference"}'` beside it or the call fails.

An end frame with no start frame beside it is accepted. Start and end images can travel with
`image_references` in the same call, and all of them count against the 30 image ceiling.

### Do not redescribe what the start frame already shows

If the frame shows the coat, the prompt does not need the coat. Spend the words on what changes.
The reference and the start frame are different jobs: the reference says who this is, the start
frame says where we begin.

### Chaining shots by hand

The most reliable continuity trick has nothing to do with the reference system. Take the final
frame of the clip you have, pass it as the start image of the next, and say so:

```
Use @Image 1 as the exact first frame and continue forward from that moment.
```

That gives the model an actual state instead of asking it to remember a previous generation. It is
also the cheap alternative to `video_extension` when the join does not need to be seamless.

---

## 6. Time, beats and long takes

This is where the previous version of this guide was wrong, so it is worth being precise.

**Short takes want two or three sentences. Long takes want staged time.** Those are not in
conflict, they are two different jobs, and the mistake is applying either one everywhere.

Under about ten seconds, write one continuous action and let the model breathe. Past that, a single
description does not fill the time: the model stretches one idea across thirty seconds and you get
a slow, drifting nothing. The vendor's own long form structure and every tested third party guide
agree on this, and they also agree on the shape.

### The staged form, for 15 to 30 seconds

```
[Generation Goal]  Video type and the central event.
[Stage 1]          Initial state, one primary event, end state.
[Stage 2]          Continue from the previous end state, new event, new end state.
[Stage 3]          Closing event and final state.
[Maintain Consistency]  What must not change across the stages.
```

**One main change and one clear end state per stage.** Two major events in one stage is the
documented way to lose both. Three stages is the natural shape of thirty seconds: establish,
something changes, the subject responds.

### Second level timestamps are first class here

On Seedance 2.0 precise timing was unstable and its own guide discouraged it. On 2.5 it works, and
the launch day sample prompts use it:

```
0s-3s:  She sets the tray down and her hand stays on the rim.
3s-9s:  She turns toward the window, the hand slides off, the curtain lifts.
9s-15s: She stops, and the room settles behind her.
```

Either notation reads: `0s-3s:` or `(0:00-0:05)`. Blocks of five or six seconds are the practical
grain. Each block must start from the physical state the previous one ended in, which is the whole
point of writing them.

Important and easy to miss: **choosing thirty seconds changes the available duration, not the
number of events.** If the prompt only contains one event, thirty seconds makes it slower, not
richer.

### Write the cause before the reaction

Sequence physical events in the order physics does them: contact first, then movement, then sound,
then reaction.

> The edge of the tray catches the cup handle. The cup tips only after contact, strikes the
> counter, and the coffee begins to spread.

Write the spill before the contact and you get a cup that leaps on its own.

Complex movement wants the same treatment, broken into contact phases: approach, contact, force
transfer, recovery. And anything fluid or particulate needs its **settling state** named, not just
its start, or it keeps moving after it should have stopped.

### Re identify anything that leaves frame

When a subject passes behind a column, a car or a crowd, the model treats the far side as a fresh
draw. Restate identity at the moment of emergence, in full:

> The same woman emerges from the opposite side of the column with the same face, hair, green coat,
> black boots and red suitcase, at the same walking speed and in the same direction.

### Tie camera moves to events, not to clock time

> The camera does not pan until the ball has fully left both hands.

> The camera moves parallel and keeps the red player in the left third of frame.

Screen space placement plus a trigger. A camera told to move at four seconds will move at four
seconds whether or not the thing it should be following has happened yet.

### When to split instead

Genuinely sequential beats that cannot share a continuous take are not a prompting problem. Split
them into separate takes and join them with `video_extension` forward, or chain by hand with the
final frame as the next start image. That is the capability 2.5 has and 2.0 does not, and it is the
right answer to the problem the old timeline blocks were invented to solve.

---

## 7. The four modes

Mode is set with `--mode` on the raw CLI, or `--extra '{"mode":"..."}'` through the wrapper. The API
enforces each mode's inputs with its own rules, all of them quoted below verbatim from the live
validator.

### `t2v`, the default

Pure text to video. **Rejects all reference media**, including start and end images:

```
mode 't2v' does not accept reference media
```

#### The silent trap

That rule is written against the `mode` parameter, and the CLI omits the parameter entirely when
you do not pass the flag. Omit it and the rule never evaluates. A video reference is then accepted
at quote and clears validation at submit, in a mode you never chose.

Measured today: with `--mode t2v` spelled out, a video reference is a hard rejection. With the flag
absent and the same reference attached, it quotes 32.5 and proceeds. **Pass `--mode` explicitly on
every call** and the trap cannot fire.

### `omni_reference`

The headline capability, and the only mode that takes keyframes. Requires **at least one** reference
item of any kind:

```
mode 'omni_reference' requires at least one reference media item
```

This is the mode for locking a character, a product or a location across shots. Same price as
`t2v`, billed on the duration you request.

```
A florist arranges stems in a studio. @Image 1 defines the florist's appearance;
do not use the background. @Image 2 defines the workbench and window.
Slow push in from wide to close on the bouquet. (soft room tone) <stems snipping>
```

### `video_edit`

Editing inside a clip you already have. Requires **exactly one** video reference:

```
mode 'video_edit' requires exactly one video reference
```

Aspect ratio and duration are locked to the source. Duration is not merely locked, it is **ignored
and billed from the source**: an 8 second clip costs 52 credits at 720p whether you ask for 5
seconds or 20. The floor is four seconds, so editing a 2 second clip costs 26, the same as editing
a 4 second one.

Structure it in three parts: name the master, define the scope, list what to preserve.

```
Edit @Video 1 from 4 to 7 seconds: change the cool blue light on the right wall to
warm orange. Keep character identity, clothing, position and motion from @Video 1.
```

**The preserve list is not optional.** Anything you do not mention is free to be redrawn. And do
not try to change the aspect ratio in an edit: reframing during an edit is a documented way to lose
the whole shot.

### `video_extension`

Bolts new footage onto a boundary frame. Requires at least one video reference **and** an explicit
direction:

```
mode 'video_extension' requires at least one video reference
'extension_mode' is required for mode 'video_extension'
'extension_mode' is only allowed for mode 'video_extension'
```

- **forward**: the new segment's first frame continues from the source's last frame.
- **backward**: the new segment's last frame must arrive at the source's first frame. Always give
  an explicit end state here, or the picture keeps drifting after it has already reached the join.

Billed on the duration you request, at the normal rate. Aspect is locked to the source, duration is
free.

```
Extend @Video 1 forward. Hold the locked medium shot and the paper airplane's position.
The airplane glides right and exits frame while the curtain sways.
```

### The rules in one place

Every constraint the validator carries, in its own words:

```
mode 't2v' does not accept reference media
mode 'omni_reference' requires at least one reference media item
mode 'video_edit' requires exactly one video reference
mode 'video_extension' requires at least one video reference
'extension_mode' is required for mode 'video_extension'
'extension_mode' is only allowed for mode 'video_extension'
start_image and end_image are only allowed for mode 'omni_reference'
at most 30 images are allowed (counting start_image and end_image)
at most 50 reference media items are allowed in total
duration: Input should be greater than or equal to 4
duration: Input should be less than or equal to 30
```

---

## 8. Craft that carries from Seedance 2.0

Most of what makes a shot good is model agnostic. What does not carry is anything about 2.0's
parameters, its reference syntax, and its ban on music. The four blocks below are the ones worth
the space.

### Capture realism, the anti plastic four

The highest value paragraph in the whole guide. State all four, in one sentence, in the visual
style slot:

1. **Depth through suspended atmosphere.** A thin veil of haze *between* the depth planes, not just
   in front of the subject, so foreground and background separate optically.
2. **Moisture without shine**, only if the scene is genuinely wet. Matte dampness, not glossy beads.
3. **Per zone specular kill on skin.** Name the zones and take the shine to zero: forehead, nose
   bridge, cheekbones, temples, chin. Pair it with a flattering ceiling so matte does not turn ugly.
4. **Contrast curve stated three ways**: lifted blacks, rolled off highlights, low contrast grade.

Four positive locks in one sentence. Positive locks steer this model harder than piles of
prohibitions, which is why the negative budget shrinks while this block does not.

### The static backplate trap

The engine front loads onto the most legible element and treats everything else, including the
subject's own aliveness and the whole background, as a plate to hold still. Three fixes:

- **Every element that must move gets its own verb**, inside the action sentence. Covered in
  section 3, and it is the main one.
- **Open on a different frame from the locked last frame.** Identical start and end invite the model
  to interpolate nothing and hold the still. This applies with force to `video_extension` backward,
  where the join is a locked last frame by another name.
- **Three live layers at different speeds** give parallax depth: foreground drift, the subject,
  background movement. Keep all three slight.

### The breathing dial

The strongest single lever against a photograph that moves. Name the breath. If a shot must have no
visible respiration, reload the life into micro movements instead: one hand regrip, a slow blink,
hair stirred by an updraft, firelight crawling on a wall.

### Lens and light behaviour, never brand names

Describe what the glass does, not what it is called. "Soft halation on the highlights and a shallow
plane of focus that falls off behind the eyes" gets you the look. "Shot on a Cooke S4" gets you a
guess about marketing photography.

The same applies to director and film references, with one caveat: they work, and they work as a
whole style at once. "Wes Anderson symmetry" will bring the palette and the framing whether you
wanted both or not. Use them when you want the whole thing.

### Negatives

Neither model has a negative prompt parameter, so negatives are prose either way. The six slot
formula has no negative slot at all, and dense prose is this model's documented failure mode, so
the budget is tight: **three short items at the very end, and reach for a positive lock first.**

"Gaze held to screen right" beats "no looking at camera". When you do need negatives, the three
with the most leverage are no jitter, no bent limbs, no temporal flicker. That trio is carried from
2.0 and has not been measured here, because measuring it properly costs 6.5 credits a second.

For a shot that must be one continuous take, the useful negative list is different and worth
writing out: no cuts, no slow motion, no repeated action, no duplicated props, no teleportation.

### Registers

The five registers from the 2.0 guide carry as prose. There is no `genre` parameter here, and
sending one is rejected as an unknown param. Write the register into the style slot instead.

---

## 9. What breaks, and the fix for each

Two kinds of failure: the ones that cost credits and the ones that cost the shot. The API rejections
are free, so they are listed first and briefly.

### Free failures, the API rejections

| Symptom | Cause | Fix |
|---|---|---|
| `does not accept reference media` | `--mode t2v` with a file attached | choose the real mode |
| `requires at least one reference media item` | `omni_reference` with nothing attached | attach one, or use `t2v` |
| `requires exactly one video reference` | `video_edit` with none or two | exactly one |
| `'extension_mode' is required` | `video_extension` without a direction | add `forward` or `backward` |
| `start_image and end_image are only allowed for mode 'omni_reference'` | keyframe in the wrong mode | set the mode |
| `Input should be greater than or equal to 4` | duration below the floor | 4 minimum |
| `Invalid values: resolution=4k` | 4K asked for | 1080p is the ceiling here |
| `Unknown params: genre` | Seedance 2.0 habit | write the register into the prose |
| `Invalid values: mode=fast` | Seedance 2.0 habit | `mode` here means capability, not speed |

### Paid failures, and what actually fixes them

**Character drift over a long take.** The known weakness of the model, and it gets worse the longer
the clip. Fixes, in order of leverage: lock identity with an image reference and a "do not use"
clause, restate the identity at every stage boundary, re identify after any occlusion, and if it
still drifts, split into two takes and chain them.

**Two characters swapping faces or clothes.** Place, then identify, then move. Pin each to a screen
side and a depth, forbid crossing the centre explicitly. Section 5 has the block form.

**A clip that is a photograph with a tremor.** The static backplate trap. Give every moving element
its own verb, put life in three layers at different speeds, name the breath.

**Multi subject physics, hands, object handling.** Still an acknowledged weak point across the whole
generation of models. Break the movement into contact phases, write cause before reaction, and keep
the number of interacting bodies down. If two people must hand something over, that is the one event
of that stage.

**The reference did nothing.** It was never given a job. `@Image 1` on its own is decoration. Name
what it defines and what to ignore.

**The reference did too much.** No "do not use" clause, so its background, grade or framing came
along. Or two references were given the same job and fought.

**Everything came out mushy and generic.** Too much prompt. This is the model's documented failure
mode and it is counterintuitive: cut the prompt in half, keep one camera move and one lighting
keyword, and it usually sharpens.

**The thirty second version is the five second version, slower.** Only one event was written.
Duration is a container, not a story. Stage it.

**A caption appeared that nobody asked for.** Random subtitles are a known artefact of the 2.0 era
that 2.5 claims to have improved. If it happens, the fix is to use the `【 】` channel deliberately
for what you do want and add "no on screen text" to the closing constraints.

**A face reference was refused.** Reference images containing real human faces are reported as
blocked in the standard workflows on some routes, which kills spokesperson and UGC work. Not tested
on our route, and worth a cheap probe before promising a client that pipeline.

**Queues and retries.** Community reports put queues at up to forty minutes at peak and note that
real production cost is retries, not the sticker rate. Budget two or three takes per finished shot,
which is exactly why drafting at 480p matters.

### Iteration discipline

**Change one thing.** When a generation misses, modify only the part that failed, usually the
physical start or end state of one stage. Changing the camera, the character and the location at the
same time gives you a new problem, not a fix, and it costs 6.5 credits a second to learn nothing.

Keep the prompt that nearly worked. Reuse a character description that is working rather than
rewriting it, across takes and across days.

---

## 10. Recipes

Templates that work as written. Delete any section you do not need.

### A. Single take, under ten seconds

```
[Subject] [does one continuous action], in [scene] with [one atmospheric detail moving].
[Lighting keyword], [the anti plastic sentence].
[One camera move].
(music) <two or three diegetic sounds>
```

Worked:

```
A luthier turns a violin scroll under a desk lamp while sawdust drifts through the beam and the
window rattles faintly. Tungsten practicals, thin haze between the bench and the back wall, matte
skin with no shine on forehead or cheekbones, lifted blacks and rolled off highlights.
Slow push in from wide to a close on his hands.
(low room tone) <chisel scraping, a distant street>
```

### B. Locked character across shots

```
[Characters]
<Name> corresponds to @Image 1. [Position in frame, depth, facing].
       Same face, [clothing] and silhouette throughout. Do not use its background.

[Scene]
<Place> corresponds to @Image 2. Do not use its framing or its people.

[Action]
[One sentence per moving element.]

[Camera] [One move, tied to an event if it needs a trigger.]
(music) <sound>
```

Run it in `omni_reference`. Same price as text to video, billed on the duration you ask for.

### C. Thirty seconds that holds together

```
[Generation Goal] A [type of piece] in which [the central event].

0s-6s:   [Initial state]. [One event]. Ends with [end state].
6s-16s:  From [that end state], [one new event]. Ends with [end state].
16s-30s: [Closing event]. Ends on [final state].

[Maintain Consistency] Same [face, wardrobe, location, lens, grade] throughout.
The camera [does one thing] and never cuts.
No cuts, no slow motion, no repeated action, no duplicated props.
(music) <sound>
```

### D. Fixing one thing inside a clip you have

```
Edit @Video 1 from [start] to [end] seconds: [the one change].
Keep [identity, clothing, position, motion, framing, grade] from @Video 1 unchanged.
```

Exactly one video reference. The cost is the source clip's own length, so trimming the clip before
you upload it is a real saving.

### E. Continuing a clip forward

```
Extend @Video 1 forward. Hold [what must not change: the framing, the light, the position].
[The one new event.] Ends with [final state].
```

Direction is mandatory. Backward extensions need an explicit end state or they drift past the join.

### F. Chaining by hand, when the join can breathe

Take the final frame of the last clip, feed it as the start image, and say so:

```
Use @Image 1 as the exact first frame and continue forward from that moment.
[The next action.]
```

### Command lines

```bash
# quote first, always, and spell out the mode
higgsfield generate cost seedance_2_5 --prompt "$(cat prompt.txt)" \
  --duration 10 --resolution 480p --mode t2v --json

# draft at 480p through the wrapper (resolution travels in --extra)
python skills/image-gen/scripts/generate.py "$(cat prompt.txt)" --video -m seedance2.5 \
  --duration 10 --extra '{"resolution":"480p"}'

# commit at 720p, the default
python skills/image-gen/scripts/generate.py "$(cat prompt.txt)" --video -m seedance2.5 --duration 10

# character locked shot, raw CLI because the wrapper drops media from quotes
higgsfield generate cost seedance_2_5 --prompt "$(cat prompt.txt)" --duration 8 \
  --resolution 720p --mode omni_reference --image-references hero.png --json

# edit inside an existing clip
higgsfield generate cost seedance_2_5 --prompt "$(cat prompt.txt)" \
  --resolution 720p --mode video_edit --video-references source.mp4 --json

# extend it forward
higgsfield generate cost seedance_2_5 --prompt "$(cat prompt.txt)" --duration 6 \
  --resolution 720p --mode video_extension --extension-mode forward \
  --video-references source.mp4 --json

# read the live schema when anything surprises you
higgsfield model get seedance_2_5 --json
```

Local file paths are uploaded automatically by both `generate cost` and `generate create`, so a
quote for a media job needs no separate upload step.

### A pre flight checklist

1. Is every reference given a job and a "do not use" clause?
2. Is there exactly one camera instruction?
3. Does every element that should move have its own verb?
4. Past ten seconds, is the time staged, with one event per stage?
5. Is the mode spelled out on the command line?
6. Have you quoted it?

---

## 11. Cheatsheet

### Formula

```
Subject + Action + Scene + Style + Camera + Audio
```

Only the first two are required. Two or three sentences. Dense prompts get worse results.

### Brackets

```
( )  music and ambient        <  >  sound effects and Foley
{ }  spoken dialogue          【 】  on screen subtitle
```

Dialogue and subtitle are separate channels. Declare the language before the line, put only the
spoken words inside the braces.

### References

```
@Image 1   @Video 1   @Audio 1        capital letter, space before the number
```

Name what each one defines **and what to ignore**. One job per reference. Eight subjects maximum,
five once video or audio references are in play.

### Price, per second, measured 2026-08-19

```
480p  2.5      720p  6.5      1080p  9.0
```

Same rate in every mode. `video_edit` ignores your duration and bills the source clip's length,
four second floor. Everything else bills the duration you request.

| | 4 s | 5 s | 10 s | 30 s |
|---|---|---|---|---|
| 480p | 10 | 12.5 | 25 | 75 |
| 720p | 26 | 32.5 | 65 | 195 |
| 1080p | 36 | 45 | 90 | 270 |

### Limits

```
duration    4 to 30 s, whole seconds
resolution  480p, 720p, 1080p        (no 4K on this route)
aspect      auto, 21:9, 16:9, 4:3, 1:1, 3:4, 9:16
references  50 total, 30 images, 10 video, 10 audio
prompt      no ceiling found up to 40000 characters
```

### Modes

```
t2v              no media at all
omni_reference   at least one reference, the only mode that takes keyframes
video_edit       exactly one video, aspect and duration locked to the source
video_extension  at least one video, plus extension_mode forward or backward
```

**Always pass `--mode` explicitly.** Omitted, the mode rules never evaluate and media slips through
into a mode you did not choose.

### The five rules that fix most bad output

1. Every element that must move gets its own verb.
2. One camera instruction, one lighting keyword.
3. Past ten seconds, stage the time: one event and one end state per stage.
4. Cause before reaction: contact, then movement, then sound, then response.
5. Re identify anyone who leaves frame and comes back.

### Cost discipline

Find the framing on `kling-turbo` or `seedance1.5`. Draft the motion here at 480p. Commit once at
720p or 1080p. Quotes are free. Never run an unquoted job.

### Commands

```bash
higgsfield generate cost seedance_2_5 --prompt "..." --duration 10 \
  --resolution 480p --mode t2v --json

higgsfield model get seedance_2_5 --json
```

---

## 12. Sources, and how each claim was verified

Three tiers of confidence. They are kept separate on purpose, because two of the six corrections in
section 1 were changes nobody announced anywhere.

### Tier 1: measured live against the route

Everything about specs, prices, parameters, validator messages and rejections was probed on
**2026-08-19** using Higgsfield CLI 1.1.23, and re-probed on **2026-08-23**, through
`higgsfield generate cost`, `higgsfield generate create` and `higgsfield model get seedance_2_5`.

Both rounds ran to a flat credit balance, unchanged to the cent before and after, which is what
makes the method safe: quotes and rejected submissions cost nothing. Prices are Higgsfield's and
can move without notice, so treat every number here as a reading with a date on it, not a contract.

Two techniques worth reusing:

- **Know what a quote does not cover.** Re-checked 2026-08-23: the cost endpoint reports one error
  at a time, local enum checks ahead of server side range checks, and it does not validate the
  prompt field at all (an empty prompt quotes 32.5 at five seconds). So a clean quote bounds the
  quote path and nothing more. To probe a submit time limit without paying, stack the thing you are
  testing onto a field the validator already refuses, and expect one error back rather than the set.
- **To prove a parameter is really parsed** rather than silently swallowed, send an out of enum
  value and check that it is rejected by name.

### Tier 2: first party vendor material

- [Dreamina Seedance 2.5 product page](https://dreamina.capcut.com/seedance/seedance-2-5), ByteDance's
  own: 30 seconds standard, 180 seconds in a beta long video mode, 50 multimodal inputs, the eleven
  dialogue languages, "removes random subtitles and unwanted BGM".
- [BytePlus ModelArk, Seedance 2.5 prompt guide](https://docs.byteplus.com/en/docs/ModelArk/2607689)
  and [Create a video generation task](https://docs.byteplus.com/en/docs/ModelArk/1520757). Both are
  the authoritative documents and both are JavaScript gated: fetching them returns the navigation
  shell and no content. The formula and syntax below were therefore taken from secondary write ups
  that quote them, which is a real weakening of provenance and is why tier 3 is listed at all.

### Tier 3: third party guides, weighted by whether they tested anything

Used where several independent sources agree, and marked as such in the text.

- [Segmind, the official six part formula explained](https://blog.segmind.com/the-official-seedance-2-5-prompt-guide-bytedances-six-part-formula-explained-with-examples/):
  the formula, the bracket table, the reference exclusion syntax, the long form stage structure, the
  parameter locks per task.
- [fal.ai prompting guide](https://fal.ai/learn/devs/seedance-2-5-prompting-guide): the most
  practically tested of the set. Cause before reaction, occlusion re identification, camera movement
  triggers, one job per reference, contact phases, reference clip duration bounds, iteration
  discipline.
- [SunoMV, tested August 2026](https://suno.bi/en/blog/seedance-2-5-prompt-guide): second level
  timestamps are first class on 2.5 where they were unstable on 2.0, the three layer colour
  structure, the motion rules.
- [AI Studios prompt writing guide](https://help.aistudios.com/en/articles/16405002-seedance-2-5-prompt-writing-guide):
  speaker labelling, camera vocabulary, chronological time ranges.
- [awesome-seedance-2.5-api-prompts](https://github.com/Anil-matcha/awesome-seedance-2.5-api-prompts):
  a reseller's API surface. Useful for the pixel dimension table and the observation that 1080p and
  4K tiers on some routes are upscales of a 720p render. Its parameter names are that reseller's,
  not ByteDance's.
- [Reddit review round up](https://www.virse.ai/blog/seedance-2-5-reddit-review): character drift,
  multi subject physics, face reference moderation, queue times, retry economics.
- [Morphic how to guide](https://morphic.com/resources/how-to/seedance-2-5-guide): carried from the
  previous version of this guide, not re read for this revision.

### Pricing elsewhere, for context only

Useful if the Higgsfield credit price ever stops making sense. Figures from vendor listings, not
verified here: Atlas Cloud publishes a flat 0.134 dollars a second across all three variants,
Replicate publishes four per second tiers that vary with resolution and video input, and Segmind
and fal bill the same token formula at 10.97 and 21.40 dollars per million output tokens
respectively, which is where the frequently quoted claim that fal is 95 percent dearer comes from.

### What is not verified anywhere

- Whether the 1080p on this route is a native render or an upscale.
- Whether real human faces are refused as references here. Reported on other routes, untested here.
- The 180 second beta, which is not exposed on this route at all.
- The eleven language dialogue claim, which is first party but untested by us.
- Every craft claim in section 8 carried from Seedance 2.0. The mechanisms are model agnostic and in
  three cases ByteDance's own documented failure modes agree, but at 6.5 credits a second they were
  reasoned rather than measured. Treat them as strong priors.
