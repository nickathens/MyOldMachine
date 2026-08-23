# FLUX 3 Video (Black Forest Labs)

Covers: `flux-video`, `flux3-video` (`flux_3_video`)

Black Forest Labs' first video model. Unrelated to `flux` / `flux-kontext`, which are the FLUX still
image models with their own guide (`flux.md`). Do not reuse still FLUX prompt habits here.

---

## Hard specs

| | Vendor claim | On our Higgsfield route **[live 2026-08-07]** |
|---|---|---|
| Duration | 5 to 20 s | **5 to 20 s**, integer seconds |
| Resolution | 720p and 1080p | **720p or 1080p** |
| Aspect | 7 ratios | 21:9, 2:1, 16:9, 4:3, 1:1, 3:4, 9:16, auto |
| Audio | native, dialogue plus SFX plus ambience | `generate_audio`, **default on** |
| Keyframes | first frame, last frame, multiple keyframes | `start_image` + `end_image` only |
| Video continuation | up to 4 s of source video and audio | `video_references` |
| Image references | yes | `image_references`, **10 max** across all image slots |
| Draft mode | fast cheap preview, then enhance | **not exposed** |
| Cost | n/a | **5.5 credits/s at 720p, 9 credits/s at 1080p** — but see the video reference tier |

**Cost, measured not estimated [live]:** 720p is 27.5 credits for 5 s, 55 for 10 s, 82.5 for 15 s,
110 for the 20 s maximum. 1080p is 45 for 5 s. Linear, no length discount.

**A video reference more than doubles the rate [live 2026-08-10].** Those are the prices for a roll
with no `video_references` file. Attach one — which is the only way to use the video continuation
this route advertises — and the rate goes to **13 credits/s at 720p and 17 at 1080p**, also exactly
linear:

| Duration | 720p plain | 720p continuation | 1080p plain | 1080p continuation |
|---|---|---|---|---|
| 5 s | 27.5 | **65** | 45 | **85** |
| 10 s | 55 | **130** | 90 | **170** |
| 15 s | 82.5 | **195** | 135 | **255** |
| 20 s | 110 | **260** | 180 | **340** |

The file is what moves the price, not any mode setting: this model has no `mode` param, and an
`image_references` file leaves the quote untouched at 27.5. `generate_audio` makes no difference
either way. Quote a continuation at the headline rate and you
understate a 20 s piece by 150 credits, so always `--cost` with the reference actually attached.

This used to be the mirror image of `seedance_2_5`, which *dropped* from 6.5 to 4 credits/s on the
same trigger. **Seedance's discount is gone [re-measured live 2026-08-23]**: it charges 6.5/s at
720p in every mode. The ranking still inverts, by half as much as it did. For a plain roll Flux 3 is
the cheaper of the two, 5.5 against 6.5; the moment there is a clip to work from Flux 3 is 13/s
against Seedance's unchanged 6.5. Compare on `video_extension`, which bills the duration you ask
for, because `video_edit` bills the source clip's own length instead. See `seedance-2-5.md`.

**The draft mode is the thing we are missing.** BFL's own workflow is: render a cheap low resolution
draft, iterate on it, then send the cached draft back for a full quality enhance. Our route has no
`draft` setting, so **every roll here is full price.** Treat 720p as the draft tier and 1080p as the
only delivery tier.

Verified rejections **[live]**: `--duration 4` and `--duration 3` give "Input should be greater than
or equal to 5"; `--duration 21` gives "less than or equal to 20".

Live rules the API enforces: at most 10 images across `start_image`, `image_references` and
`end_image`; image inputs and `video_references` **cannot be mixed**; `end_image` requires
`start_image`.

---

## The one rule that matters most: name the format, not just the subject

FLUX 3 was trained with heavy world knowledge and does its own prompt expansion. Its strongest
behaviour, and the one that separates it from every other model on this route, is that **naming a
year and a medium makes it infer camera, lens, grain, lighting, editing and pacing for you.**

```
a 1969 documentary about Woodstock
a 1987 local news report about teenagers hanging out at the mall
archival footage of the Wright brothers' first flight in 1903. No sound
```

Those are complete prompts. Six words carry a full look. Over specifying a known subject actively
fights the model. **Write at the level you actually think.**

This makes FLUX 3 the best model in the catalog for period pieces, archival pastiche, explainers and
documentary texture, and a poor choice when you need a precise, invented, non referential frame.
For that, use `seedance` or `kling`.

---

## Prompt shape

Plain declarative prose. Conversational sentences, not caption syntax and not keyword lists.
Attention is strongest in the **first few hundred words** and later clauses compete for focus, so
front load what matters.

Name every layer you actually want to control: camera, subject, action, light, audio. Anything you
leave out gets a sensible default rather than a random one, so silence is a legitimate choice.

**Budget 5 to 7 seconds per shot beat.** One continuous take with a single small motion fits 5 to
7 s. A three cut spot needs 12 to 18 s.

---

## Camera language

Six named moves are reliably recognised. Use these words:

| Move | Phrasing that works |
|---|---|
| Static | camera locked at eye level |
| Push in | camera pushes slowly forward from wide to close |
| Pull back | camera pulls slowly back, revealing the room |
| Pan / tilt | camera pans slowly right across the shelves |
| Orbit | camera orbits smoothly clockwise around the subject |
| Handheld | wide handheld tracking shot alongside the subject |

Pair the move with a lens to set register: 35mm documentary, 70mm portrait compression,
24mm subjective, anamorphic cinematic.

---

## Audio

Audio is on by default and generated in the same pass: dialogue, effects and ambience, with lip sync
across English, Chinese, Spanish, French, German, Japanese, Portuguese, Russian, Italian, Indonesian,
Turkish, Hindi and Punjabi.

**To get a silent clip you must say so.** Write `No sound` in the prompt, or set
`--extra '{"generate_audio":false}'`. A prompt that simply omits audio still gets an invented bed.

Layer it explicitly: interior room tone, then a specific effect, then a distant element. Direct
music by instrumentation and tempo rather than by genre.

---

## Multi-shot

FLUX 3 can cut within a single generation. Beats are separated with a hard cut instruction in the
prompt; thread one continuous audio bed across the cuts so they read as one piece rather than three
clips. Keep shots contrasting (wide, then close, then detail) and give the whole thing 12 to 18 s.

---

## Style registers

The model is unusually broad outside photoreal. Name the medium and its material tells:

- **Motion design**: name vector qualities and ease characteristics.
- **Hand drawn**: cite a style anchor, for example Ghibli adjacent watercolour with visible pencil linework.
- **Stop motion**: name the material artefacts, for example visible fingerprints in the clay.
- **Timelapse / hyperlapse**: state the time compression ratio and the motion blur treatment.

---

## Worked example (10 s, 720p)

```
An unbroken ten second shot inside a small secondhand bookshop on a rainy afternoon.
Warm brass picture lights over dark wooden shelves, a tabby cat asleep on the counter.
Camera locked at eye level for four seconds, then pans slowly right across the shelves.
Shot on 35mm, documentary register.
Audio: close interior room tone, a wall clock ticking, rain on the window,
one distant roll of thunder. No music.
```

---

## What NOT to do

- **Do not over specify a known subject.** Naming the year and the format does more than a paragraph
  of art direction, and the paragraph can fight the format.
- **Do not use keyword lists or caption syntax.** Plain sentences outperform them.
- **Do not bury the important beat.** Attention falls off after the first few hundred words.
- **Do not mix image inputs with a video reference.** Hard rejection.
- **Do not pass `end_image` without `start_image`.** Hard rejection.
- **Do not assume silence.** Audio is on by default; ask for `No sound` explicitly.
- **Do not expect a draft tier.** BFL has one, this route does not.
- **Do not exceed 10 images** across start, end and references combined.

---

## Cost discipline

5.5 credits a second at 720p makes a 20 s piece 110 credits, and 1080p nearly doubles it. Because
there is no draft mode, iterate at 720p and short durations, then commit once at length.

Those figures are for a roll from scratch. A **video continuation is 13/s at 720p**, so the same
20 s piece is 260, and iterating on one is dear enough that the cheap pass belongs on
`seedance_2_5` at 6.5/s instead, which halves it.

```bash
python skills/image-gen/scripts/generate.py "..." --video -m flux-video --duration 10 --cost
```

---

## Sources

- [FLUX 3 Video, Part 1: Generation](https://bfl.ai/blog/flux-3-video)
- [FLUX 3 model page](https://bfl.ai/models/flux-3)
- [Runware FLUX 3 Video prompting guide](https://runware.ai/docs/models/bfl-flux-3-video/guides)
- [fal FLUX 3 text to video examples and prompts](https://fal.ai/learn/tools/flux-3-video-examples-prompts)
- Everything marked **[live]** was measured against `higgsfield generate cost` and
  `higgsfield model get flux_3_video` on CLI 1.1.20, 2026-08-07.

Note: vendor sources disagree on the limits (fal lists 480p/720p and fixed 5/10/15/20 s steps;
Runware and BFL list 720p/1080p and any length 5 to 20 s). The **[live]** column above is what our
route actually accepts and is the one to trust.
