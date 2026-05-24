# Veo (Google DeepMind)

Covers: veo3 (Veo 3), veo3.1 (Veo 3.1), veo3-lite (Veo 3.1 Lite)

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

- **veo3**: Highest quality, native audio, best physics. Use for hero content.
- **veo3.1**: Updated model, strong general quality. Good default for video.
- **veo3-lite**: Faster, lower cost. Good for iterations and drafts.

## Example Refinement

Bad: "ocean waves crashing, beautiful sunset, 4k cinematic"
Good: "Slow-motion close-up of a wave breaking on dark volcanic rock. Golden hour backlight turning the spray into a curtain of glowing mist. Camera low and locked off, slightly wide angle. Sound of the wave crashing and pulling back over smooth stones. Deep amber sky, no clouds."
