# Hailuo (Minimax)

Covers: `hailuo` (`minimax_hailuo`)

Not the same model as `h3` / `hailuo3` (MiniMax H3), which is a newer, structured-field model with
its own guide (`minimax-h3.md`). This one wants narrative prose. Do not mix the two styles.

---

## Hard specs **[live 2026-08-07]**

| | |
|---|---|
| Duration | **6 or 10 s only** (preset, not a slider) |
| Variants | `minimax`, `minimax-fast`, `minimax-2.3` (default), `minimax-2.3-fast` |
| Resolution | 512 / 768 / 1080 on paper, **768 in practice, see below** |
| Aspect ratio | **no `aspect_ratio` param at all** |
| Keyframes | `start_image`, `end_image`, variant dependent |
| Cost | **6 credits for 6 s** on `minimax-2.3`, 4 on `minimax-2.3-fast` |

At roughly 1 credit a second this is one of the cheapest video models on the route, which makes it a
genuinely good place to block out a shot before committing to `h3`, `seedance2.5` or `flux-video`.

### Two traps, both fixed in the wrapper on 2026-08-07

**1. The variant has to go on the wire.** `model get` prints `minimax-2.3` as the default, but the
API does not apply it. Every prompt-only call was rejected with "start_image or end_image is
required unless variant is 'minimax-2.3'". The wrapper now sends `--variant minimax-2.3`
explicitly. If you call the CLI by hand, you must pass it too.

**2. The flag is `--variant`, not `--model`.** This guide and the wrapper previously documented
`model`. `--model` is rejected outright with "Unknown params: model". Same correction applies to
`veo3` and `veo3.1`.

Also fixed: the wrapper used to send `--aspect_ratio` to this model, which has no such parameter, so
**every cost check failed** with "Unknown params: aspect_ratio". Framing comes from the start image.

### Resolution is unreachable, and that is upstream

`resolution` accepts `512`, `768` or `1080`, and **no spelling of it works through the CLI**.
Bare digits are sent as a number and rejected with "resolution should be string, got number"; a
quoted value is passed through with its quotes and rejected with "allowed: 512,768,1080". The model
is pinned to its 768 default until Higgsfield fixes the CLI. Verified across four forms.

### Variant rules the API enforces **[live]**

- `start_image` or `end_image` is **required** unless the variant is `minimax-2.3`.
- `end_image` is **not supported** on `minimax-2.3` or `minimax-2.3-fast`.
- Resolution 512 is incompatible with `end_image`, and unsupported on either 2.3 variant.
- Resolution 1080 is not available at 10 s.

Net effect: `minimax-2.3` is the only text-to-video variant, and it cannot do keyframed endings. For
a last-frame transition you need an older variant plus a start image, at 6 credits for 6 s.

---

## Prompt Style

Narrative-driven. Hailuo excels when prompts read like short screenplay directions. Describe the moment as a story beat, not a technical specification.

## Optimal Length

40-100 words. Narrative clarity over technical density.

## Key Techniques

- **Shot-based workflow.** Think in shots, not scenes. Each generation is one continuous shot: "Close-up of hands kneading bread dough on a flour-dusted wooden counter. Camera static, natural overhead kitchen light."
- **Narrative framing.** Hailuo responds well to story context: "A detective steps into a dimly lit room and pauses, scanning the scene." This produces more intentional character movement than purely technical direction.
- **Image-first for consistency.** When character consistency matters, provide a reference image and describe only the changes: "Same character, now in an outdoor market, reaching for an apple from a stall."
- **Simple camera, strong subject.** Hailuo handles subject motion better than complex camera work. Prefer "locked-off medium shot, subject crosses frame left to right" over elaborate multi-axis camera moves.
- **Environment as character.** Describe the space with sensory detail: "a cramped bookshop, shelves floor to ceiling, warm lamp on the counter, dust in the air." Environment detail improves scene coherence.

## What NOT to Do

- Don't describe complex multi-person choreography (stick to 1-2 subjects)
- Don't request rapid camera movement (slow and static shots are strongest)
- Don't use quality boosters ("8k", "masterpiece")
- Don't write abstract or metaphorical prompts (be literal about what is visible)

## Example Refinement

Bad: "person in a bookshop, atmospheric, cinematic vibes"
Good: "A woman in her 50s stands in a narrow bookshop aisle, running her finger along the spines of old hardcovers. She pulls one out and opens it, dust motes rising into a shaft of warm light from a high window. Camera locked off at eye level, medium shot. Quiet, contemplative. Warm amber tones."
