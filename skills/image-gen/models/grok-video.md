# Grok Video (xAI)

Covers: `grok-video` (Grok Video)

**Grok Imagine Video 1.5 is a separate, newer model** with a different engine, different limits and
no aspect ratio control. It has its own guide, `grok-video-1-5.md`. Use `grok-video1.5` for that one.

---

## Hard specs **[live 2026-08-07]**

| | |
|---|---|
| Duration | **1 to 15 s**, integer seconds |
| Aspect | 16:9, 9:16, 1:1 |
| Start image | yes |
| End image / references | none |
| Resolution | not exposed |
| Cost | **1.5 credits/s** (1.5 for 1 s, 7.5 for 5 s, 22.5 for 15 s) |

At 1.5 credits a second and a **1 second floor**, this is the cheapest way on the whole route to test
a motion idea: a one second probe costs 1.5 credits. Use it to sanity check movement before paying
4.5 a second for `grok-video1.5`, which is the same family with better audio and photorealism.

Verified rejection **[live]**: `--duration 16` gives "Input should be less than or equal to 15".

---

## Prompt Style

Concise action-oriented direction. Similar principles to Grok Image but adapted for motion. Specific visual details over vague aesthetics.

## Optimal Length

40-80 words. Focused on one shot, one action.

## Key Techniques

- **Action clarity.** Lead with the subject and their motion: "A barista pours steamed milk into a ceramic cup, creating a rosetta pattern." Clear verb, clear result.
- **Camera as observer.** Describe where the camera is and how it behaves: "Eye-level, static, medium close-up." Let the subject provide the movement.
- **Physical detail for realism.** Include one or two grounding details: "steam rising from the cup", "flour on the apron", "condensation on the glass." These anchor the physics engine.
- **Light source naming.** Specify the light: "warm pendant light overhead", "cold fluorescent from the ceiling", "backlit by a large window." Named light sources produce consistent illumination.

## What NOT to Do

- Don't stack multiple complex actions in one prompt
- Don't use quality boosters or resolution tags
- Don't describe abstract or non-physical concepts
- Don't ignore camera position (unspecified camera produces random angles)

## Example Refinement

Bad: "coffee shop scene, aesthetic, cinematic"
Good: "Close-up of a barista's hands pouring steamed milk into a latte, white rosetta forming on dark espresso. Warm pendant light overhead, steam visible. Marble counter, ceramic cup. Camera locked off, eye level. Quiet cafe ambience."
