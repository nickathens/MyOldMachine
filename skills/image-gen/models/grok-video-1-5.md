# Grok Imagine Video 1.5 (xAI)

Covers: `grok-video1.5` (`grok_video_v15`)

Distinct model from `grok-video` (`grok_video`), which stays in the catalog with its own guide
(`grok-video.md`). 1.5 runs on xAI's Aurora autoregressive engine and is **image first by design**:
its strongest and intended mode is animating a still you already control.

---

## Hard specs

| | Vendor claim | On our Higgsfield route **[live 2026-08-07]** |
|---|---|---|
| Duration | 6 to 15 s | **2 to 15 s**, integer seconds |
| Resolution | 720p, 24fps | **480p, 720p or 1080p** |
| Aspect ratio | n/a | **no `aspect_ratio` param at all** |
| Audio | native, synchronised, in the same pass | always on, not switchable |
| Start frame | primary mode | `start_image` |
| Image references | yes | `image_references` |
| Audio references | yes | `audio_references`, needs an image reference |
| Cost | n/a | **4.5 credits/s at 720p, 2.5/s at 480p, 8/s at 1080p** |

**Cost, measured not estimated [live]:** 720p is 13.5 credits for 3 s, 22.5 for 5 s, 45 for 10 s,
67.5 for 15 s. 480p is 12.5 for 5 s. 1080p is 40 for 5 s. Linear.

**This model has no aspect ratio control.** It is one of only two video models on the route without
the parameter (the other is `hailuo`). Passing one is a hard rejection, "Unknown params:
aspect_ratio". Framing comes from your start image, which is another reason to lead with one. The
wrapper now suppresses the flag automatically for this model.

Live rules the API enforces: `start_image` **cannot be combined with reference media**;
`audio_references` require at least one image reference; **1080p is not available with reference
media** at all.

Verified rejections **[live]**: `--duration 1` gives "Input should be greater than or equal to 2";
`--duration 16` gives "less than or equal to 15".

---

## The workflow this model wants

Generate the still somewhere else, then animate it here.

1. Build the frame on a still model you can iterate on cheaply (`nano`, `seedream`, `flux`). Settle
   composition, wardrobe, lighting and grade there.
2. Pass it as `start_image` and write a prompt describing **only what changes**.

The input image acts as the literal first frame, not a loose style hint, so composition, identity
and grade carry through. Splitting the work this way also means you iterate on look and on motion
independently instead of paying for both at once.

---

## Prompt shape

**Order is chronological and it is load bearing.** The model renders actions described early in the
prompt early in the clip. Anything buried at the end may arrive too late in the generation sequence
to appear at all. Write the beats in the order they happen: establishing state, then the movement,
then the closing detail.

Keep it focused. Three clear story beats outperform a sprawling multi scene description.

---

## Always write an explicit `Sound:` section

Audio is a separate design layer on this model and it rewards specificity more than almost anything
else in the prompt.

```
Weak:   Sound: city sounds, traffic.
Strong: Sound: cars passing, skateboard wheels on pavement, teenagers laughing,
        the distant rumble of a street.
```

Name materials, not categories: "water pulling back across stone" beats "water sounds". `no music`
is a valid and useful instruction. So is `camera not moving`.

---

## Camera

Defaults to static framing unless told otherwise, and **static usually reads as more cinematic than
unmotivated movement**. When you do want a move, say it in explicit directional language:
"tracking shot alongside", "slow aerial push in", "camera drifts gently to the left",
"locked, static".

---

## Intensity language

Vague verbs produce vague motion. Escalate them.

```
Weak:   The wave crests.
Strong: The wave crests fully and pitches forward, crashing down with tremendous force.
```

Progression phrasing works well for pacing: "starts slowly, then goes faster, then is going
very fast."

---

## Worked example (image to video, 8 s)

```
Sound: boots on wet gravel, a distant train, light rain on a metal roof, no music.

The figure stands still for a moment, breath visible in the cold. She lifts her head
and turns to look down the track. Her coat lifts slightly as a gust crosses the platform.
Camera locked, static, eye level.
```

Run with `--start-image plate.png`.

---

## What NOT to do

- **Do not pass an aspect ratio.** The model has no such parameter and rejects it.
- **Do not combine `start_image` with reference media.** Hard rejection, pick one.
- **Do not ask for 1080p with references attached.** Not available in that combination.
- **Do not bury the key action at the end of the prompt.** It may never render.
- **Do not write vague sound.** An unspecific `Sound:` line is close to no sound line.
- **Do not ask for 4K or "ultra detailed 8k".** It tops out where it tops out and the booster words
  just burn a generation.
- **Do not request unmotivated camera movement.** Static is the stronger default here.

---

## Cost discipline

4.5 credits a second at 720p makes 1.5 roughly three times the price of plain `grok-video` (1.5/s)
for the same length. Use the cheaper `grok-video` to test motion ideas, and bring only the settled
shot here. 480p is a genuine draft tier at 2.5/s.

```bash
python skills/image-gen/scripts/generate.py "..." --video -m grok-video1.5 --duration 8 --cost
```

---

## Sources

- [How to prompt Grok Imagine Video 1.5, Replicate](https://replicate.com/blog/grok-imagine)
- [xAI video generation docs](https://docs.x.ai/developers/model-capabilities/video/generation)
- [Grok Imagine Video 1.5 overview, WaveSpeed](https://wavespeed.ai/blog/posts/grok-imagine-video-1-5-image-to-video-api/)
- [Grok Imagine Video 1.5 guide, imagine.art](https://www.imagine.art/blogs/xai-grok-imagine-video-1-5-guide)
- Everything marked **[live]** was measured against `higgsfield generate cost` and
  `higgsfield model get grok_video_v15` on CLI 1.1.20, 2026-08-07.
