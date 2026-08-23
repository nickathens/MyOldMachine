# Postproduction

The whole back end of a film: ingest, repair, resolution, conform, compositing,
colour, supers, subtitles, sound, mastering, delivery and archive. One ordered
spine, a gate on every step, proof not description.

Deterministic engines that check themselves, dated standards with their sources
named, and a failure log of forty two faults that each reached a real film.

**Before using:** read `SCOPE.md`. It never approves, never sends, never
downscales, and never deletes a master until the survivors are verified.

---

## What this is, and what it refuses

Post is not one job. It is twelve jobs in a fixed order, and most of the damage
in a finishing pipeline comes from doing one of them out of turn, or from
starting the next one before the previous one was proved. This skill owns
METHOD, ORDER and PROOF. It writes an engine only where nothing existed, and it
does not reimplement `colorgrade`, `remotion` or Resolve.

It refuses, by design: marking a delivery approved (that is the client's, and it
arrives through a person); sending anything (a file is delivered only when the
send returns success for that exact path); reducing the frame size or bit depth
of a deliverable; silently changing a rate, a tag or a channel layout; deleting a
master before its survivors are verified and its own hash is recorded; and
reporting a state of the disk from notes rather than from the artifact.

Where a measurement cannot be made, it says UNPROVEN and names what is missing.
Unproven is not a pass.

## Two registers, one skill

Read which is wanted from the question.

**Client register.** Plain language, no paths, no command lines. What was wrong,
what was done, what to look at, what is still open. Answer first, evidence
second.

**Finishing register.** The measurement, the file it was taken on, the decode
path, the threshold and where the threshold came from, and what was NOT checked.
Written so a colourist or an online editor can check it line by line.

A casual register never lowers the standard of the measurement behind the
answer.

## The discipline we never relax: measurement

The analogue of citation discipline in the law and engineering skills, and load
bearing for the same reason. One wrong number that somebody trusted costs more
than ten gaps that somebody filled.

- **Never state a file's condition you did not measure ON THAT FILE.** Every
  number carries the file and the decode path it came through. Two arrays from
  different decode paths cannot be compared, and `_common.require_same_path`
  refuses to.
- **A hash proves the pixels and says nothing about how the file DECLARES them.**
  Container and stream metadata are part of the deliverable.
- **Notes are not the disk.** A note saying something was deleted, sent or
  rebuilt records an intention. Look at the artifact.
- **Read every difference against the frame's own generation floor**, never
  against zero. A check demanding zero where a re-encode cannot give zero fails
  a correct film.
- **A new check must pass on an already approved file before it is believed.** If
  it fails there, the check is wrong, not the film.
- **A changed frame list is a proof only when it was PREDICTED** from the layer
  files before the render, and the delivered file agrees with the prediction.
- **When a check cannot separate two candidates, say it bounds but cannot rank**
  and move the ranking to a measurement with real ground.
- **Nothing is sent until the send confirms.**

## The spec gate: ask before you render

Before anything renders, name the delivery facts the result depends on: frame
size; frame rate and whether it is native or conformed; scan; colour primaries,
transfer, matrix and range; codec and bit depth; chroma; audio channel layout
and the loudness target WITH its gate; safe area convention; aspect and any
crop; and the deliverable list including textless and sidecars. If any are
unknown, ask for the whole set in one compact list and stop.

    python $SKILL_DIR/scripts/spec.py gate --profile broadcast_hd_r128

What may default are the standards' own constants: the Rec.709 tags, the 93 and
90 per cent safe areas, the R128 target and the true peak ceiling. What may never
default is a project fact. **A standard's constant may default; a project's fact
may not.**

One escape hatch, and it is the user's to open: when they say to assume typical
values, run on documented typical inputs, list every assumption first, and stamp
the whole result INDICATIVE.

## The spine: twelve departments, each with a gate

`python $SKILL_DIR/scripts/route.py spine` prints it in full; `route.py find
"phrase"` routes a client's own words to the right department and names the
steps that come first.

1. **Ingest and spec.** Gate: the true raster, rate, tags, codec, depth, chroma,
   audio layout and duration, read off the FILE.
2. **Picture repair.** Before colour, always, because rebuilding frames
   afterwards rebuilds them from source and drops the colour repair on them.
   Gate: held out frames score the rebuild, and rate, count, duration and
   constant timing are verified on the delivered file.
3. **Resolution and restoration.** The department entered TWICE: restore at size
   HERE, before the cut and long before colour, because it changes every pixel
   the grade is built on; enlarge to the delivery raster at step 10, because
   enlarging early makes every department downstream pay for pixels nobody asked
   for. Gate: the source's EFFECTIVE resolution measured before anything is
   enlarged, the clock and the sound and the tags untouched, and the result
   proved TEMPORALLY, because a stills upscaler run down a clip re-invents its
   detail every picture and no still frame shows it.
4. **Conform.** Gate: frame count and duration agree with the edit, every join
   accounted for, timecode done in the delivery's own rate and drop frame
   convention.
5. **Compositing and inserts.** Gate: the plate's cadence measured BEFORE the
   track, the motion model chosen by cross validation rather than assumed, the
   track certified by a second route that shares no assumption with the first,
   every occluded edge declared UNMEASURED and any rigid fill proved by hold
   out, and every check on the result anchored in the PLATE, never in the model
   the build used.
6. **Colour.** Gate: dE2000 consistency before and after, and every correction
   that hit a cap declared.
7. **Supers and titles.** On the GRADED picture. Gate: safe area, AND the ink
   separated from its actual ground in CIE Lab, per block, at 1:1.
8. **Subtitles.** After supers, so a subtitle cannot land on one. Gate: reading
   speed, line rules, durations, gaps, and the collision check.
9. **Sound.** Gate: integrated loudness, true peak and range against the named
   profile, measured on the FINAL cut, because every trim moves the integrated
   number. Normalise last.
10. **Master.** Gate: the null version encode byte identical to the delivered
   predecessor, colour tags uniform across every join, and the changed frame
   list matching the prediction.
11. **Deliverables.** Gate: every item present and proved, sidecars named, the
    viewing copy labelled as one.
12. **Versions and archive.** Gate: survivors verified BEFORE anything is
    condemned, condemned hashed on the way out, every restore path proved.

## Engines

Standard library Python, offline, deterministic, `--json` on every command.
ffmpeg and ffprobe are the only hard requirement.

- **Ingest**: `spec.py probe FILM.mov` reads the true spec and flags what is
  wrong with it; `... claims FILE` holds the FILENAME's claims against the file,
  which on one job found a whole review set named for a resolution none of them
  had; `... depth FILE` measures the bit depth the picture actually CARRIES, by
  distinct code count and by lattice fraction, because a promoted 8 bit file
  declares 10 everywhere; `... check FILE --profile P` is the field by field
  comparison; `... gate` is the spec gate; `... profile --template` starts a new
  job's profile.
- **The verifier**: `prove.py` is the tool that was rewritten in job folder
  after job folder, and it is the reason this skill exists. `sha` and `verify`
  for ledgers; `frames` and `diff` for per frame hashes, cached on path, size
  and mtime; `predict` to derive the changed frame set from the LAYER files and
  `expect` to hold the delivered file against it (it FAILS on a constant offset
  rather than absorbing it); `timeline` to find a spliced master's joins;
  `seek --frame N` for the only correct way to reach a frame on one; `packets
  --compare` to prove a cut without decoding; `tags` to walk the colour metadata
  across every join; `floor` for the generation floor.
- **Supers**: `supers.py metrics` reads a font's own numbers with no
  dependencies; `plan` derives the family geometry from two anchors; `safe`
  checks against EBU R95; `contrast --ink --ground` is the demonstration that
  lands, and `audit FRAME --spec` is the real measurement of ink against its
  actual ground, per block, at 1:1.
- **Subtitles**: `subs.py` reads and writes SRT, WebVTT and TTML, checks reading
  speed and line rules and durations and gaps and frame boundaries, shifts,
  retimes across a rate change (and makes you say whether the picture was
  re-timed or re-labelled), finds collisions with the supers, and prints a burn
  in command with its geometry stated. Nothing in this toolkit could read a
  subtitle before it.
- **Sound**: `audio.py measure`, `check`, `normalise`, `layout`. It measures
  BS.1770 level gated loudness and refuses to pretend to be a dialogue gated
  meter.
- **Resolution**: `upres.py` owns the two questions the header cannot answer.
  `effres FILE` reads the picture's own spectrum and says whether it CARRIES the
  raster it claims, names the source raster an enlargement is consistent with,
  and catches pixel doubling by its exact replication lattice when the spectrum
  cannot; `route FILE --target WxH` measures and recommends, and lists what the
  route will NOT fix; `temporal SRC OUT` is the measurement nothing here could
  make before, because a stills upscaler run down a clip boils and no still
  frame shows it; `verify SRC OUT` is the whole gate, and it REFUSES the per
  frame comparisons outright when the clock differs rather than returning a
  plausible wrong answer; `superscale` states Resolve's Super Scale API with the
  enumerations read out of the manual inside the app.
- **Conform**: `conform.py tc` for timecode including drop frame, `rates`,
  `edl read` and `edl check`, `handles` with exclusive out points, `duration`.
- **Delivery precheck, the signature feature**: `deliver.py check --type tvc
  --have master,textless` lists what is missing, what is conditional and needs
  an explicit answer, and what a reviewer would strike. `deliver.py audit FILE
  --profile P` runs the picture spec, the colour tag walk and the loudness and
  returns one strike list.
- **Archive**: `archive.py stage` before you overwrite, `unlink` to break the
  hard links a `cp -al` version folder shares with the delivered one, `links` to
  see what a version really depends on, and `sweep` for the three gates before
  any deletion.
- **Compositing**: `comp.py` is the department where the most convincing wrong
  answers live, so every command ends by naming what it did NOT prove. `cadence`
  finds a conformed plate's own beat BEFORE anything is tracked; `track` solves
  reference to frame by ECC, chooses the motion model by cross validation on
  held out correspondences, MEASURES its own solve settings on this plate rather
  than trusting a published figure, and certifies the result by a second route
  that shares no assumption with the first; `quad` returns an ordered ring with
  a verdict per edge AND per corner, and tells a bite out of an edge from a
  missing corner; `aspect` gives the anisotropy R that content must be laid out
  at, plus the rectangle's true aspect WITH a verdict on whether this view can
  determine it at all; `key` pulls a matte three ways and reports where the
  plate breaks the assumption it was keyed with; `despill` measures the light it
  removes and the hue it rotates; `triangulate` is the exact matte when the
  object was shot against two backings; `warp` builds the composite in linear
  light, premultiplied and padded; `insert` is the horizon ratio; `grain`
  measures a plate's own grain by luminance band; `holdout` is the gate for any
  occluded edge; and `verify` is the point of the whole thing, five checks each
  anchored in the PLATE.
- **Standards**: `standards.py show loudness`, `... const safe.action`,
  `... freshness`. Dated scaffolding with its sources named, never authority.
- **Self test**: `python $SKILL_DIR/scripts/selftest.py` must end "0 failures"
  before any number here is worth anything. `--with-media` also builds a clip
  and exercises the ffmpeg paths.

## What it wraps, delegates and absorbs

**Wrapped** (called, and sequenced, never reimplemented): `colorgrade` is the
colour department entire, plus picture repair and series matching;
`video-editing` and `davinci-resolve` for the cut and for Super Scale; `upscale`
for STILLS only, because it is a per frame model and a clip needs one that sees
neighbours; `remotion` and `logo-animate` for animated type and brand marks;
`audio-editing`, `stems`, `audio-analysis`, `sound-design` for the sound
department's hands; `ocr` for reading burned in text back off a frame;
`font-tools`, `color-palette`, `image-editing`, `gimp`, `inkscape`,
`background-removal`. Roto is delegated, and it is TWO models, not one, because
a mask is not a matte: something to say WHICH pixels are foreground, then
something to say HOW MUCH at every edge pixel. Route one, when a Studio licence
is present, is DaVinci Resolve's Magic Mask, driven headless through
`CreateMagicMask("F"|"B"|"BI")` and `RegenerateMagicMask()`. Route two is a
segmentation model (SAM 3, or SAM 3.1 from 2026-03-27) into a learned video
matting model, which wants a GPU and its own environment. Licences differ
sharply between the candidates and decide which is usable on a paid job; see
`reference/06_compositing.md` section 10. What this skill contributes to such a
job is everything downstream of the matte.

**Delegated** (named as the neighbour, never duplicated): `image-gen`, `watch`,
`media`, `blender` and `blender-video`, `voice` and `text-to-speech`,
`translate`, `screenplay`, `presentations`, `compress`, `cloud-sync`,
`workflow`, `docs`, `spreadsheet`, `img2threejs`.

**Absorbed** (nothing existed, so it is built here): conform and timecode,
the supers engine, subtitles entirely, loudness, delivery verification, and
version and archive safety.

## The 2026 standards moment

Every figure carries the date it was checked and the document it came from.
`standards.py freshness` flags anything past 120 days, and the most expensive
mistake in this department is applying last year's figure with this year's
confidence. Read in the primary text on 2026-08-23: EBU R128 v5 (target -23.0
LUFS, the plus or minus 1.0 LU tolerance applying only where the target is not
practically achievable, true peak -1 dBTP, level gated, no emphasis on speech);
EBU R95 rev 1 (93 and 90 per cent, concentric); one large VOD platform's mix spec
(-27 LKFS dialog gated, plus or minus 2 LU, -2 dBTP) and its English timed text
guide (42 characters, two lines, 20 CPS adult and 17 children's, and those
numbers live in the PER LANGUAGE guide, not the general one); IMSC 1.2 as a W3C
Recommendation of 4 August 2020; OpenTimelineIO 0.18.1, Apache 2.0. Everything
still tagged [VERIFY] is listed in `reference/09_sources.md`.

## Setup

ffmpeg and ffprobe on PATH is the whole requirement for every engine except two.
The supers legibility audit renders a glyph mask and measures real pixels, and
the compositing engine does tracking, keying and warping, so both want numpy,
OpenCV, SciPy and Pillow in this skill's OWN environment, never the bot's:

    python3 -m venv ~/.venvs/post
    ~/.venvs/post/bin/pip install pillow numpy opencv-python-headless scipy

Three things use it: the supers legibility audit, the whole of `comp.py`, and
the whole of `upres.py` except `routes` and `superscale`. Run them with
`~/.venvs/post/bin/python`; everything else runs with the system
`python3`. `selftest.py` runs either way and SKIPS the compositing section with
an explanation when it is run without that environment, so run it with
`~/.venvs/post/bin/python` to exercise everything. The colour department calls
`colorgrade`, which keeps its own environment at `~/.venvs/colorgrade`.

## Documentation

- `reference/00_departments.md` the spine, department by department, with gates
- `reference/01_conform.md` rates, timecode, drop frame, handles, interchange
- `reference/02_colour.md` how a file declares itself, and who does the grading
- `reference/03_supers.md` type on picture, derived geometry, Lab legibility
- `reference/04_subtitles.md` formats, reading speed, collisions, Greek
- `reference/05_sound.md` R128 read in the primary text, and the platform gates
- `reference/06_compositing.md` tracking, mattes, premultiply, screen comps
- `reference/07_delivery.md` masters, splices, proofs, viewing copies, archive
- `reference/08_failures.md` **THE FAILURE LOG.** Forty two faults, each one
  measured on a real film and then fixed. Start here when something is wrong.
- `reference/09_sources.md` every source with its date and verification state
- `reference/10_resolution.md` effective resolution, boiling, and the routes
- `practice/method.md` how to work a job, and the two registers

## Output discipline

- Lead with the direct answer, then the numbers, then the grounding.
- Never state a condition you did not measure on the file in front of you.
- Say what you did NOT check. Every result here carries its own limit, and a
  result that comes back without its limit attached is a bug in the tool.
- Close a substantive answer by naming the file, the decode path and the
  profile, and listing anything unproven.

## Build roadmap

- **Stage 1 (shipped).** The spine and both disciplines. `SKILL.md`, `SCOPE.md`,
  `route.py`, `standards.py` with freshness, the reference packs seeded from
  real failures, `practice/method.md`.
- **Stage 2 (shipped).** `spec.py` and `prove.py`: the ingest engine including
  the filename claim check and the measured bit depth, and the verifier with the
  generation floor, the packet timestamp seek, the cut proof, the colour tag
  walk, the layer derived prediction and the cache.
- **Stage 3 (shipped).** `supers.py`: font metrics with no dependencies, derived
  family geometry, safe areas, and the Lab legibility audit.
- **Stage 4 (shipped).** `subs.py`: a whole new capability, SRT and WebVTT and
  TTML, the rule checker, retiming, and the collision check against supers.
- **Stage 5 (shipped).** `audio.py`: loudness measured and normalised against a
  profile, with the dialogue gate refusal.
- **Stage 6 (shipped).** `conform.py`: timecode and drop frame proved by round
  trip, EDL reading and checking, handles.
- **Stage 7 (shipped).** `deliver.py` and `archive.py`: the delivery precheck,
  the file audit, and the three deletion gates.
- **Stage 8 (shipped).** `comp.py` plus `_pix.py`, `_geom.py`, `_matte.py` and
  `_track.py`: cadence, tracking with a measured model and a measured solve
  scale and a two route certificate, ordered rings with per corner occlusion
  verdicts, the anisotropy and the Zhang and He rectangle aspect with its
  determinacy verdict, three keys with the Vlahos violation map, exact
  triangulation, named despill with its measured luma and hue cost, linear light
  premultiplied compositing, grain by luminance band, the horizon ratio insert,
  and the five plate anchored checks. Grounded in Smith and Blinn 1996,
  Evangelidis and Psarakis 2008 and Zhang and He 2004, all read in the primary
  text, and validated against synthetic ground truth in `selftest.py`.
- **Stage 9 (shipped).** `upres.py` plus `_res.py`: effective resolution from
  the radially averaged power spectrum with a curvature guard and an exact
  pixel doubling lattice detector, the routes with their licences and their
  places in the spine, temporal stability on a bounded scale-free statistic
  with one shared motion model, the six things an enlargement may never touch,
  and the Resolve Super Scale enumerations read out of the shipped manual.
  Calibrated against synthetic ground truth and re-checked on real encodes in
  `selftest.py`; the flow sign convention and the unbounded ratio it hid are
  failures 36 and 37.
- **Stage 10.** OpenTimelineIO for real AAF and FCPXML interchange, on the first
  job that actually has one.
- **Stage 11.** A house profile per recurring client, supplied by the user from
  their own delivery documents rather than shipped here.
