# Ideogram 4.5

Covers: ideogram (Ideogram 4.5)

Ideogram's reason to exist is **text inside the image**: signage, posters, packaging, logos, book covers, anything where letters must be spelled right and set with intent. For plain photography most other models here are stronger. Reach for Ideogram when words are the subject.

## Prompt Style

Two layers in one prompt. First the scene, then the exact text in quotes, then the typographic treatment.

- Put the literal text in straight quotes: `a poster that reads "NIGHT SHIFT"`. Only quoted text is treated as text to render; everything else is scene.
- Keep the rendered text short. One to five words sets cleanly. A full paragraph will degrade into lettering-shaped noise.
- Describe the type the way a designer would: weight, case, serif or sans, spacing, where it sits, how it relates to the art. "Bold condensed sans, all caps, centred, slight letterpress emboss."

## Optimal Length

30 to 70 words. Spend them on the text content, the type treatment, and the layout. Ideogram holds composition better than it holds a long atmosphere passage.

## Key Techniques

- **Quote the words, name the font feeling.** The quoted string is what gets spelled; the words around it steer the typeface. "Serif, high contrast, 1920s theatre bill" lands a very different face than "rounded geometric sans, modern tech".
- **magic_prompt.** The model can rewrite your prompt into a richer one before generating. `auto` lets it decide, `on` forces the expansion, `off` renders your words as written. Use `off` when you have already written a precise brief and want no surprises; use `on` for a one-line idea you want fleshed out. Default is `auto`.
- **Layout language.** It understands "centred", "top third", "lower left", "two-column", "circular badge". State where the text sits rather than hoping.
- **Ratios for the medium.** The enum runs from square to extreme banners (3:1, 1:3, 23:9, 9:23). Match the ratio to the artefact: 9:16 for a story, 3:1 for a web banner, 1:1 for a logo tile. The banner ratios render at 2k only (see Controls).
- **Negate in the sentence.** No negative-prompt field. Write the positive: "plain background, no extra text, no watermark" as part of the brief.

## Controls

- **quality**: low, medium, high. High is the default and the right choice for finished text; lower tiers trade crispness of lettering for credits.
- **very_low quality is image-to-image only.** The live validator refuses it unless you attach a source image, so it is not offered on a plain text-to-image card. Use it only with a reference; it costs 0.25 credits.
- **Resolution 1k or 2k; the model's own default is 2k.** 1k takes the fourteen ordinary ratios: auto, 1:1, 1:2, 2:1, 2:3, 3:2, 4:5, 5:4, 9:16, 16:9, 5:8, 8:5, 3:4 and 4:3. The ten banner ratios (3:1, 1:3, 23:9, 9:23, 22:9, 9:22, 12:5, 5:12, 8:3, 3:8) need 2k. Only a mask lifts that; a source image does not. Both sizes cost the same: 1 credit at low, 2 at medium, 6.25 at high.
- **Send a ratio with 1k.** The live rule is checked against the settings sent, so `--resolution 1k` with no `-a` is refused ("This aspect ratio requires 2K resolution without a mask"), even though the model's own ratio is auto. Pass `-a` with 1k, or leave the resolution out and get 2k at the same price.
- **References.** The tool's `-r` attaches one reference image, for style or subject carry-over. Higgsfield sets no count limit of its own on this model (twenty references quoted without complaint on 2026-10-14); only a mask limits them, to one to four.

## What NOT to Do

- Don't ask for long bodies of text. Headlines and labels, not paragraphs.
- Don't fight a misspelling by repeating the word. Shorten it, raise quality, or switch the ratio so the letters have room.
- Don't use quality boosters ("8k", "masterpiece"). They add nothing and crowd out the brief.
- Don't expect photoreal skin and light to match a dedicated photo model. Ideogram's edge is type and graphic design.

## Example Refinement

Bad: "a cool logo for a coffee shop, 4k, amazing"

Good: `Square logo tile for a coffee roaster. Centred text reads "EMBER & OAK" in a bold condensed serif, all caps, tight letter spacing, warm cream on deep espresso brown. A small single-line flame mark above the text. Flat, clean, generous margins, no extra text. 1:1, high quality.`
