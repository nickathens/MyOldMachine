# Veo (Google DeepMind)

Covers: `veo3` (Veo 3), `veo3.1` (Veo 3.1), `veo3-lite` (Veo 3.1 Lite)

---

## Hard specs **[live 2026-08-07]**

| | `veo3` | `veo3.1` | `veo3-lite` |
|---|---|---|---|
| Duration | **none, fixed length** | 4 / 6 / 8 s | 4 / 6 / 8 s |
| Start image | **REQUIRED** | optional | optional |
| End image | no | no | yes |
| Aspect | 16:9, 9:16 | 16:9, 9:16 | 16:9, 9:16, auto |
| Variants | `veo-3-fast` (default), `veo-3-preview` | `veo-3-1-fast` (default), `veo-3-1-preview` | none |
| Quality | none | `basic` (default), `high`, `ultra` | none |
| Audio | native | native | **off by default**, `generate_audio` |
| Cost | **22 flat** (fast), **58 flat** (preview) | 2.75 credits/s basic and high, 6 credits/s ultra | **1.0 credit/s** |

**`veo3` is image to video only.** It has no `duration` parameter and `start_image` is a required
param: a prompt-only call is refused with "Missing required params: start_image". If you want Veo
from text alone, use `veo3.1` or `veo3-lite`.

**The flag is `--variant`, not `--model`.** This guide and the wrapper previously documented `model`
for both Veo models. `--model` is rejected with "Unknown params: model". Corrected 2026-08-07.

**`quality: high` costs the same as `basic`** on `veo3.1`: both quote 22 credits at 8 s, 16.5 at 6 s.
Only `ultra` is dearer, at 48 for 8 s. So there is no reason to run `veo3.1` on `basic`. Measured,
not assumed. If that changes, `--cost` will show it.

**`veo3-lite` is the cheapest video model on the route at 1 credit a second** (4, 6 and 8 credits
for 4, 6 and 8 s). It is the right default for blocking out any Veo shot. Note its audio is **off**
by default, unlike the other two, so pass `--extra '{"generate_audio":true}'` if you need sound.

`veo3-lite` also enforces one rule the others do not: **duration must be 8 when both `start_image`
and `end_image` are set.**

---

## Prompt Style

Concise cinematic direction. Veo is a thinking model that understands physics and intent. Write clear, natural descriptions focused on one key action and mood. Less is more.

## Optimal Length

30-100 words. Veo parses intent well from shorter prompts. Overlong prompts dilute focus.

## Key Techniques

- **Single dominant action.** One clear motion per clip: "a hawk swoops down and catches a fish from a still lake." Multiple complex actions cause the model to average or pick one.
- **Cinematic terminology.** Veo understands film language: "tracking shot", "pull focus", "crane down", "locked-off medium shot", "whip pan". Use these instead of describing camera math.
- **Material and physics cues.** Describe physical properties to ground realism: "steam rising from hot asphalt", "fabric rippling in wind", "dust motes caught in a shaft of light". These stabilize the physics simulation.
- **Time and light anchoring.** Specify the moment: "golden hour, fifteen minutes before sunset", "overcast midday, flat diffused light", "blue hour, last glow on the horizon". Time of day sets the entire visual tone.
- **Native audio (Veo 3+).** Veo 3 generates synchronized audio. Include sound design cues: "crashing waves", "distant thunder", "quiet room with a ticking clock". Sound reinforces the scene.
- **Negative prompts.** Veo supports negative prompts. Use for specific exclusions: "no text overlays", "no lens flare". Keep negatives minimal and targeted.

## What NOT to Do

- Don't write prompts over 150 words (the model loses coherence on later instructions)
- Don't describe rapid scene changes within a single generation
- Don't use still-image quality boosters ("4k", "masterpiece", "photorealistic")
- Don't contradict the physics you describe (e.g., "underwater scene with dry hair")

## Model Differences

- **veo3**: Highest quality, native audio, best physics. Use for hero content. **Image to video
  only, and a flat 22 credits (fast) or 58 (preview) regardless of length.**
- **veo3.1**: Updated model, strong general quality. Good default for video. Run it on `high`, which
  costs the same as `basic`.
- **veo3-lite**: Faster, lower cost, **1 credit a second, the cheapest on the route**. Good for
  iterations and drafts, and the only Veo with an end frame. Audio is off unless you ask for it.

## Example Refinement

Bad: "ocean waves crashing, beautiful sunset, 4k cinematic"
Good: "Slow-motion close-up of a wave breaking on dark volcanic rock. Golden hour backlight turning the spray into a curtain of glowing mist. Camera low and locked off, slightly wide angle. Sound of the wave crashing and pulling back over smooth stones. Deep amber sky, no clouds."
