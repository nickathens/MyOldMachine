# Kling (Kuaishou)

Covers: `kling` (Kling 3.0), `kling2.6` (Kling 2.6), `kling-turbo` (Kling 3.0 Turbo)

---

## Hard specs **[live 2026-08-07]**

| | `kling` | `kling2.6` | `kling-turbo` |
|---|---|---|---|
| Duration | **3 to 15 s** | **5 or 10 s only** | **3 to 15 s** |
| Aspect | 16:9, 9:16, 1:1 | 16:9, 9:16, 1:1 | 16:9, 9:16, 1:1 |
| Start image | yes | yes | yes |
| End image | **yes** | no | no |
| Mode | `std` (default), `pro`, `4k` | none | none |
| Resolution | via mode | none | 720p (default), 1080p |
| Sound | `on` / `off` string | boolean, default true | none |
| Cost | **2.0 credits/s** std, 2.5/s pro, **6.0/s 4k** | **2.0 credits/s** | **1.5 credits/s** 720p, 2.0/s 1080p |

`kling-turbo` was previously routed to this guide but never described in it. It is the cheapest of
the three at 1.5 credits a second, has no `end_image`, and is the right place to block out a Kling
shot before committing to `kling` for the keyframed version.

Kling 3.0's `4k` mode is **three times** the price of `std` (30 credits for 5 s against 10). It is a
delivery setting, never a drafting one.

Verified rejections **[live]**: `--duration 2` on `kling` and `kling-turbo` gives "Input should be
greater than or equal to 3"; `--duration 16` gives "less than or equal to 15".

**Kling is the camera specialist on this route.** When a shot lives or dies on a precise, motivated
camera move, this family beats `happy-horse`, `seedance` and `h3`. That is what to spend on here.

---

## Prompt Style

Cinematographer-driven. Think like a DP writing shot notes. Kling responds best to structured, layered prompts that separate camera behavior from subject action.

## Optimal Length

80-150 words. Kling benefits from detail but needs it organized.

## Key Techniques

- **5-part structure.** Organize every prompt: (1) Camera movement and lens, (2) Scene/environment, (3) Subject and action, (4) Mood/atmosphere, (5) Time and lighting. This order matches how the model processes.
- **Camera movement vocabulary.** Be specific: "slow dolly forward", "handheld tracking shot", "locked-off wide angle", "crane rising from street level", "steady push-in on subject's face". Vague "cinematic movement" gets generic results.
- **Single dominant action.** One clear motion per clip: "she turns to face the camera", "he sets the cup down and stands". Two simultaneous complex actions degrade quality.
- **@image references.** When using a reference image, prefix with @image and describe what to preserve: "@image maintain the character's face and outfit, place them in a rainy Tokyo street at night."
- **Motion intensity.** Kling supports a motion control parameter (0.1-1.0). Low values (0.1-0.3) for subtle movement, mid (0.4-0.6) for natural action, high (0.7-1.0) for dynamic scenes.
- **Native audio.** Kling 3.0 generates synchronized audio. Describe sound context: "busy cafe ambient noise", "footsteps on gravel", "wind through trees".

## What NOT to Do

- Don't describe multiple fast actions in sequence (the model picks one or averages them)
- Don't use still-image quality tags ("8k", "ultra HD")
- Don't ignore camera direction -- unguided camera produces random movement
- Don't mix contradictory moods ("peaceful yet intense", "calm chaos")

## Model Differences

- **kling**: Kling 3.0. Best quality, native audio, strongest motion coherence.
- **kling2.6**: Kling 2.6. Lower cost, no audio, still good for simple scenes.

## Example Refinement

Bad: "a person walking in the rain, cinematic, beautiful"
Good: "Slow tracking shot following a woman from behind as she walks through a narrow Tokyo alley at night. Handheld, slight sway. Neon signs in Japanese reflect off wet cobblestones. She carries a transparent umbrella, rain visible against backlit shop fronts. Warm tungsten from izakaya doorways, cool blue from vending machines. Ambient city hum and rain pattering on the umbrella. Shallow depth of field, anamorphic bokeh."
