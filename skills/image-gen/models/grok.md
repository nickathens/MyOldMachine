# Grok Image (xAI)

Covers: grok (Grok Image)

## Prompt Style

Precise visual specifications. Grok responds best to concrete, measurable details rather than abstract aesthetic terms. Treat the prompt like a photography brief.

## Optimal Length

40-80 words. Every word should add visual information.

## Key Techniques

- **Specificity over aesthetics.** "A 60-year-old fisherman mending a net on a wooden dock at dawn" beats "an old man by the sea". Replace every adjective with a concrete detail.
- **Lens and aperture.** "Canon EOS R5, 85mm f/1.2" produces more controlled results than "portrait lens". Specify focal length for framing control.
- **Controlled imperfection.** Add one or two physical-world imperfections: "slight motion blur on the hands", "raindrops on the lens", "visible film grain". Pushes toward photorealism.
- **In-sentence negation.** No dedicated negative prompt field. Negate by describing the positive: instead of "no glasses" write "bare face, no eyewear". Instead of "no background clutter" write "clean, minimal background".
- **Lighting as mood.** Name the light source and its quality: "diffused overcast light", "hard noon shadow", "single tungsten bulb overhead". Lighting is the strongest mood lever.

## What NOT to Do

- Don't use quality boosters ("masterpiece", "8k", "trending on artstation") as they dilute the actual description
- Don't rely on negative prompt syntax (brackets, minus signs) as it is not supported
- Don't write vague prompts expecting the model to infer intent

## Example Refinement

Bad: "cool photo of a dog, 4k, amazing quality"
Good: "Golden retriever mid-leap catching a tennis ball in a suburban backyard. Late afternoon side-light, long shadow on fresh-cut grass. Shot on Nikon Z9, 70-200mm f/2.8 at 200mm, 1/2000s freeze. Slight motion blur on the tail. Shallow depth of field, fence posts soft in the background."
