# Seedance 2.5 (ByteDance)

Covers: `seedance2.5` (`seedance_2_5`)

Distinct model from `seedance` (Seedance 2.0) and `seedance1.5`. Those stay in the catalog and
keep their own guide (`seedance.md`). Seedance 2.5 is the only model on this route that can **edit
an existing clip** or **extend one in either direction**, and it is the most expensive video model
we can reach. Read this before touching it.

---

## Hard specs

| | Vendor claim | On our Higgsfield route **[live 2026-08-07]** |
|---|---|---|
| Duration | up to 30 s, 180 s via chained extension (beta) | **4 to 30 s**, integer seconds |
| Resolution | up to 4K | **480p or 720p only.** No 1080p, no 4K |
| Aspect | 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, auto | same, default 16:9 |
| Audio | native, generated in the same pass | `generate_audio`, default on |
| References | 50 files (30 image, 10 video, 10 audio) | `image_references`, `video_references`, `audio_references`, **50 total** |
| Modes | t2v, omni reference, video edit, video extension | all four reachable |
| Prompt length | not published | **4000 characters, hard** (characters, not bytes) |
| Cost | n/a | **6.5 credits/s at 720p, 3 at 480p** — but **4 and 2** with a video reference |

**The 4000-character prompt ceiling is real, undocumented by the vendor, and counted in
CHARACTERS not bytes, tokens or words [live 2026-08-10].** Boundary walked on both sides: 4000
characters is accepted, 4001 is rejected with `prompt: String should have at most 4000 characters`.
The rejection is free and nothing is charged, but **`generate cost` does not enforce it** — the
estimator quotes a 4339-character prompt without a murmur, so the quote is not a pre-flight check.
Count locally before you submit:

```bash
wc -m < prompt.txt          # characters
```

**Use `wc -m`, never `wc -c`.** `wc -c` counts bytes, and the ceiling is Unicode characters, so
bytes overcount every non ASCII prompt — which means Greek, curly quotes and em dashes all inflate
the number. Measured: a 4000-character Greek prompt is 7525 bytes. `wc -c` calls that 88 percent
over the limit; the API accepts it. Trust `wc -m` and you keep the prompt you wrote.

For scale: a disciplined structured `video_edit` prompt of ~670 words came to 3778 characters, so
the ceiling bites at roughly **700 words** of English. Budget in characters, not words — an English
word plus its space averages about 5.6 characters here, and long prompts are exactly where you stop
noticing.

**Cost, measured not estimated [live 2026-08-09]:** 720p is 26 credits for the 4 s minimum, 32.5 for
5 s, 65 for 10 s, 97.5 for 15 s, 195 for the 30 s maximum. 480p is 12 for 4 s, 15 for 5 s, 30 for
10 s. Exactly linear, no discount for length.

**Those are `t2v` prices, and the discount that undercuts them is not a mode — it is the video
reference [live 2026-08-10].** Any job carrying a `video_references` file drops from 6.5 to **4
credits/s at 720p**, and from 3 to **2 credits/s at 480p** — 38 percent off at 720p, 33 at 480p.
The file is what moves the price, not the name of the mode:

| 5 s job | 720p | 480p |
|---|---|---|
| `t2v` | 32.5 | 15 |
| `omni_reference`, image references only | 32.5 | 15 |
| `omni_reference` **carrying a video reference** | **20** | **10** |
| `video_edit` (requires exactly one) | **20** | **10** |
| `video_extension` (requires at least one) | **20** | **10** |

Linear at the lower rate as well: `video_edit` at 720p is 16 for 4 s, 40 for 10 s, 60 for 15 s and
120 for the 30 s maximum. The 30 s take that costs 195 as `t2v` costs **120** as an edit or an
extension. Reach for a video reference and the model stops being the dearest on the route.

**`generate_audio` changes nothing.** On or off, the quote is identical in every mode and at both
resolutions. Mute it because you do not want a soundtrack, never to save credits.

Quote the mode you will actually run, and quote it through the CLI rather than the wrapper.
`generate.py --cost` never forwards media references, so `--extra '{"mode":"video_edit"}'` dies on
the model's own rule (`mode 'video_edit' requires exactly one video reference`); and it drops
`--resolution` for video kinds, so every wrapper quote silently comes back at the 720p default
unless you push resolution through `--extra` too:

```bash
higgsfield generate cost seedance_2_5 --prompt "..." --duration 5 --resolution 480p \
  --mode video_edit --video-references "<upload-id>" --json
```

At 6.5 credits a second this is **the most expensive video model in the catalog**, ahead of
`flux-video` (5.5), `cinematic3` and `cinematic3.5` (5.0), `seedance` (4.5) and `h3` (4.0).
A single 30 s take is 195 credits. That ranking is a `t2v` ranking: at the 4 credits/s video
reference rate the same model lands below `flux-video` and level with `h3`, so an edit or an
extension is not the luxury the headline rate makes it look.

**480p is less than half price and is a real draft tier here**, unlike H3 which has none. Block the
shot out at 480p, then commit once at 720p.

Verified rejections **[live 2026-08-09]**: `--duration 3` gives "Input should be greater than or
equal to 4", `--duration 31` gives "less than or equal to 30".

---

## The six-slot formula

ByteDance publishes an actual grammar for this model, and slot **order** is what makes intent
legible to it. Only the first two slots are required.

```
Subject + Action or Event + Scene and Environment + Visual Style + Camera Movement or Cut + Audio
```

The single most misunderstood rule: **filling every slot with dense detail makes output worse.**
Competing style and camera instructions fight each other. Two or three clear sentences beat a long
paragraph. This is the opposite of the `seedance.md` Mode B advice for Seedance 2.0, which rewards
long timeline blocks. Do not carry that habit over.

Describe **one continuous take**, not a frozen frame. "A ceramic artist lifts the cup from the wheel
and sets it on a shelf" is a take. "A ceramic artist holding a cup" is a photograph.

---

## The four bracket channels

Audio and on-screen text are routed by bracket type, not by prose. These are literal syntax.

| Bracket | Channel | Example |
|---|---|---|
| `( )` | music and ambient beds | `(low cello drone, slow)` |
| `< >` | sound effects and Foley | `<door latch, rain on glass>` |
| `{ }` | spoken dialogue, heard | `{We should go.}` |
| `【 】` | on-screen subtitle, seen | `【We should go.】` |

Dialogue and subtitle are **separate channels**. If you want a line both heard and burned in, write
it in both `{ }` and `【 】`. Writing it once in `{ }` gives you audio with no caption.

**This reverses the hardest rule in `seedance.md`.** Seedance 2.0 synthesizes audio from loose prose,
so that guide bans music, lyrics and score from the prompt outright and allows diegetic sound only.
On 2.5 music has its own channel and belongs in the prompt, inside `( )`. The 2.0 ban is the single
most damaging sentence to carry over, because obeying it here throws away a control the model has.

Foley still goes in `< >` and stays diegetic. The 2.0 instinct that survives is the *description*
discipline: name what the scene itself makes, keep it to two or three sources, do not write a
paragraph of sound design.

---

## Reference syntax

Files are addressed positionally by type, in upload order: `@Image 1`, `@Image 2`, `@Video 1`,
`@Audio 1`. The reference is inert unless the prompt says what it governs.

**The rule that makes references work: name what each reference defines, and what to ignore.**

```
@Image 1 defines the detective's coat and face; do not use its background.
@Image 2 defines the workbench and the window light.
```

Without the "do not use" half you inherit the reference's background, grade and framing by accident.

For prompts with several subjects, group them with role headers rather than scattering the tags:

```
[Characters]
<Detective> corresponds to @Image 1
<Barman>    corresponds to @Image 2

[Scenes]
<Plaza>     corresponds to @Image 3
```

**Budget discipline.** The ceiling is 50 files but the practical stability limit is far lower: keep
distinct subjects to **8 or fewer** with image references, and **5 or fewer** once video or audio
references are in play. Past that, identities start bleeding into each other.

---

## Keyframes: start and end image **[live 2026-08-09]**

2.5 accepts `start_image` and `end_image`. The six slot formula never mentions them, and they are
what make the whole image to video half of `seedance.md` applicable here. Two hard rules, both
measured against the live validator:

**They are legal in `omni_reference` only.** In the default `t2v` mode the API refuses outright:

```
generate cost seedance_2_5 --prompt "test" --start-image f.png
    Error: start_image and end_image are only allowed for mode 'omni_reference'

generate cost seedance_2_5 --prompt "test" --start-image f.png --mode omni_reference
    32.5 credits
```

**The wrapper does not set the mode for you.** `generate.py` passes the flag straight through, so
`-m seedance2.5 --start-image f.png` fails unless you also send
`--extra '{"mode":"omni_reference"}'`. A start frame and `image_references` can be sent together in
that mode; both count against the 30 image ceiling.

An end frame alone, with no start frame beside it, is accepted. So is an end frame on 2.0, which
corrects the note in `seedance.md`.

**`--cost` cannot check any of this, so do not use it as the proof.** `estimate_cost` never forwards
`--start-image`, `--end-image` or `--video-references` to the CLI. A keyframed job therefore quotes
as if it were a plain text to video roll, the mode rejection above stays invisible, and the call
fails only once it is paid for. Both blocks above are raw CLI, which is the only shape that
validates the keyframe. Quote a keyframed roll by hand before committing to it:

```bash
higgsfield generate cost seedance_2_5 --prompt "..." --duration 5 \
  --start-image first.png --mode omni_reference --json
```

---

## The four modes

Mode is set with `--extra '{"mode":"..."}'`. The API enforces each mode's inputs with its own rules,
all verified live.

### `t2v` (default)

Pure text to video. **Rejects all reference media** ("mode 't2v' does not accept reference media").
If you attach anything, you must change mode.

**But only when you say `t2v` out loud [live 2026-08-10].** The rule is written against the `mode`
parameter, and the CLI omits the parameter entirely when you do not pass `--mode`. Omit it and the
rule never evaluates: `--video-references <id>` with no `--mode` is accepted at quote and clears
validation at submit, and it is quoted at the 4 credits/s video reference rate rather than 6.5. The
mode rules also run *before* the field rules, so a request that is wrong in both ways reports only
the mode error — which is why the default path shows nothing at all. Pass `--mode` explicitly on
every call and the trap cannot fire.

### `omni_reference`

The headline capability. Requires **at least one** reference item. This is the mode for locking a
character, a product or a location across shots. Use the `@Image n` syntax above.

```
A florist arranges stems in a studio. @Image 1 defines the florist's appearance;
do not use the background. @Image 2 defines the workbench and window.
Slow push in from wide to close on the bouquet. (soft room tone) <stems snipping>
```

### `video_edit`

In-clip editing. Requires **exactly one** video reference. Changes a targeted region or time range
without regenerating the whole clip. Aspect ratio and duration are **locked to the source** here
(duration within about 0.3 s), so do not pass a conflicting duration.

Structure it in three parts: name the master, define the scope, list what to preserve.

```
Edit @Video 1 from 4 to 7 seconds: change the cool blue light on the right wall to
warm orange. Keep character identity, clothing, position and motion from @Video 1.
```

The "keep" list is not optional. Without it the model treats unmentioned elements as free to redraw.

### `video_extension`

Bolts new footage onto a boundary frame. Requires at least one video reference **and** an explicit
`extension_mode` of `forward` or `backward`. Passing `extension_mode` in any other mode is rejected.

- **forward**: the new segment's first frame continues from the source's last frame.
- **backward**: the new segment's last frame must *arrive at* the source's first frame. Always give
  an explicit end state, or the image keeps drifting after it has already reached the join.

```
Extend @Video 1 forward. Hold the locked medium shot and the paper airplane's position.
The airplane glides right and exits frame while the curtain sways.
```

---

## What carries from Seedance 2.0

`seedance.md` is 290 lines and most of it is craft, not API behavior. Craft transfers. What does not
transfer is every sentence about 2.0's parameters, its reference syntax, and its ban on music.

Read the table, then the sections under it: one block carries verbatim, four need reshaping rather
than copying, and the last is a worked example in 2.5 shape.

| From `seedance.md` | On 2.5 | Basis |
|---|---|---|
| Lighting first, source direction and quality | carries | craft |
| Volumetric depth in every shot, light the air | carries | craft |
| Capture realism, the anti plastic four | carries whole | craft |
| Camera and subject in separate sentences | carries, and the slot order enforces it | craft |
| One camera instruction, never a stack | carries, and harder | craft, vendor doc agrees |
| No fast or slow as bare adjectives | carries | craft |
| Physical verbs over abstract ones | carries | craft |
| Lens and light behavior, never brand names | carries | craft |
| Handheld is a camera choice, not a garnish | carries | craft |
| No decorative adjective stacks | carries, this is 2.5's headline failure | craft, vendor doc agrees |
| Five registers M1 to M5 | carries as prose, there is no `genre` param | measured |
| Night, hard practical register | carries | craft |
| The static backplate trap and its three fixes | carries, see below | craft |
| The breathing dial | carries | craft |
| Locked title safety | carries | craft |
| The rain streak trap | carries | craft |
| Do not redescribe what the start frame shows | carries | craft |
| Reference and start frame are different jobs | carries, and 2.5 makes them different params | measured |
| Name characters to the prompt writer, never to the model | carries | craft |
| Motion and look are separate systems, route every attribute | carries, it is 2.5's own reference rule in a richer form | craft, vendor doc agrees |
| The empty background trap | carries | craft |
| Crowd as individuals, and the motion donor clip | carries | craft |
| Reference video hygiene, 3 to 8 s, one idea wide | carries | craft |
| Frame map, subject lock, cross frame rules | carries compressed, see below | adapted |
| Restate motion per beat, never once | carries reshaped, see below | adapted |
| Negatives, 4 to 5 focused items | tighten to 3, prefer positive locks | adapted, unmeasured |
| Count the beats | inverted, see below | adapted |
| Render cheap, upscale after | 480p draft then 720p, nothing above to upscale into | measured |
| Mode B, timeline blocks of 150 to 800 words | conflicts, do not carry | vendor doc |
| Sound bed, diegetic only, never music | reversed, music goes in `( )` | vendor doc |
| `@image1`, lowercase and no space | wrong syntax here, use `@Image 1` | vendor doc |
| 9 images, 3 video, 3 audio, 12 total | wrong ceiling, **30 images and 50 total** here | measured |
| `mode` fast or std, iterate on fast | rejected, `mode` means capability on 2.5 | measured |
| `genre` | rejected, unknown param | measured |
| `bitrate_mode` | rejected, unknown param | measured |
| 1080p and 4k | not exposed on this route | measured |

Every row marked measured was probed live on 2026-08-09. The three parameter rejections:

```
--mode fast          Invalid values: mode=fast (allowed: t2v,omni_reference,video_edit,video_extension)
--genre noir         Unknown params: genre
--bitrate_mode high  Unknown params: bitrate_mode
```

`mode` is the dangerous one. It exists on both models and means something completely different:
a speed tier on 2.0, a capability on 2.5. Carrying the 2.0 habit of iterating on `fast` does not
degrade the shot here, it fails the call.

### Capture realism, the anti plastic four

Unchanged from `seedance.md`, and it is the highest value block to bring over. State all four, in
one sentence, in the Visual Style slot:

1. **Depth through suspended atmosphere.** A thin veil of haze *between* the depth planes, not just
   in front of the subject, so foreground and background separate optically.
2. **Moisture without shine**, only if the scene is genuinely wet. Matte dampness, not glossy beads.
3. **Per zone specular kill on skin.** Name the zones and take the shine to zero: forehead, nose
   bridge, cheekbones, temples, chin. Pair it with a flattering ceiling so matte never turns ugly.
4. **Contrast curve stated three ways**: lifted blacks, rolled off highlights, low contrast grade.

This survives the compression rule because it is four positive locks, not decoration. Positive locks
steer the model harder than piles of prohibitions, which is exactly why the negative budget shrinks
while this block does not.

### The static backplate trap, reshaped

The mechanism is about how the model allocates attention, so it is model agnostic and it applies
here in full: the engine front loads onto the most legible element and treats everything else,
including the subject's own aliveness and the whole background, as a plate to hold still.

The 2.0 fix was to restate motion inside every beat block. 2.5 has no beat blocks, so the fix
changes shape rather than disappearing:

**Give every element that must move its own verb, inside the action sentence.** A moving thing named
as a noun in the Scene slot will freeze. "A woman at the window, rain outside, a curtain" is three
frozen nouns. "A woman breathes slowly at the window while rain runs down the glass and the curtain
stirs behind her" is one sentence carrying three independent motions, and it costs fewer words than
the 2.0 form did.

The other two fixes carry unchanged and both now have a mechanical hook, because 2.5 takes keyframes:

- **Open on a different frame from the locked last frame.** Identical start and end invite the model
  to interpolate nothing and hold the still. This also applies to `video_extension` with
  `extension_mode: backward`, where the segment must arrive at the source's first frame: the join is
  a locked last frame by another name, so give the opening a different state to travel from.
- **Three live layers at different speeds** give parallax depth. Foreground drift, the subject,
  background movement. Keep it slight.

**The breathing dial** is still the strongest single lever against a photograph that moves. If a
shot must have no visible respiration, reload the life into micro movements and the living
background instead: one hand regrip, a slow blink, hair stirred by an updraft, firelight crawling.

### Multi subject, compressed

The 2.0 discipline is place, then identify, then move, and it was the single biggest lever against
two characters melting into each other. It carries. What does not carry is spending a paragraph per
character on it.

2.5 already groups subjects under role headers. Put the geography into that header line, so the
frame map, the subject lock and the cross frame rules collapse into one line each:

```
[Characters]
<Detective> corresponds to @Image 1. Left third, midground, facing screen right.
            Same face, coat and silhouette throughout. Do not use its background.
<Barman>    corresponds to @Image 2. Right third, behind the counter, turned inward.
            Same face and apron throughout. Do not use its background.

They hold their screen sides and never cross the center.
```

That is three 2.0 blocks in five lines, and it keeps the two rules that did the work: pin position,
depth and gaze before identity, and forbid the swap explicitly.

### Beats, inverted

On 2.0, two or more discrete beats meant Mode B and a timeline. Here the same count means one of two
things, and never a timeline:

1. **Compress to one continuous take.** Most two beat shots are really one action with a
   consequence, and the model handles those natively.
2. **Split and chain.** Genuinely sequential beats become separate takes joined with
   `video_extension` forward. This is the capability 2.5 has and 2.0 does not, and it is the correct
   answer to the problem Mode B was invented to solve.

The old test still holds in reverse: if a prompt feels bloated at 300 words, you are writing a
timeline the model will not read.

### Negatives

Neither model has a negative prompt parameter, so negatives are prose either way. The six slot
formula has no negative slot at all, and dense prose is the model's documented failure mode, so the
budget is tighter than 2.0's four to five: **keep it to three short items at the very end, and reach
for a positive lock first.** "Gaze held to screen right" beats "no looking at camera".

Untested claim, flagged as such: the three highest leverage universals from 2.0, no jitter, no bent
limbs, no temporal flicker, are almost certainly still the right three, but I have not measured them
on 2.5 and every generation here costs 6.5 credits a second.

### A worked example, carried craft in 2.5 shape

```
A detective sets a wet coat on the back of a chair and lowers himself into it, breathing out
once. A narrow bar at night, rain running down the window behind him, the ceiling fan turning
slowly. Thin haze between the window and the counter so the planes separate; matte skin with no
shine on forehead, nose bridge or cheekbones; lifted blacks, rolled off highlights, low contrast
grade. Slow push in from wide to medium. (low cello drone, slow) <rain on glass, a chair creak>

@Image 1 defines the detective's face and coat; do not use its background.
@Image 2 defines the bar and its window light; do not use its framing.
```

Six slots, 85 words in the body and 112 with the reference routing, one camera instruction, three
moving elements each with their own verb, the anti plastic four in one sentence, music in its own
channel, both references routed and de authorized. Nothing from Mode B.

---

## What NOT to do

- **Do not fill all six slots.** Dense style plus dense camera instructions degrade the result. This
  is the model's own documented failure mode.
- **Do not draft at 720p.** 480p is half price and exists precisely for this.
- **Do not submit over 4000 characters.** Hard rejection at submit. Free, but the cost estimator
  does not enforce it, so a clean quote is no evidence the prompt will be accepted.
- **Do not count the prompt with `wc -c`.** That is bytes. Use `wc -m`. Greek, curly quotes and em
  dashes all read as over-long in bytes and get cut for nothing.
- **Do not omit `--mode` when you attach a reference.** With `--mode t2v` spelled out, a reference
  is a hard rejection. Leave `--mode` off and the rule never fires: the default silently takes the
  reference, and bills it at the cheaper video reference rate. Always pass the mode you mean.
- **Do not budget an edit or an extension at the `t2v` rate.** Anything carrying a video reference
  runs at 4 credits/s at 720p, not 6.5. Quoting the wrong mode overstates a 30 s job by 75 credits.
- **Do not omit the "do not use" clause** on a reference. You will inherit its background.
- **Do not omit the preserve list** in `video_edit`.
- **Do not expect 1080p or 4K.** ByteDance ships them; this route does not expose them.
- **Do not carry over Seedance 2.0 Mode B habits.** Long multi beat timeline blocks are the wrong
  shape for 2.5.
- **Do not exceed 8 distinct subjects** even though the file ceiling is 50.
- **Do not send `mode: fast` or `mode: std`.** Those are 2.0's speed tiers. Here `mode` selects the
  capability and the call is rejected.
- **Do not send `genre` or `bitrate_mode`.** Both are 2.0 only and come back as unknown params.
- **Do not keep music out of the prompt.** That is the 2.0 rule and it is inverted here.
- **Do not send a start or end image outside `omni_reference`.** Hard rejection, and the wrapper
  will not set the mode for you.

---

## Cost discipline

At 6.5 credits a second, a three take 10 s shot at 720p is 195 credits. Before generating:

1. Find the framing on `kling-turbo` (1.5/s) or `seedance1.5` (1.2/s).
2. Draft the motion here at **480p** (3/s).
3. Commit one 720p roll.

**Resolution travels in `--extra`, not in `--resolution`.** The `--resolution` flag is the image
side of the wrapper and only accepts `1k`, `2k`, `4k`; passing `480p` to it is rejected by argparse
before anything reaches the API. Verified live: the two commands below quote 30 and 65 credits.

Quote every time, the flag is cheap and the roll is not:

```bash
# draft at 480p
python skills/image-gen/scripts/generate.py "..." --video -m seedance2.5 --duration 10 \
  --extra '{"resolution":"480p"}' --cost

# commit at 720p (the default, no extra needed)
python skills/image-gen/scripts/generate.py "..." --video -m seedance2.5 --duration 10 --cost
```

---

## Sources

- [Official Seedance 2.5 prompt guide, six part formula](https://blog.segmind.com/the-official-seedance-2-5-prompt-guide-bytedances-six-part-formula-explained-with-examples/)
- [Modes, references and examples](https://morphic.com/resources/how-to/seedance-2-5-guide)
- [Seedance 2.5 model overview](https://morphic.com/resources/models/seedance-2-5)
- Everything marked **[live]** was measured against `higgsfield generate cost` and
  `higgsfield model get seedance_2_5`. The specs and costs on CLI 1.1.20, 2026-08-07. The keyframe
  rules, the three parameter rejections and the carry table's measured rows on CLI 1.1.23,
  2026-08-09, cross checked against `model get seedance_2_0` on the same run.
- The carry table's **craft** rows are reasoned, not measured: the mechanism is model agnostic and
  in three cases ByteDance's own documented failure mode agrees with it. They cost 6.5 credits a
  second to test properly, so treat them as strong priors rather than proven on this model.
