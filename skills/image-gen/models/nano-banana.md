# Nano Banana Family (Google Imagen)

Covers: nano (Nano Banana), nano2 (Nano Banana 2), nano2.1 (Nano Banana 2.1), nano-pro (Nano Banana Pro),
nano-lite (Nano Banana 2 Lite), and recraft (Recraft V4.1), which SKILL.md routes here too.

- **nano-lite**: the cheapest Pro tier. `--extra '{"thinking": "MINIMAL"}'` for speed; HIGH is the default.
- **recraft**: a different model (design, logos, icons). `--extra '{"model_type": "vector"}'` for vector
  output (also standard, utility, utility_vector); 1k or 2k. The advice below is written for the
  Nano Banana models.

## Prompt Style

Natural language. Write narrative descriptions, not keyword lists. These are thinking models that understand intent, physics, and composition. Treat them like collaborators, not search bars.

## Optimal Length

30-80 words. Descriptive prose, not comma-separated tags.

## Key Techniques

- **No keyword stuffing.** "4k, high quality, trending on artstation" actively hurts results. Replace with specific descriptions of what you want.
- **Be descriptive about details.** Instead of "add text" say "Write the text 'HELLO WORLD' in a bold, red, serif font on the sign."
- **Text rendering:** Enclose exact strings in double quotes. Keep text short (a few words). Nano Banana Pro is the strongest for text.
- **Editing:** When editing an existing image, focus on what is changing and what stays the same. Don't re-describe the whole image.
- **Material and texture:** Specify physical materials ("brushed aluminum", "matte ceramic", "worn leather") for realistic surfaces.

## What NOT to Do

- Don't use legacy prompt engineering syntax (parentheses for emphasis, colons for weights)
- Don't stack quality keywords
- Don't write overly long prompts (150+ words) as the model starts averaging conflicting instructions

## Model Differences

- **nano**: Budget-friendly, realistic. Good for quick iterations.
- **nano2**: Fast, high-quality. Best general-purpose default. Strong photorealism.
- **nano2.1**: The newest Nano. `thinking_level` minimal, medium (the default) or high, through `--extra '{"thinking_level": "high"}'`; it does not move the price. It can ground a picture in a web or image search (`--extra '{"enable_web_search": true}'`, or `enable_image_search`). Its ratios include the strips 4:1, 1:4, 8:1 and 1:8, and its own default is auto. 1.5 credits at 1k, 2 at 2k, 3 at 4k. Sent straight to the CLI, 14 or more references are refused ("Unsupported validation rules") unless `--is_inpaint false` goes with them, because CLI 1.1.26 checks the inpaint rule against the settings sent.
- **nano-pro**: Ultimate quality. Best text rendering and diagram generation. Use for final output when quality matters most.

## Example Refinement

Bad: "a cat, 4k, ultra realistic, high detail, award winning, trending"
Good: "A tabby cat curled on a sun-warmed windowsill, late afternoon light casting long shadows across wooden floorboards. Dust motes visible in the golden beam. Shot with shallow depth of field."
