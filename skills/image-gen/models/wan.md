# Wan (Alibaba)

Covers: `wan` (Wan 2.7), `wan2.6` (Wan 2.6)

---

## Hard specs **[live 2026-08-07]**

| | `wan` (2.7) | `wan2.6` |
|---|---|---|
| Duration | **2 to 15 s** | **5 / 10 / 15 s only** |
| Aspect | 16:9, 9:16, 1:1, 4:3, 3:4 | 16:9, 9:16, 1:1 |
| Resolution | 720p (default), 1080p | `quality`: 720p (default), 1080p |
| Start / end image | **yes, both** | no |
| References | audio only, **1 max** | image, video, audio |
| Cost | **1.5 credits/s** 720p, 2.5/s 1080p | **2.6 credits/s** 720p, 4.0/s 1080p |

**Corrected 2026-08-07:** the wrapper advertised a 3 s floor for `wan`. The validator accepts **2 s**
(3 credits), so very short cutaways are available and were previously blocked by our own table.

`wan` is the cheapest model on the route that offers **both a start and an end frame** at
1.5 credits a second, which makes it the default choice for keyframed transitions on a budget.
`kling` also keyframes but costs 2.0/s, and `veo3-lite` keyframes at 1.0/s but locks you to 8 s when
both frames are set.

Live rules the API enforces: on `wan`, `end_image` requires `start_image`, and at most one audio
reference. On `wan2.6`, reference-to-video with `video_references` supports **only 5 or 10 s**, not 15.

---

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
