# The look library: what each parameter does, and what a film look actually is

## The chain, in order

Every look is evaluated per pixel in this sequence. The spaces are chosen so
each operation behaves the way a colourist expects it to.

1. **Tone, in Cineon log.** `contrast` is a slope about `pivot`, which sits at
   0.4573, where 18 percent grey lands in Cineon. `toe` softens the shadows,
   `shoulder` rolls the highlights. Contrast in log space keeps the mid tones
   where they were and moves the ends, which is what film does. Contrast in
   linear crushes and blows.
2. **Crosstalk, in linear.** A symmetric 3 by 3 matrix that bleeds a little of
   each channel into the others, white preserving. This is the single most
   film like operation available. Real dye layers are not perfectly separated
   and the result is that extremes desaturate rather than clip into a pure
   primary.
3. **Split tone, in linear, weighted by a log luma ramp.** `shadow_tint` and
   `highlight_tint` are RGB offsets. `tint_falloff` governs how quickly one
   hands over to the other.
4. **Saturation, luma preserving.** `saturation` is global. `sat_shadow` and
   `sat_highlight` ramp it away at the ends. Film desaturates its shadows
   strongly, and setting `sat_shadow` around 0.7 is most of what makes a digital
   image stop looking digital.
5. **Targeted hue work, in Lab.** Each entry in `hue_shifts` is a centre, a
   width, a rotation in degrees and a chroma multiplier, applied with a raised
   cosine weight so there are no edges. `protect_skin` scales all of it down
   inside the skin band.
6. **Gamut compression.** See method notes. Off unless the look pushes chroma.
7. **Display encode**, with `black_offset` lifting the very bottom and
   `highlight_rolloff` softening the very top.

## Skin, and why it is protected by default

Skin sits on one line on the vectorscope. Keith Jack's Video Demystified gives
phase angles of 116 to 126 degrees for skin tone correction, with 123 degrees,
the plus I axis, used because it simplifies the processing. Different software
disagrees by around 10 degrees.

The biology is the reason it holds across every skin colour: the hue is the
blood under the skin, and melanin mostly moves brightness and saturation rather
than hue. So a single hue band is a legitimate gate for all faces, and it is the
brightness and saturation that vary.

This matters because brand colours regularly sit next to skin, yellow and orange
especially, and any hue vs hue move that shifts a brand colour will drag a face
with it unless something stops it. `protect_skin` at 1.0 stops it completely.

The grader also measures the skin hue angle per shot and reports the spread
across the piece, because skin drifting between shots is the single most visible
matching failure there is.

## kodak2383

Print film emulation, built from the characteristics Kodak themselves publish
for VISION Color Print Film 2383, not from copying somebody's LUT.

What their data sheet actually says, and how each item maps to a parameter:

- Higher D max in the upper scale, giving improved black on projection. Maps to
  contrast 1.18 with a toe of 0.22, so the bottom gets dense rather than lifted.
- Toe areas of the sensitometric curves producing more neutral highlights. Maps
  to a long shoulder at 0.55 plus a highlight rolloff of 0.55, so bright areas
  transition instead of clipping.
- Absorber dyes controlling intragrain scatter. Maps to crosstalk at 0.035, the
  channel bleed that keeps saturated colour from going electric.

On top of that, warm mids and cool shadows via the split tone, shadow saturation
pulled to 0.72 because film shadows are not colourful, and two small hue moves
that pull cyan slightly and desaturate green, which is what stops foliage and
skies from looking video.

## The rest, in one line each

`clean_commercial` is the honest default for brand work: a little contrast, a
long rolloff, saturation up ten percent, nothing stylised. Use it when the brand
colour has to stay literal.

`teal_orange` is the commercial standard, and the only reason it is safe here is
that skin is explicitly protected, so the complementary split never drags a face.

`bleach_bypass` emulates skipping the bleach so silver stays with the dye:
contrast up hard, saturation down hard.

`nordic` is low chroma with a blue bias in the low end and a lifted black, so
shadows read as fog rather than ink.

`warm_doc` is the smallest useful move: the room should still look like the room.

`vintage_70s` emulates a faded release print, where dye fade lifts the black and
pulls the neutral axis toward yellow green.

`noir` is near monochrome with the chroma almost out and contrast at 1.55. It is
the one look that cannot be baked to a 65 cube inside the 1.0 dE budget, and the
grader says so when it happens.

`sunlit` is a warm gain on the top end with a long shoulder, so skies bloom
rather than clip.

## Editing a look

The json files in `looks/` are the whole definition. Copy one, change numbers,
pass the path to `--look`. Check what a change did before trusting it:

```bash
$PY scripts/selftest.py     # prints the bake error for every look at 17, 33, 65
```

A look whose 65 cube exceeds about 1.0 dE is bending the transfer harder than a
cube can follow. That is a signal to soften the contrast or turn on gamut
compression, not a signal to ship it.
