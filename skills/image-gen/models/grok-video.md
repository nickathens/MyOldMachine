# Grok Video (xAI)

Covers: grok-video (Grok Video)

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
