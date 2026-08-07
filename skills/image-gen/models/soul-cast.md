# Soul Cast (Higgsfield)

Covers: `soul-cast` (Soul Cast)

---

## This is an image model, not a video model **[live 2026-08-07]**

The live catalog reports `soul_cast` as **type `image`**. It has no `duration`, no `start_image` and
no video parameters at all. Its full parameter list is `aspect_ratio` (required, and **16:9 is the
only permitted value**), `budget` (integer, default 50) and `prompt`.

Until 2026-08-07 the wrapper carried `soul-cast` in the **video** alias table, so a generation would
have written its stills into a file named `.mp4`. It now resolves as an image model. Call it as one:

```bash
python skills/image-gen/scripts/generate.py "..." -m soul-cast -a 16:9 -o /tmp/cast.png
```

**Cost [live]:** 0.12 credits, and the quote does not move with `budget` (10, 50 and 100 all quote
the same). It is effectively free to run, which fits a casting-sheet tool rather than a renderer.

The prompt advice below was written for a video model and its **identity and wardrobe technique
still applies to the stills**, but ignore anything about motion, performance beats or facial
animation until someone re-tests what this model actually returns. Flagged rather than deleted so
the useful half is not lost.

---

## Prompt Style

Character-driven casting direction. Soul Cast specializes in character consistency. Lead with
identity, follow with scene.

## Optimal Length

40-80 words. Identity description is the priority.

## Key Techniques

- **Identity lock.** Define the character with fixed traits before describing the scene: "A tall woman with cropped silver hair, sharp cheekbones, wearing a black turtleneck." These traits persist across generations.
- **Simple motion.** Soul Cast handles subtle, human-scale movement best: "she looks down at the letter in her hands", "he leans back in his chair and sighs." Avoid athletic or fast-paced action.
- **Emotional direction.** Name the emotion in the performance: "expression shifts from curiosity to concern", "a slow, tired smile." The model uses this to drive facial animation.
- **Consistent wardrobe.** Always describe clothing. If generating a sequence, keep the same outfit description across prompts.
- **Scene as backdrop.** Keep environments simple. The model prioritizes character rendering: "in a quiet office", "at a park bench", "standing in a doorway."

## What NOT to Do

- Don't change character traits between related generations
- Don't describe complex group scenes (1-2 characters max)
- Don't expect elaborate environment rendering (character is the strength)
- Don't omit the character description assuming it carries over from previous generations

## Example Refinement

Bad: "person talking in a room"
Good: "A man in his late 20s, dark curly hair, stubble, wearing a gray henley shirt, sits across a cafe table. He looks up from his coffee, expression shifting from distraction to recognition. Warm ambient light, shallow depth of field. Camera static, medium close-up."
