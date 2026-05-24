# GPT Image 2 / Hazel (OpenAI)

Covers: gpt (GPT Image 2), hazel (OpenAI Hazel)

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

## Example Refinement

Bad: "portrait of a woman, beautiful, 8k, masterpiece"
Good: "Editorial portrait of a woman in her 30s, strong jawline, dark hair pulled back loosely. Shot on 85mm f/1.4, shallow depth of field. Single softbox camera-left creating dramatic shadow on the right cheek. Matte skin, no retouching. Linen collar visible. Neutral gray studio backdrop."
