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
| Duration | up to 30 s, 180 s via chained extension (beta) | **5 to 30 s**, integer seconds |
| Resolution | up to 4K | **480p or 720p only.** No 1080p, no 4K |
| Aspect | 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, auto | same, default 16:9 |
| Audio | native, generated in the same pass | `generate_audio`, default on |
| References | 50 files (30 image, 10 video, 10 audio) | `image_references`, `video_references`, `audio_references`, **50 total** |
| Modes | t2v, omni reference, video edit, video extension | all four reachable |
| Cost | n/a | **6.5 credits/s at 720p, 3 credits/s at 480p** |

**Cost, measured not estimated [live]:** 720p is 32.5 credits for 5 s, 65 for 10 s, 97.5 for 15 s,
195 for the 30 s maximum. 480p is 15 for 5 s, 30 for 10 s. Exactly linear, no discount for length.

At 6.5 credits a second this is **the most expensive video model in the catalog**, ahead of
`flux-video` (5.5), `cinematic3` and `cinematic3.5` (5.0), `seedance` (4.5) and `h3` (4.0).
A single 30 s take is 195 credits. **480p is less than half price and is a real draft tier here**,
unlike H3 which has none. Block the shot out at 480p, then commit once at 720p.

Verified rejections **[live]**: `--duration 4` gives "Input should be greater than or equal to 5",
`--duration 31` gives "less than or equal to 30".

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

## The four modes

Mode is set with `--extra '{"mode":"..."}'`. The API enforces each mode's inputs with its own rules,
all verified live.

### `t2v` (default)

Pure text to video. **Rejects all reference media** ("mode 't2v' does not accept reference media").
If you attach anything, you must change mode.

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

## What NOT to do

- **Do not fill all six slots.** Dense style plus dense camera instructions degrade the result. This
  is the model's own documented failure mode.
- **Do not draft at 720p.** 480p is half price and exists precisely for this.
- **Do not attach references in `t2v`.** Hard rejection, not a silent ignore.
- **Do not omit the "do not use" clause** on a reference. You will inherit its background.
- **Do not omit the preserve list** in `video_edit`.
- **Do not expect 1080p or 4K.** ByteDance ships them; this route does not expose them.
- **Do not carry over Seedance 2.0 Mode B habits.** Long multi beat timeline blocks are the wrong
  shape for 2.5.
- **Do not exceed 8 distinct subjects** even though the file ceiling is 50.

---

## Cost discipline

At 6.5 credits a second, a three take 10 s shot at 720p is 195 credits. Before generating:

1. Find the framing on `kling-turbo` (1.5/s) or `seedance1.5` (1.2/s).
2. Draft the motion here at **480p** (3/s).
3. Commit one 720p roll.

Quote every time, the flag is cheap and the roll is not:

```bash
python skills/image-gen/scripts/generate.py "..." --video -m seedance2.5 --duration 10 --cost
```

---

## Sources

- [Official Seedance 2.5 prompt guide, six part formula](https://blog.segmind.com/the-official-seedance-2-5-prompt-guide-bytedances-six-part-formula-explained-with-examples/)
- [Modes, references and examples](https://morphic.com/resources/how-to/seedance-2-5-guide)
- [Seedance 2.5 model overview](https://morphic.com/resources/models/seedance-2-5)
- Everything marked **[live]** was measured against `higgsfield generate cost` and
  `higgsfield model get seedance_2_5` on CLI 1.1.20, 2026-08-07.
