# Marketing Studio Video (Higgsfield)

Covers: `marketing` (Marketing Studio Video)

---

## Hard specs **[live 2026-08-07]**

| | |
|---|---|
| Duration | 15 s default, **5 s and up** (see the caution below) |
| Aspect | **9:16 by default**, plus 21:9, 16:9, 4:3, 1:1, 3:4, auto |
| Resolution | 480p, 720p (default), 1080p |
| Audio | `generate_audio`, **off by default** |
| Modes | `ugc`, `ugc_how_to`, `ugc_unboxing`, `product_showcase`, `product_review`, `tv_spot`, `wild_card`, `ugc_virtual_try_on`, `virtual_try_on` |
| Cost | **5.0 credits/s** at 720p, 10 credits/s at 1080p |

Measured: 75 credits for the 15 s default, 150 for 30 s, 300 for 60 s at 720p. Linear.

**This is the longest-form model on the route** and the only one that reaches a full 60 second spot
in one pass, which is why it is priced like the cinematic tier rather than the drafting tier.

It is also the only video model that **defaults to 9:16**, not 16:9, which follows from its UGC
purpose. Set the aspect explicitly if you want landscape.

**Caution: the cost endpoint does not bound the duration.** Quoting 300 seconds returns a price
(1500 credits) instead of an error, unlike most models here, which reject out-of-range values
outright. The same is true of `cinematic3` and `cinematic3.5`. A successful quote from this model
proves the arithmetic, not that generation will accept the length.

Beyond the prompt, this model takes structured commercial inputs the wrapper does not expose:
`product_ids`, `web_product_ids`, `avatar_ids`, `hook_id`, `setting_id`, `storyboard_id` and
`ad_reference_id`. Two rules the API enforces: `ad_reference_id` cannot be combined with `hook_id`
or `setting_id`, and `product_ids` and `web_product_ids` cannot both be set. Reach those through the
`higgsfield marketing-studio` command directly.

---

## Prompt Style

Product and brand-focused. This model is optimized for commercial and marketing video content. Describe the product, the setting, and the desired commercial feel.

## Optimal Length

40-80 words. Clear product focus with commercial context.

## Key Techniques

- **Product as hero.** Lead with the product and its key visual features: "A matte black wireless speaker with a brushed aluminum top, sitting on a white marble surface." The product is always the subject.
- **Commercial lighting.** Use studio and product photography lighting terms: "soft rim light separating product from background", "gradient backdrop from dark to light", "key light at 45 degrees, fill from below."
- **Motion for reveal.** Describe motion that showcases the product: "slow 360-degree rotation on a turntable", "camera orbits the bottle, catching light on the glass", "lid opens to reveal the interior."
- **Brand-safe environment.** Keep backgrounds clean and intentional: "white infinity cove", "lifestyle kitchen counter", "outdoor table at golden hour." Avoid clutter.
- **Text and logo placement.** If including text, specify content, position, and style: 'Text "PURE SOUND" fades in at bottom center, clean sans-serif white font.'

## What NOT to Do

- Don't describe busy backgrounds that compete with the product
- Don't include multiple products without establishing hierarchy
- Don't use artistic or abstract direction (this is commercial, not art house)
- Don't forget to describe the product's physical materials and finish

## Example Refinement

Bad: "product video of headphones, professional, 4k"
Good: "Close-up of matte black over-ear headphones on a dark slate surface. Slow push-in, soft rim light from behind separating the cups from the background. Memory foam ear cushion texture visible. Camera circles 45 degrees, catching the brushed metal hinge detail. Minimal, premium, dark gradient backdrop."
