# HappyHorse 1.0

Covers: `happy-horse` (`happy_horse_video`)

A 15 billion parameter open weights video model from Future Life Lab (Alibaba's Taotian Group), led
by Zhang Di, formerly technical lead on Kling. It took the number one slot on the Artificial
Analysis Video Arena for both text to video and image to video, ahead of Seedance 2.0. On this route
it is the best value in the mid tier: near flagship quality at 2.5 credits a second.

---

## Hard specs

| | Vendor claim | On our Higgsfield route **[live 2026-08-07]** |
|---|---|---|
| Duration | n/a | **3 to 15 s**, integer seconds |
| Resolution | up to 1080p | **720p or 1080p** |
| Aspect | n/a | 16:9, 9:16, 1:1, 4:3, 3:4 |
| Audio | native, single pass, synchronised | generated with the video, no switch exposed |
| Lip sync | 7 languages, very low word error rate | same |
| Start frame | image to video is its strongest category | `start_image` |
| End frame / references | not on this route | **no `end_image`, no reference media** |
| Cost | n/a | **2.5 credits/s at 720p, 4.5 credits/s at 1080p** |

**Cost, measured not estimated [live]:** 720p is 7.5 credits for 3 s, 12.5 for 5 s, 25 for 10 s,
37.5 for 15 s. 1080p is 22.5 for 5 s. Linear.

At 2.5 credits a second it sits alongside `seedance-mini` and well under `seedance` (4.5) or
`seedance2.5` (6.5), while benchmarking above Seedance 2.0. **This is the default choice when you
want quality without flagship pricing.**

Verified rejections **[live]**: `--duration 2` gives "Input should be greater than or equal to 3";
`--duration 16` gives "less than or equal to 15".

---

## What it is good at

The architecture is a 40 layer single stream transformer: text, video and audio tokens run in one
unified sequence with no cross attention, so **audio and picture are generated together rather than
dubbed afterwards**. In practice that means dialogue, Foley and ambience land in sync without being
separately directed, and lip sync holds across seven languages.

Denoising runs in 8 steps rather than the usual 50 or more, which is why it is fast.

Its two documented strengths are **motion consistency** and **image to video fidelity**, where it
set the arena record. Its weakness relative to `kling` and `cinematic3` is deliberate camera
choreography: it is a prompt first model, not a camera move specialist. If the shot lives or dies on
a precise crane move, use Kling.

---

## Prompt shape

Plain, prompt first, minimal ceremony. This model was built so that a clear sentence works without a
formula, and it does not reward the structured field syntax that `h3` or `seedance2.5` need.

Write one continuous take: subject, what it does, where it is, how it is lit, what you hear.
Keep camera direction simple and let the model carry motion.

```
A fishmonger in a rubber apron lifts a crate of ice onto a steel counter in a covered
market at dawn. Cold blue light from the roof vents, warm bulbs over the stalls behind him.
Medium shot, locked off. Ice clattering, distant voices, a radio playing faintly.
```

Because audio is generated jointly, **describe what you hear in the same breath as what you see**.
Do not write it as a separate technical block the way `grok-video1.5` wants.

---

## Image to video

This is its record setting category. Pass a settled frame as `start_image` and describe only the
motion. Composition and identity hold well across the full 15 s, which is longer than most models on
this route sustain.

There is **no end frame and no reference media** on this route, so it cannot do keyframed
transitions or character locking from a reference sheet. For those, use `wan` or `seedance2.5`.

---

## What NOT to do

- **Do not expect keyframing.** No `end_image` parameter exists here.
- **Do not expect reference media.** No image, video or audio references on this route.
- **Do not write elaborate multi axis camera choreography.** It is not a camera specialist. Use
  `kling` or `cinematic3` for that.
- **Do not separate the sound design into its own block.** Audio and picture come from one pass.
- **Do not use quality boosters** ("8k", "masterpiece"). They do nothing.
- **Do not go under 3 s.** Hard rejection.

---

## Cost discipline

2.5 credits a second is cheap enough to iterate directly, which is unusual on this route. A 5 s
720p test is 12.5 credits. Draft here, and only move to `seedance2.5` or `flux-video` if you need
something this model cannot do (references, editing, extension, period pastiche).

```bash
python skills/image-gen/scripts/generate.py "..." --video -m happy-horse --duration 5 --cost
```

---

## Sources

- [HappyHorse 1.0 on Hugging Face](https://huggingface.co/happyhorse-ai/happyhorse-1.0)
- [HappyHorse 1.0 on fal](https://fal.ai/happyhorse-1.0)
- [HappyHorse tops the Artificial Analysis leaderboard](https://www.barchart.com/story/news/1210723/happyhorse-1-0-crowned-1-open-source-ai-video-generator-tops-artificial-analysis-global-leaderboard)
- [Architecture and benchmark breakdown](https://www.xugj520.cn/en/archives/happyhorse-1-open-source-ai-video-leaderboard.html)
- Everything marked **[live]** was measured against `higgsfield generate cost` and
  `higgsfield model get happy_horse_video` on CLI 1.1.20, 2026-08-07.
