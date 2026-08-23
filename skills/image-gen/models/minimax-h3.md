# MiniMax H3 / Hailuo 3.0

Covers: `h3`, `hailuo3` → `minimax_h3` (Higgsfield) / `MiniMax-H3` (MiniMax direct)

Researched and written 2026-08-07, five days after the model shipped (31 July 2026). Sources at the
bottom. **This is not the guide for `hailuo`** — that alias still points at the older
`minimax_hailuo` model and keeps its own file, `hailuo.md`. The two want opposite prompts: the old
one wants 40-100 narrative words, this one wants a 350-500 word structured document. Do not carry
habits across.

Specs marked **[live]** were probed on this machine with `higgsfield model get minimax_h3` and
`higgsfield generate cost minimax_h3` on 2026-08-07. Nothing here has been verified by an actual
paid generation yet.

---

## What it actually is

An omni-modal video model: text, images, video and audio all enter at the same level, and it returns
one clip of 5-15 s with **native stereo audio generated in the same pass** — dialogue, room tone,
foley, score. It is currently ranked #1 for video editing and #2 for text-to-video on Artificial
Analysis.

The thing that makes it different from everything else in this catalog is not the picture. It is
that **the prompt is a structured document with named fields, not a paragraph.** MiniMax publishes
the exact rewrite format their own pipeline produces, and the model was trained on it. A good free-
prose prompt underperforms a mediocre structured one.

**Mental model:** every other video model here takes a description. H3 takes a *spotting sheet* —
picture track, dialogue track, ambience track, score track, each in its own named field, timed.

Under the hood there are three modules: `H3-Context-IR` (rewrites messy multimodal input into that
structured form), `H3-Base` (33B dense transformer, generates 768p video + audio), and
`H3-Regenerate-2K` (upscales to 2K in-context). On MiniMax's own API you can call Context-IR
separately to do the rewriting for you. **Through Higgsfield you cannot** — so writing the
structured form is our job, and that is what this file is for.

---

## Hard specs

| | MiniMax direct (official docs) | Higgsfield route (our CLI) |
|---|---|---|
| Duration | 4-15 s, integers | **5-15 s** — the floor is 5 here, not 4 **[live]** |
| Resolution | 768p native, 2K via Regenerate-2K | **2K only**, no cheaper tier exposed **[live]** |
| Frame rate | 24 fps | same |
| Audio | native, 32 kHz stereo, in-pass | same |
| Aspect | 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, adaptive | same list, default `auto` **[live]** |
| Prompt length | ≤ 7000 characters | same field |
| Reference images | ≤ 9 (≤ 30 MB each) | `--image-references`, repeatable **[live]** |
| Reference videos | ≤ 3, each 2-15 s, ≤ 15 s total (≤ 50 MB) | `--video-references`, repeatable **[live]** |
| Reference audio | ≤ 3, each 2-15 s, ≤ 15 s total (≤ 15 MB) | `--audio-references`, repeatable **[live]** |
| Total ref files | 12 | same |
| Cost | $0.13/s at 2K | **4 credits per second, flat** **[live]** |
| Prompt rewriting (Context-IR) | callable as its own API mode | **not exposed** |
| Open weights | yes, MiniMax H3 Community License | n/a |

**Cost, measured, not estimated [live]:** 5 s = 20 credits, 6 s = 24, 10 s = 40, 15 s = 60. Exactly
4 credits a second with no discount for length.

That puts H3 in the **upper middle** of the catalog, not at the top. Corrected 2026-08-07 after a
full sweep: the earlier version of this line called H3 the most expensive video model here, which
was wrong. It compared a 15 s H3 clip against other models quoted at 5 s. Per second, at each
model's own default quality, H3 is eighth:

| Model | Credits/s | | Model | Credits/s |
|---|---|---|---|---|
| `seedance2.5` | 6.5 | | **`h3`** | **4.0** |
| `flux-video` | 5.5 | | `gemini` | 3.0 |
| `cinematic3`, `cinematic3.5`, `marketing` | 5.0 | | `veo3.1` | 2.75 |
| `seedance`, `grok-video1.5` | 4.5 | | `happy-horse`, `seedance-mini` | 2.5 |

One correction to the top row **[re-measured live 2026-08-19, re-verified 2026-08-23]**:
`seedance2.5` used to drop to 4.0/s for any job run from a video reference, which used to put the
two models on the same rate. **That discount is gone.** It charges 6.5/s at 720p in every mode, so
the gap this table shows holds whether or not there is a clip to work from. What moves its bill
instead is duration: a `video_edit` is charged the source clip's own length rather than the length
you ask for.

What is still true, and is the real constraint: **there is no 768p draft tier on this route, so
every roll is a full-price 2K roll.** Models below it in that table mostly have a cheap resolution
step; H3 does not. Block the idea out on `kling-turbo` (7.5 for 5 s) or `seedance-mini` (12.5) and
bring only the settled shot here.

Verified rejections **[live]**: `--duration 4` and `--duration 3` are refused with "Input should be
greater than or equal to 5"; `--duration 16` with "less than or equal to 15".

---

## The five modes, and which ones we can actually reach

MiniMax names five base modes. Higgsfield exposes them through media flags:

| Mode | What it is | Our CLI |
|---|---|---|
| T2VA | text only, model builds the whole timeline | prompt alone |
| I2VA | first frame supplied, develop forward | `--start-image` |
| FL2VA | first and last frame supplied, interpolate between | `--start-image` + `--end-image` |
| L2VA | **last frame only**, infer a plausible opening | **unreachable** |
| Ref2VA | subjects/scene/motion/voice carried from reference assets | `--image-references` / `--video-references` / `--audio-references` |

**L2VA is not available through Higgsfield [live].** The API rule is `end_image requires
start_image`, so a last-frame-only job is refused. If a shot has to land on a specific final frame
and the opening is free, either supply an opening frame too (making it FL2VA) or run it on MiniMax
direct.

Two more live constraints, both hard:

- **Keyframes and references cannot be mixed.** `start_image`/`end_image` in the same request as any
  reference media is refused. So "start on this frame *and* carry this character from that photo" is
  not one job — pick the frame route or the reference route.
- **Audio references cannot travel alone.** At least one image or video reference must come with
  them.

For text-only jobs set the aspect ratio explicitly. MiniMax's docs state text-to-video requires a
concrete ratio rather than adaptive, and our default is `auto`.

**What `scripts/generate.py` actually passes today:** one reference image (`-r/--ref-image`) and one
reference video (`--video-references`), plus both keyframes. The Higgsfield CLI itself accepts the
flags repeatedly for up to 9 images and 3 videos, and accepts audio references — our wrapper does
not forward those yet. So a full Ref2VA job with several subjects or a voice-timbre reference has to
be run on the `higgsfield` CLI directly until the wrapper catches up.

---

## The base prompt format (T2VA, I2VA, FL2VA)

Three named fields, in this order, always:

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

For the keyframe modes, one alignment line comes **first**, then a blank line, then the fields.
MiniMax gives these verbatim:

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
```

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.
```

`S.SS` is the clip duration to exactly two decimals. `N` is the index of the real final shot.

What goes where:

- **integrated_multimodal_description** — the body. Picture, action, shot changes, who speaks, what
  they say, and any sound tied to a moment. Everything in it must be something you can see or hear.
- **overall_soundscape** — 1-4 sentences, one paragraph, ambience and physical/non-verbal human
  sound across the whole clip (wind, traffic, footsteps, fabric, breathing). Never repeat dialogue
  or diegetic music here. `N/A` only if silence was explicitly asked for.
- **non_diegetic_music** — 1-3 sentences of score the characters cannot hear. Instrumentation,
  tempo, rhythm, dynamics. **No mood words and no explaining what the music is for.** `N/A` if
  there is none. Music a character can hear (radio, band, phone) is diegetic and belongs in the body
  instead.

Open Shot 1 with the style and the initial framing: `[Shot 1] Live-action, cinematic, a medium-wide
shot frames...`. Style vocabulary MiniMax names: cinematic, live-action, 2D-animated, 3D CG,
claymation, watercolor, vintage film. On keyframe modes take the style from the supplied image
rather than inventing one.

### Shots and cuts

Shot 1 carries no timestamp. Every later shot opens with a strictly increasing cut time inside the
duration:

```text
[Shot 2] At 00:03.500, the camera cuts to...
```

Accepted cut phrasings: `the camera cuts to`, `the shot cuts to`, `the shot transitions to`, `the
shot changes to`, `the shot switches to`. Cross-dissolve, fade and wipe only when the brief asks for
them. A cut has to deliver new information — new subject, space, state, viewpoint or time. If all
that changes is distance or a small angle, **move the camera instead of cutting.**

### Camera: type, then amplitude, then speed

Written as natural English action inside the sentence, never stacked as labels at the end. Add
amplitude and speed only when they matter; medium and normal are the assumed defaults.

| Dimension | Values |
|---|---|
| Motion type | `Zoom In / Zoom Out`, `Push In / Pull Out`, `Pan Left / Pan Right`, `Truck Left / Truck Right`, `Tilt Up / Tilt Down`, `Pedestal Up / Pedestal Down`, `Arc Shot`, `Tracking Shot`, `Static Shot`, `Shake Slightly / Shake Strongly`, `POV`, `Roll Clockwise / Roll Counterclockwise` |
| Amplitude | `with small amplitude`, `with large amplitude` |
| Speed | `at slow speed`, `at fast speed` |

```text
The camera pushes in with small amplitude at slow speed toward the folded letter in her hands.
The camera holds a static shot as the runner exits the frame.
```

Zoom changes focal length with the body still; Push In moves the body. Pan pivots in place; Truck
translates. The model treats these as distinct — using them loosely is how you get the wrong move.

### Speakers, dialogue and singing

Anyone who speaks, sings or makes an off-screen vocal sound gets a stable ID: `(S1)`, `(S2)`, and
`(S1,S2)` when they vocalise together. The ID follows the character across every shot. Characters
who never make a sound get no ID.

Identification, delivery and action sit **outside** the dialogue tag. Inside it goes only a language
tag and the exact words:

```text
The young woman with a quiet, breathy voice (S1) says: <d>[English] I get off at the next station.</d>
The two children (S1,S2) shout together, <d>[English] Wait for us!</d>
```

Every original word and punctuation mark is preserved verbatim. Never translate or paraphrase a line
the client wrote. For unintelligible source audio write `[unclear]` rather than guessing.

Voiceover has a fixed phrase, `says in an off-screen voiceover`, and must be followed immediately by
a statement that the on-screen character's lips stay shut — otherwise you get a talking head:

```text
The man (S1) says in an off-screen voiceover: <d>[English] I still remember that road.</d> while his lips remain completely closed.
```

A line that crosses a cut is marked `<scenetrans>` at both connecting points, with the continuity
stated in words (`continues seamlessly across the cut`, `carries over from the previous shot`).
Speech chopped off by the end of the clip is marked `<cutoff>`.

First time a speaker appears, establish the voice: type, age, gender, on- or off-screen, pitch,
timbre, rate, accent. That is what keeps a voice stable across shots.

### On-screen text

Anything actually legible in frame — a sign, a banner, a label, a subtitle, neon — goes in English
double quotation marks, verbatim, untranslated, in whatever alphabet it is written in:

```text
A red neon sign reading "ΑΝΟΙΧΤΑ" glows above the doorway.
```

---

## Full-reference mode (Ref2VA)

When reference assets are attached, the body field changes name and four more sections appear. Six
sections, in this order:

| Section | Job |
|---|---|
| `subject_definitions` | declares each referenced thing and its label |
| `summary` | one paragraph, opens with a bracketed task-type prefix |
| `retention_analysis` | one line per label: what survives into the target |
| `detailed_description` | the body, in playback order (replaces `integrated_multimodal_description`) |
| `overall_soundscape` | as above |
| `non_diegetic_music` | as above |

Four label types, and the distinction matters:

- `<Subject N>` — **visible content that gets reused**: a person, an animal, a prop, a location, a
  costume, a style, a pose, an action. This is the workhorse label.
- `<Picture N>` — an image acting as a **concrete frame or composition anchor** (first frame,
  keyframe, last frame, storyboard). If an image only defines a character or a look, do *not* give
  it its own line — cite it inside that subject's definition.
- `<Video N>` — **whole-video relationships only**: the clip being edited, the clip being continued,
  or a clip supplying camera movement, cutting and rhythm. A person or object lifted out of a
  reference video is still a `<Subject N>`.
- `<Audio N>` — a copied or referenced audio signal: music style, voice timbre, dialogue, effects,
  beat.

```text
<Subject 1> is the young woman in <Picture 1>, with long dark hair, a blue cardigan, and a thin silver necklace.
<Subject 1> is the woman whose appearance comes from <Picture 1> and whose walking motion comes from <Video 1>.
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
```

A label means the same thing in every section once assigned. Video and audio labels are numbered
independently, so `<Video 1>` and `<Audio 2>` can be the same file — and a reference video does not
automatically produce an `<Audio N>` just because it has sound.

**`summary` opens with the task type in square brackets**, joined with ` + ` when several apply:
`keyframe completion`, `reference generation`, `video editing`, `video continuation`, `audio reuse`,
`audio reference`. A reference video that only lends camera movement or rhythm is `reference
generation`, not `video editing` — `video editing` means that source clip is genuinely being
modified.

**`retention_analysis` uses fixed markers, one line per label.** Visible content:
`fully_preserved`, `partially_preserved`, `attribute_transfer`, `weak_reference`. Audio:
`fully_copy`, `partially_copy`, `reference`, `weak_reference`.

```text
<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - the blonde hair and light-pink shirt are retained.
<Video 1> (cut and pacing structure): weak_reference - only the rhythm of the cutting is followed.
<Audio 2>: reference - the target speaker follows its timbre and measured delivery without copying the signal.
```

Two differences from base mode that are easy to miss: the style line is stated **before** `[Shot 1]`
rather than inside it, and speaker IDs are **never written in `retention_analysis`** — they live in
`detailed_description` only. When a referenced subject speaks, carry both labels: `<Subject 2> (S1)`.

---

## Length

`detailed_description` runs **350-500 English words** for a generation task. That is the single
biggest departure from every other guide in this folder. Dialogue-heavy work fits the full spoken
timeline first and lets the word count fall where it lands; editing tasks scale with the source
clip. A single-shot clip does not license a short description — spread the detail across the shots
by how much is actually happening in each.

Under-writing is the common failure. The field is 7000 characters; a 60-word prompt leaves the
soundtrack, the camera and half the performance to the model's own taste.

---

## Worked example (T2VA, 10 s, one cut)

```text
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a
luthier's workshop at dusk, wood shavings across the bench and a half-strung bouzouki clamped in a
vice. Warm tungsten light falls from a single lamp above the bench, leaving the back wall in shadow.
The camera pushes in with small amplitude at slow speed as a man in his sixties, grey stubble, dark
apron, a low and slightly hoarse voice (S1), draws a bow of rosin across the strings and stops. He
turns the tuning peg a quarter turn and says: <d>[Greek] Ακόμα δεν είναι έτοιμο.</d> His hand stays
on the peg. [Shot 2] At 00:05.000, the shot cuts to a close-up of the soundhole, dust drifting in
the lamplight, while the last of his words carries over from the previous shot. The camera holds a
static shot as one string is plucked once and the body of the instrument resonates.

overall_soundscape: Quiet workshop room tone with a faint electrical hum from the lamp. A bow drags
once across wound strings, a wooden peg creaks under pressure, and a single plucked note rings and
decays into the room.

non_diegetic_music: N/A
```

Note what is doing the work: the style and framing open Shot 1, the camera move carries type,
amplitude and speed, the speaker is established by voice before the line, the Greek line is untouched
and untranslated, the cut arrives with a timestamp and carries the audio across it, and the score is
explicitly `N/A` rather than left silent for the model to fill.

---

## What NOT to do

- **Do not write it as prose.** No field names means the model reconstructs them itself, badly.
- **Do not leave the sound blank.** The audio is generated in the same pass either way. Unwritten
  means invented — usually generic music you did not want.
- **Do not put dialogue in `overall_soundscape` or `non_diegetic_music`.** Spoken lines live only
  inside the dialogue tags in the body.
- **Do not use mood words for score.** "Tense", "uplifting", "emotional" get ignored. Instruments,
  tempo, dynamics.
- **Do not translate or tidy a client's line.** Verbatim inside the tag, original language, original
  punctuation.
- **Do not cut when a camera move would do.** Cuts are for new information.
- **Do not attach a reference without saying what it is for** in `subject_definitions`. An
  unexplained asset gets averaged into everything.
- **Do not mix keyframes with reference media,** and do not send audio references alone. Both are
  refused before generation **[live]**.
- **Do not stack camera labels** at the end of a sentence. Write the move as an action.
- **Do not draft here.** 4 credits a second, 2K only. Find the shot on a cheap model first.

---

## Cost discipline

At 4 credits a second with no draft tier, a three-take 10 s shot is 120 credits. Before generating:
run `--cost`, present the number, and say what a re-roll costs. Prefer FL2VA over a re-roll where the
problem is the ending rather than the whole shot — supplying both keyframes constrains the take far
more cheaply than rewriting and hoping.

---

## Sources

Official (authoritative):
- https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md — the base-mode format, camera table, dialogue and sound rules. Primary source for most of this file.
- https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md — full-reference mode, labels, retention markers, task types.
- https://huggingface.co/MiniMaxAI/MiniMax-H3 — model card: architecture, checkpoints, limits, licence.
- https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md — MiniMax's own prompt-writing skill.
- https://platform.minimax.io/docs/guides/video-generation — API limits, modes, file constraints.
- https://www.minimax.io/news/minimax-h3-open-source — release announcement.

Live probes on this machine (2026-08-07): `higgsfield model get minimax_h3`, `higgsfield generate
cost minimax_h3 --duration {3,4,5,6,10,15,16}`. Source of every **[live]** marking.

Practitioner (cross-checked, lower trust):
- https://huggingface.co/blog/ResterChed/minimax-h3-hailuo-3-0 — specs, pricing, ranking.
- https://mixio.studio/hailuo-h3-prompt-guide — reference-role framing.
- https://morphic.com/resources/models/minimax-h3 — input limits.

**Flagged, not resolved:** whether Higgsfield runs Context-IR rewriting server-side before
generation is undocumented — this guide assumes it does not and writes the structured form
explicitly, which is safe either way. The 4 s floor in MiniMax's docs versus the 5 s floor measured
on our route is unexplained; treat 5 s as the real minimum here.
