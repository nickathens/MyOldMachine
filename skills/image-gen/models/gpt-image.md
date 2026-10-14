# GPT Image 2 and 2.5 / Hazel (OpenAI)

Covers: gpt (GPT Image 2), gpt2.5 (GPT Image 2.5), hazel (OpenAI Hazel)

## Prompt Style

Structured visual direction. Think like a director, not a prompter. Layered prompts (subject, style, lighting, composition, constraints) deliver consistent results.

## Optimal Length

40-100 words. Structure beats length.

## Key Techniques

- **Visual direction over fantasy.** Describe the photograph: lens, framing, time of day, light source, texture, surface wear, believable imperfection, ordinary background detail.
- **Lens parameters.** Replace vague keywords with specific optics: "85mm f/1.4 bokeh" beats "blurry background". Use "35mm wide angle", "50mm standard", "135mm telephoto" for framing control.
- **Lighting specification.** Name the light source: "single softbox camera-left", "golden hour backlit", "overcast diffused", "neon reflected off wet pavement".
- **Two-column edits.** When editing, explicitly state what changes and what stays locked: "Preserve the bottle and label exactly. Change only the background to a marble countertop."
- **Character consistency.** Break characters into a 5-tuple: age + ethnicity + hairstyle + iconic features + clothing. Keep this fixed across generations.
- **Text rendering.** Strong text capability. Enclose text in quotes. Specify font style, color, and placement.
- **Add imperfection.** "Slight film grain", "minor lens flare", "visible fabric texture" push results toward photorealism.

## What NOT to Do

- Don't use SDXL/Midjourney-era keywords ("masterpiece", "best quality", "8k uhd") as they occupy semantic space intended for real descriptions
- Don't mix too many styles in one prompt
- Don't describe impossible physics without acknowledging it as surreal/fantasy

## Model Differences

- **gpt**: GPT Image 2. `quality` low, medium or high, and high is its default; 1k, 2k or 4k, 2k by default. 6.5 credits when nothing is chosen.
- **gpt2.5**: GPT Image 2.5. `quality` low, medium, high, xhigh or max, and **its default is low**, so `-m gpt2.5` with nothing chosen buys a 0.25 credit draft, not GPT Image 2's high. Choose the quality on purpose: at 1k, low is 0.25, medium 0.5, high 1.5, xhigh 2.5 and max 5; max at 4k is 15. Also `variant` flare (the default) or sunburst at the same price, `background` auto, opaque or transparent, and four more ratios: 27:16, 16:27, 9:8 and 8:9.

## Example Refinement

Bad: "portrait of a woman, beautiful, 8k, masterpiece"
Good: "Editorial portrait of a woman in her 30s, strong jawline, dark hair pulled back loosely. Shot on 85mm f/1.4, shallow depth of field. Single softbox camera-left creating dramatic shadow on the right cheek. Matte skin, no retouching. Linen collar visible. Neutral gray studio backdrop."
