# Character & Identity Consistency

Cross-model workflow for building a character once and keeping them identical across every still and shot. Pulls from Nano Banana Pro, GPT Image, and Soul depending on the step (see routing below). This is the stills-side companion to `seedance.md`: lock the character here, then animate it there.

## The build order (Mode 0 to Mode 5)

The stills pipeline runs as six modes in a strict order. Each mode hands its output to the next, so a hero character moves from a bare locked face to a finished, dressed, in-scene still without the identity drifting:

- **Mode 0 — Face / identity lock.** Build the face on medium grey using the locked identity spec below. This is the only mode that invents the face; every later mode reuses it verbatim.
- **Mode 1 — Outfit.** Dress the locked face in a simple garment, identity wording unchanged.
- **Mode 2 — Six-panel sheet.** Generate the full character sheet in one render (below) to bank a reference set.
- **Mode 3 — Scene plate.** Drop the locked, dressed character into an environment.
- **Mode 4 — Detail / hero pass.** Route the hero face through GPT Image for maximum fidelity (see routing).
- **Mode 5 — Outfit swap.** Change wardrobe from reference images (below).

Run them in order. Jumping back to re-invent the face mid-pipeline (re-running Mode 0) is the most common way a character drifts between stills.

## Build on medium grey, never white seamless

Build and reference characters against a **medium grey** backdrop, not pure white. White seamless blows out the edges of the face, kicks bounce light up into the jaw, and bakes in a plastic sheen that then fights every scene you drop the character into. Medium grey gives the model a mid-value to anchor skin tone against, so the face reads with real weight and zero gloss. This one change is among the biggest "reads photographed vs reads generated" levers on the stills side.

## The Locked Identity Spec

For any recurring character or brand figure, write one fixed identity block and reuse it **verbatim** in every prompt. Eight fields, each with precise physical descriptors (no names, no mood words):

1. **Heritage & build** — ancestry read, age, height and frame
2. **Skin** — tone, undertone, texture, marks
3. **Hair** — color, length, texture, parting and styling
4. **Brows** — shape, thickness, color
5. **Eyes** — color, shape, set, lids
6. **Nose** — bridge, width, tip
7. **Lips** — fullness, shape, resting set
8. **Face structure** — jaw, cheekbones, chin, overall shape

Keep this block identical across generations. The moment you paraphrase it, the face drifts. Store the spec next to the character's reference images so any future session reuses the exact wording.

## Six-panel character sheet in one prompt

Ask for a full character sheet in a single generation: one prompt yields a six-panel layout — full body plus detail insets (face, hands and nails, jewelry, distinctive markings, profile). No Photoshop assembly. That gives you a consistent reference set from one render to feed downstream, including as Seedance references.

## Five universal render rules

Close most stills with five render rules that push "reads photographed" over "reads generated." State them whenever realism matters:
1. **Real skin.** Pores, fine texture, subsurface warmth; never wax or plastic.
2. **Hair physics.** Strands with weight and stray flyaways, not a molded helmet.
3. **Lens character.** A real lens signature: shallow focus with soft falloff, gentle halation, a touch of edge distortion. Behavior, not a brand name.
4. **Light physics.** Light that obeys a source: direction, falloff, soft shadow, bounce.
5. **Film grain.** A fine grain layer over the whole frame to bind it into one photographic image.

**The flattering-realism ceiling.** Realism with a cap: matte and textured, but never unflattering. No acne, no blemishes, no harsh enlarged pores. Skin that reads real, not skin that reads like a dermatology photo.

## The Cinema Stack closing block

For most stills, append one merged closing block (the "Cinema Stack") that bundles the five render rules, the lens and light behavior, and the grade into a single paragraph at the end of the prompt. It does for a still what the Camera Capture block does for a Seedance shot.

**Exception for grey plates.** On a medium-grey build (Mode 0 to Mode 2), do not append the full stack — its contrast would re-raise the very values grey is there to keep low. Use a LEAN Rembrandt grade instead: soft key at roughly 45 degrees, one gentle shadow side, lifted blacks, no heavy contrast. Save the full Cinema Stack for scene plates (Mode 3 onward), where the environment supplies the contrast.

## Night Cinema register

For dark scenes, do not let the model default to milky bright-night or wash everything teal. Run a hard-practical register: deep shadow with sharp practical sources doing the lighting, in the sensibility of films like Tokyo Drift, The Batman, and John Wick (the Lin / Wan / Fraser school of hard practical night). Two sub-modes:
- **Exterior / canyon.** Wide dark with hard pools of practical light (signage, headlights, windows) carving the subject out of black.
- **Interior / urban.** Tighter motivated practicals (lamps, screens, neon) as the key, deep falloff into shadow.

Either way: color comes from the practicals themselves, blacks stay rich, and nothing is lit that the scene cannot explain.

## Model routing

| Job | Route to | Why |
|---|---|---|
| Face / identity lock | **Nano Banana Pro** (default) | Best identity adherence per credit |
| Highest-fidelity hero face | **GPT Image** | Sharpest result, higher cost — reserve for hero stills |
| Fast iteration on a locked face | **Soul** | Cheap, quick variations once identity is set |
| Simple outfit | **Nano Banana Pro** | Handles plain garments cleanly |
| Complex / stylish outfit (translucent, baggy, layered) | **Soul** | Pushes silhouette and drape harder |

### Outfit swaps

To put a locked character in a new outfit, work from **screenshots / reference images, not description alone**. Upload the **outfit image first, then the character image second**, and **restate any identity markings in text** (scars, tattoos, jewelry). Image references alone tend to drop small markings, so name them explicitly and they survive the swap.

## Handoff to Seedance

Once the character is locked and you have the sheet, the detail panels become your `@imageN` references in `seedance.md`. Remember the Seedance rule that carries over here: name the character to Claude so it tracks continuity, but describe them physically in the prompt, never by name.
