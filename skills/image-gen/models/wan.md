# Wan (Alibaba)

Covers: wan (Wan 2.7), wan2.6 (Wan 2.6)

## Prompt Style

Direct, literal descriptions. Wan performs best with straightforward scene descriptions and clear action statements. No metaphor, no abstraction.

## Optimal Length

30-80 words. Concise and literal.

## Key Techniques

- **Literal description.** Describe exactly what the camera should see: "A red sports car drives along a coastal highway, waves crashing against cliffs on the right. Bright midday sun, clear blue sky." Wan interprets prompts at face value.
- **Single action focus.** One motion, one clip: "the bird takes off from the branch and flies left out of frame." Multiple actions produce inconsistent results.
- **Camera simplicity.** Wan handles simple camera moves best: "static wide shot", "slow pan left", "gentle zoom in". Avoid complex multi-axis directions.
- **Environmental grounding.** Anchor the scene in a specific place: "Japanese garden in autumn", "industrial warehouse at night", "Mediterranean hillside village". Named environments produce more coherent backgrounds.
- **Color and weather.** Explicit color and weather cues help: "overcast sky, muted greens and grays", "golden sunset, warm tones", "heavy snowfall, low visibility".

## What NOT to Do

- Don't write elaborate multi-sentence camera directions (keep it simple)
- Don't use quality keywords ("4k", "ultra HD", "photorealistic")
- Don't describe rapid scene transitions within one generation
- Don't expect complex character interaction (best with 1 subject)

## Model Differences

- **wan**: Wan 2.7. Latest version, better motion coherence and detail.
- **wan2.6**: Wan 2.6. Lower cost, slightly less refined motion.

## Example Refinement

Bad: "beautiful nature scene, cinematic, stunning visuals, 4k"
Good: "A deer drinks from a still forest stream, early morning mist low over the water. Pine trees reflected on the surface. Camera locked off, wide shot. Soft diffused light through the canopy. The deer lifts its head and looks toward camera."
