# FLUX 2.0 (Black Forest Labs)

Covers: flux (FLUX 2.0), flux-kontext (Flux Kontext)

## Prompt Style

Clear, descriptive prose. Natural language. Word order matters: put the most important elements first.

## Optimal Length

30-80 words for most work. Up to 120 for complex scenes.

## Key Techniques

- **Priority ordering.** FLUX pays more attention to what comes first. Order: Main subject, key action, critical style, essential context, secondary details.
- **No negative prompts.** FLUX does not support negative prompts. Describe what you want, not what you don't want. Instead of "no blur" write "sharp focus" or "crisp detail".
- **Camera and film stocks.** "Shot on Fujifilm X-T5, 35mm f/1.4" produces more authentic results than "professional photo". Specify actual camera models and lenses.
- **Typography.** FLUX 2.0 Flex is the strongest for readable text. Enclose text in quotes. Specify font characteristics.
- **Hex color matching.** Supports hex codes for precise color control: "a #FF4500 orange button on a #1A1A1A dark background". Extremely useful for product and brand work.
- **Multi-reference.** Flux Kontext maintains character consistency, product styling, and brand identity across generations using reference images.
- **Aspect ratio.** Results improve when the aspect ratio matches the described scene. Landscapes in 16:9, portraits in 3:4, products in 1:1.

## What NOT to Do

- Don't use negative prompt syntax (it's ignored and wastes tokens)
- Don't put minor details before main subjects
- Don't use parentheses or brackets for emphasis (not supported)

## Model Selection

- **flux**: Precise prompt adherence. Best for complex scenes where every detail matters.
- **flux-kontext**: Context-aware. Best when working with reference images for consistency.

## Example Refinement

Bad: "beautiful sunset over mountains, 4k, ultra realistic"
Good: "Dramatic alpine sunset, jagged snow-capped peaks silhouetted against a deep amber and violet sky. Low clouds threading through the valleys. Shot on Hasselblad X2D 100C, 45mm lens. Golden hour, last light catching ice crystals on the ridge. 16:9 landscape composition."
