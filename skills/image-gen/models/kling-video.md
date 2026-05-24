# Kling (Kuaishou)

Covers: kling (Kling 3.0), kling2.6 (Kling 2.6)

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
