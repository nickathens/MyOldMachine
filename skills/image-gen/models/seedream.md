# Seedream (ByteDance)

Covers: seedream (Seedream 4.5), seedream-lite (Seedream V5 Lite)

## Prompt Style

Natural language with optional negative prompts. Seedream understands conversational descriptions and supports explicit negative constraints. Write like you are describing a scene to a photographer.

## Optimal Length

30-100 words. Natural prose, not keyword lists.

## Key Techniques

- **Natural description.** Write complete sentences: "A ceramic mug filled with black coffee sits on a weathered wooden table next to an open paperback book." The model parses intent from grammar, not tags.
- **Text rendering.** Seedream handles text well. Enclose exact strings in double quotes and describe placement: 'A neon sign reading "OPEN LATE" above a dark storefront doorway.'
- **Negative prompts supported.** Seedream accepts negative prompts. Use them sparingly for specific exclusions: "no watermark", "no text overlay", "no extra fingers". Don't dump generic negatives.
- **Material and surface.** Specify physical materials for realism: "brushed steel handle", "cracked leather binding", "frosted glass panel". Material descriptions ground the image.
- **Composition cues.** Seedream responds to framing language: "close-up of", "wide establishing shot of", "overhead view of", "eye-level perspective".

## What NOT to Do

- Don't use legacy syntax (parentheses for emphasis, colons for weights)
- Don't stack quality keywords ("4k, ultra realistic, best quality")
- Don't write prompts over 150 words as the model averages conflicting instructions

## Model Differences

- **seedream**: Higher quality, more detail fidelity. Best for final output.
- **seedream-lite**: Faster, lower cost. Good for iteration and drafts.

## Example Refinement

Bad: "cat on table, hyper realistic, 8k resolution, masterpiece"
Good: "A black and white tuxedo cat sitting upright on a kitchen counter, one paw resting on a closed laptop. Morning light from a window to the left, casting soft shadows. Granite countertop, stainless steel appliances in the background. Shallow depth of field."
