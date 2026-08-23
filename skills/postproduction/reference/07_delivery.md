# Delivery: masters, packages, versions and proof

## The delivery document wins

Everything below is scaffolding. What a delivery requires is set by the client's
own delivery document, and that document beats every default in this skill.
Never infer a delivery spec from the codec of the file you were sent.

## Working master currencies

| Codec | Where it lives | Notes |
|---|---|---|
| ProRes 422 HQ | The general online master | All intra, so any frame is a cut point |
| ProRes 4444 | With alpha, or for graphics heavy work | 12 bit, alpha optional |
| DNxHR | The Avid side | HQX for 10 bit |
| H.264 / HEVC | Viewing copies and web | Long GOP, so seeking is not frame exact |
| MXF OP1a | Broadcast packages | AS-11 UK DPP has used it since 1 October 2014 |
| IMF | SMPTE ST 2067 | Application 2E is the common one; Application DPP is RDD 59 |

All of the IMF and AS-11 lines above are [VERIFY]: they have not been read in
the primary specifications, and they change.

## An all-intra master is a resource, not just an output

ProRes has no inter frame prediction, so a delivered master can be cut on ANY
frame with `-c copy` and every kept frame stays bit identical. Replacing two
shots in a 1162 frame film cost 164 rendered frames and 54 minutes instead of a
full re-render, and the untouched frames were PROVED identical by hashing them
either side of each splice boundary rather than assumed.

Cut the new spans on the SHOT boundaries, not on the old render's fixed frame
grid, or the grid pulls in neighbouring shots whose intermediates are gone.

## Splicing, and the timeline it leaves behind

A spliced master's own timeline is broken at its joins. See failure 6 for the
measurements. In practice:

- Seek by the file's OWN packet timestamps: `prove.py seek FILE --frame N`.
- Prove the cut by packet SIZES, not by frame count: `prove.py packets --compare`.
- Rewrite the output's timeline onto a clean grid so the next version can cut it
  with plain arithmetic.
- Tag every segment before concatenating, and walk the tags after.

## The three proofs a revision owes

1. **The changed frame prediction.** Derive it from the LAYER files, never type
   it. `prove.py predict`, then `prove.py expect`.
2. **The null version encode.** For an additive change, build every changed
   segment twice, once with the new thing off, and require sha equality with the
   delivered segment. It proves the decode, the transform and the encoder all
   still reproduce the delivered film.
3. **The smoke test over a span you are NOT touching.** It is the only thing
   that finds an element that was built, written up as delivered, and never
   reached the compositor.

## Viewing copies

A viewing copy keeps the SAME frame size and the SAME bit depth as the master
and is only compressed. A downscaled preview is a different film, and it puts a
fault, or a fix, in front of somebody in a form that cannot show it.

On a 10 bit source: hevc_videotoolbox, main10, p010le, high bitrate, tagged
hvc1. Measured 22 MB at 61.65 dB against the lossless master, better than ProRes
4444 at sixty times the size, and it plays natively. Prove the bit depth on the
delivered file, `spec.py depth`, rather than trusting the pixel format tag.

Label it as a viewing copy in its filename. Nobody has ever regretted that.

## The delivery list

`deliver.py list` prints the catalogue; `deliver.py check --type ...` holds a
package against it. The items that go missing most often, in order: the
TEXTLESS, the hash ledger, the conform notes, and the changed frame proof on a
revision.

The textless is the one whose absence surfaces months later, when a territory
version is wanted and the type is baked in.

## Naming

A naming convention is a project fact. Agree it before the first delivery, not
after the third. Whatever it is, it should carry: the film, the version, the
raster, the rate, and whether the file is a master, a textless or a viewing
copy. A name that claims a raster the file does not have is worse than a name
that claims nothing (failure 1).

## The paperwork that makes a rollback possible

- A hash ledger of every delivered file. `prove.py sha --ledger`.
- A spec sheet from the file itself, not from the intention. `spec.py probe`.
- Conform notes: rate, drop frame, start timecode, handles.
- The build's own segment files kept on disk after delivery, because the null
  version proof needs them.

## Archive and deletion

Three gates, in order, enforced by `archive.py sweep`: verify the survivors
against their record; hash the condemned before deleting them; prove each
restore path exists. Plus the two traps from failure 15: a version number in a
path is a LABEL, and symlinks must be resolved before any dependency graph is
believed.

Archive BEFORE you overwrite: `archive.py stage`. Break hard links before the
first write in a new version: `archive.py unlink`.

## What this skill will not do

It never marks a delivery approved. Approval is the client's.

It never sends. A file is delivered only when the send returns success for that
exact path, and building the deliverable and delivering it are two separate acts.

It never downscales a deliverable, and never silently changes resolution, bit
depth, frame rate or colour tags.

It never deletes a master until the survivors are verified and the condemned are
hashed.

It never reports done from notes, only from the artifact.
