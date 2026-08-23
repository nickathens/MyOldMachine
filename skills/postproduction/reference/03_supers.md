# Supers: type on picture

Two jobs. Place it by deriving the geometry from the font, and prove it is
readable against the ground it actually sits on.

## Safe areas, and why they are not enough

EBU R95 revision 1, July 2016, read in the primary text on 2026-08-23. Two
areas, concentric:

- ACTION SAFE: inset 3.5 per cent of picture width and height per edge, so 93
  per cent of the raster. On 1920x1080 that is 1786x1004; the document's own
  errata confirms 3.5 per cent of 1920 is 67 pixels.
- GRAPHICS or TITLE SAFE: inset 5 per cent per edge, so 90 per cent. On
  1920x1080 that is 1728x972. On 3840x2160, 3456x1944.

R95 also carries per raster FIRST and LAST safe line and pixel numbers for 576i,
720p, 1080i, 1080p, 2160p and 4320p, and those live in the figures as images.
For a broadcast deliverable, read them out of the PDF rather than trusting the
percentage rounding.

    python supers.py safe --raster 3840x2160 --box 2100,1800,1500,200

Safe area is necessary and nowhere near sufficient. A super entirely inside
title safe can be completely unreadable, and that is what clients notice.

## Derive the family, do not copy it

A supers family is two edges and two steps: an anchor for the last line's em box
bottom, an anchor for the widest line's ink right edge, a size and a pitch.
Written down as pixel numbers it describes one job in one typeface; derived from
the font's metrics it survives a change of face, of raster and of copy.

Definitions, because renderers disagree:

- **em_top** is the ASCENDER line. Pillow's default `la` anchor puts y here.
- **the em box** is the box of height `size` whose top is em_top, so the house
  rule "the last line's em box bottom sits on Y" means em_top(last) + size = Y.
- **the baseline** is em_top plus the ascender scaled to size, which is where
  Pillow's `ls` anchor wants y.
- **ink** is the real glyph extent from the font's own per glyph boxes, not the
  advance width. On flush right anchoring that difference is visible.

`supers.py metrics` reads units per em, ascender, descender, cap height, x
height, the advance of a string and its ink box, from the font file, with no
dependency beyond the standard library. It reads TrueType outlines from .ttf,
.ttc and .otf; CFF outlines carry no glyf table so per glyph ink boxes are not
available there, and it says so rather than guessing.

**Kerning is NOT applied.** GPOS is not read, so advance widths are unkerned. At
display sizes kerning moves a line by a pixel or two: check one real render
against the plan before the family is locked.

    python supers.py metrics --font FACE.ttf --size 102 --text "MAKE IT COUNT"
    python supers.py plan SUPERS.json

Derive it, then CHECK the derivation against blocks already delivered and
approved before using it anywhere.

## Legibility: measure the ink against its actual ground, in Lab

This is the measurement the whole department exists for.

**What hides a glyph is ground the same COLOUR as the ink, not ground the same
brightness.** A luma only check cannot choose between two inks. On a pale ground:

| Ink | WCAG contrast ratio | dE2000 |
|---|---|---|
| white | 1.27 | 6.8 |
| a brand turquoise | 1.29 | 26.2 |

Indistinguishable by luma, four times apart in Lab, and the Lab number is the
one that agrees with the eye. Reproduce it in one line:

    python supers.py contrast --ink "#FFFFFF" --ground "#E8E4DC"

On a real delivered master, measured in Lab, white ink lost 17 to 98 per cent of
its glyph surround on five of eight blocks while the turquoise never dropped
below dE 39.

The real measurement takes a rendered frame, renders the block's glyph mask at
1:1, takes a ring of pixels just outside the glyph edges, and reports the
FRACTION of that surround within dE 20 of the ink. A block does not fail all
over: it fails where one bright object sits behind two words.

    ~/.venvs/post/bin/python supers.py audit FRAME.png --spec SUPERS.json

Run it on the GRADED picture. A grade moves the ground, and the ground is half
of this measurement. Run it at 1:1, or the answer is about a resampled picture
and not about the film.

## The fix that keeps the look

A pale film breaks plain ink and a dark one does not, so the same family needs
different treatment across a campaign. The fix that does not change the design
is a soft black shadow scaled to the TYPE, not to the frame:

| Dose | offset | blur | alpha |
|---|---|---|---|
| hairline | 0.02 x size | 0.09 x size | 0.40 |
| soft | 0.04 x size | 0.14 x size | 0.70 |
| heavy | 0.06 x size | 0.18 x size | 0.85 |

Invisible on dark shots, load bearing on pale ones. `supers.py shadow --size N`
prints the pixel values. **Pick the dose by rendering three or four at 1:1 on the
WORST grounds in the film and looking.** A number picked from a table has never
been looked at on the shot it has to survive.

## Animation and holds

Blocks build one line at a time. Nothing clears until the scene does, and every
block should end on a cut rather than mid shot. Whether the film wants a rise, a
fade or a wipe is a design decision; where the block ENDS is an editorial one.

The animation itself is `remotion`'s job for anything with real motion design in
it, and `logo-animate` for a brand mark. This department owns the geometry, the
legibility and the timing against the cut.

## Reading a super back off the picture

`ocr` will read burned in text off a frame, which is the cheapest way to catch a
wrong caption, a typo in a generated mock, or a super that did not update
between versions. It is a check, not a source of truth: a low contrast super is
exactly the one OCR will fail on, and that failure is itself a signal.
