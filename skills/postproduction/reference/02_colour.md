# Colour: how a file declares itself, and who does the grading

The grading is the `colorgrade` skill's job and this document does not repeat a
line of it. Read `skills/colorgrade/SKILL.md` and its `reference/01_method.md`.
What lives here is the part that belongs to DELIVERY rather than to the look:
how a file says what its numbers mean, and what goes wrong when it does not.

## Four declarations, and they are separate

A delivery declares four things and a file can get any one of them wrong on its
own:

| Field | ffprobe name | Rec.709 HD |
|---|---|---|
| primaries | color_primaries | bt709 |
| transfer | color_transfer | bt709 |
| matrix | color_space | bt709 |
| range | color_range | tv (limited) |

The display side of the Rec.709 transfer is BT.1886. HDR10 is bt2020 primaries,
smpte2084 transfer, bt2020nc matrix, plus MaxCLL and MaxFALL as separate
metadata; HLG is arib-std-b67.

## An untagged file is a delivery fault, not a neutral state

The player decides, and it will not always decide the way the colourist did.
ProRes written from a PNG sequence with no `-colorspace` comes out untagged; if
you then splice it into a tagged film with `-c copy`, the master changes its
colour metadata partway through and every frame hash still matches. That is
failure 2 in the failure log, and it is the reason `prove.py tags` exists.

    python prove.py tags MASTER.mov

Three rules that came out of it:

1. Encode every spliced segment with the tag the film carries, MATCHED to the
   file being joined, never assumed.
2. Compare the segment's tag with the film's BEFORE concatenating, and the
   assembled master's after.
3. Walk the whole delivered length, probing either side of every join.

## Range is the one that looks like a grade note

Full range read as limited crushes the blacks and clips the whites, and the
symptom looks exactly like somebody pushed contrast. Check the tag before
touching a wheel.

## Two decode paths are not comparable

Hashing a seeked frame against the same file's own framemd5 does not work: the
two paths choose different pixel formats. Two decoders disagree by 1.5 to 2.3
code levels purely from YUV to RGB conversion. Every measurement in this skill
carries its decode path for that reason, and `_common.require_same_path` refuses
to compare across paths.

## A re-encode has a floor, and the floor has a shape

Read every difference against the frame's own generation floor rather than
against zero. ProRes quantises in slices 16 rows tall and picks one quantiser
per strip, so new ink anywhere in a strip re-quantises the rest of that strip
and nothing outside it. Mask the SLICE ROWS, not the bounding rectangle, and the
floor often falls to exactly zero, which is a stronger statement than any
tolerance. `prove.py floor` measures it.

## Bit depth: the tag is a claim

Content that began at 8 bit and was promoted into a 10 bit container is still 8
bit content and every tag says 10. `spec.py depth` measures it two ways and
reports both, because one of them dies under a lossy codec:

- DISTINCT CODES. Promotion is injective, so a LOSSLESS 10 bit file made from 8
  bit content still carries at most 256 distinct values. Decisive when it holds.
- LATTICE FRACTION. Promoting 8 to 10 multiplies by 4, so every sample lands on
  a multiple of 4. A lossy re-encode moves samples off that lattice but not far.
  Calibrated on this machine, 2026-08-23: an 8 bit clip promoted and written as
  ProRes HQ kept 95.2 per cent of its luma on the multiple of 4 lattice, while
  native 10 bit ProRes sat at 31.5 per cent against a 25 per cent chance level.

The samples have to be read in the file's OWN pixel format. Asking ffmpeg for a
wider one runs the scaler, which range converts and dithers: the same 10 bit
file then reports 27946 distinct codes instead of 805. Measured, not assumed.

## ACES and OCIO

ACES 2.0 support landed in OpenColorIO 2.4.2, and OCIO 2.5 ships built in ACES
2.0 Studio and CG configs needing no external LUT files. [VERIFY] the version
numbers at opencolorio.readthedocs.io before quoting them; this line has not
been read in the primary source.

An ACES pipeline is a decision about the whole job, taken at ingest, not a
switch thrown at the grade. If the deliverable is Rec.709 and the sources are
Rec.709, adding ACES adds two transforms and no information.

## Where the grading actually happens

`colorgrade` track one for the whole picture, track two for one object via a
DCTL, track four for landing a new film where its approved siblings landed. That
last one matters more than it sounds: matching a series is not the same thing as
giving the new film the siblings' look values.
