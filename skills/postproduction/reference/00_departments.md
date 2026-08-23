# The spine: twelve departments, in order, each with a gate

Post is not one job. It is twelve jobs in a fixed order, and most of the damage
in this skill's failure log came from doing one of them out of turn, or from
starting the next one before the previous one had been proved.

A GATE is not a checklist item. It is the thing that has to be TRUE before the
next step is allowed to start, and it is stated as a measurement so it can be
argued with. `route.py spine` prints this list; `route.py gate <dept>` prints
one.

---

## 1. Ingest and spec

**Answers.** What actually arrived, and what has to go out.

**Gate.** True resolution, true frame rate and whether it is native or
conformed, colour tags, codec, bit depth, chroma, audio layout, duration, and
the deliverable list, every one of them read off the file rather than off the
filename or the email.

**Why it is first.** Everything downstream is a transformation of facts
established here, and each of those facts has been wrong on a real job. See
failure 1: a whole review set named for a resolution none of the files had.

**Engines.** `spec.py probe`, `spec.py claims`, `spec.py depth`, `spec.py gate`.

---

## 2. Picture repair

**Answers.** Frames that stick, judder, teleport, or step in colour with no cut.

**Gate.** Held out frames score the rebuild, and all three of the scores are
read, not the error alone. Frame rate, frame count, duration and constant
timestamp spacing verified on the DELIVERED file.

**Why it is second, before colour.** Rebuilding frames afterwards means
rebuilding them from source, which silently drops any colour repair that lands
on them.

**Wraps.** The colorgrade skill's track three, which does the work. This
department owns the ORDER and the proof, not the algorithm.

---

## 3. Resolution and restoration

**Answers.** Softness, compression damage, and the delivery raster.

**Gate.** The source's EFFECTIVE resolution measured before anything is
enlarged; the frame rate, frame count, duration, audio and colour tags identical
on the result; and the result proved TEMPORALLY.

**Why it is entered TWICE.** Restoring at size belongs here, at stage 3, because
it changes every pixel the grade will be built on, and it comes after repair
because there is no sense paying a model to rebuild a duplicate frame.
Enlarging to the delivery raster belongs at stage 10, at the master, because
enlarging early makes every department downstream pay for pixels nobody asked
for and forces the whole job to be rebuilt when the raster moves. Putting the
second one where the first belongs is the expensive error this department
exists to prevent.

**Two measurements that exist nowhere else in the toolkit.** Whether a file
carries the resolution its raster claims, read off the picture's own spectrum
rather than its header; and whether an enlargement BOILS, which no still frame
can show, because a stills upscaler run down a clip re-invents its detail every
picture.

**Engines.** `upres.py`. **Wraps.** upscale (STILLS only), davinci-resolve
(Super Scale, Studio), colorgrade (track three).

---

## 4. Conform

**Answers.** The cut, the timeline, handles, timecode.

**Gate.** Frame count and duration agree with the edit, every join is accounted
for, and the timecode arithmetic is done in the delivery's own rate and drop
frame convention.

**Engines.** `conform.py`. **Wraps.** davinci-resolve, video-editing.

---

## 5. Compositing and inserts

**Answers.** Screen comps, clean plates, repaints, object removal, inserts.

**Gate.** The track is proved on HELD OUT frames, and every check is anchored in
the PLATE, never in the model the build used. A keyed matte cannot audit the
track that put content inside it.

**Why here.** On the repaired picture, so nothing gets rebuilt underneath the
comp; in the film's own colour space, before the grade moves the ground.

**Wraps.** colorgrade track two, image-editing, gimp, inkscape,
background-removal, blender, img2threejs.

---

## 6. Colour

**Answers.** Balance, shot to shot match, the look, one LUT per shot.

**Gate.** dE2000 consistency measured before and after, and the list of
corrections that hit a safety cap declared rather than hidden.

**Wraps.** The colorgrade skill, entirely. This department sequences it and
proves its output; it does not reimplement a line of it.

---

## 7. Supers and titles

**Answers.** Type on picture: supers, lower thirds, end boards, CTAs.

**Gate.** Safe area respected, AND the ink separated from its ACTUAL ground in
CIE Lab, per block, measured at 1:1 on the graded picture the audience will see.

**Why after colour.** Legibility is a property of the ink against the ground,
and the grade IS the ground. Type approved over an ungraded plate is type
approved against a picture nobody will watch.

**Engines.** `supers.py`. **Wraps.** remotion, logo-animate, font-tools, ocr.

---

## 8. Subtitles and captions

**Answers.** Timed text: sidecars, burn ins, localisation, access captions.

**Gate.** Reading speed, line count, line length, minimum and maximum duration,
gap between events, and no collision with a super.

**Why after supers.** A subtitle that lands on a super is the most predictable
client note there is, and it can only be predicted once the supers are placed.

**Engines.** `subs.py`. **Wraps.** translate, voice, ocr.

---

## 9. Sound

**Answers.** Mix, stems, and the loudness the platform will measure.

**Gate.** Integrated loudness, true peak and loudness range measured against the
named profile, ON THE FINAL CUT.

**Why last of the creative steps.** Every trim moves the integrated
measurement. Normalising before the cut locks means normalising to a number that
no longer exists.

**Engines.** `audio.py`. **Wraps.** audio-editing, audio-analysis, stems,
sound-design, text-to-speech.

---

## 10. Master

**Answers.** Build, splice, tag, and prove the file that ships.

**Gate.** The null version encode is byte identical to the delivered
predecessor; colour tags are uniform across every join; and the changed frame
list matches what the planner PREDICTED, not what somebody remembers changing.

**Engines.** `prove.py`, `spec.py`. **Wraps.** video-editing, davinci-resolve,
upscale.

---

## 11. Deliverables

**Answers.** Everything that leaves the building, and its paperwork.

**Gate.** Every item on the delivery list present and proved, every sidecar
named to the house convention, and a viewing copy labelled as a viewing copy.

**Engines.** `deliver.py check`, `deliver.py audit`. **Wraps.** compress,
cloud-sync, presentations, email.

---

## 12. Versions and archive

**Answers.** What was delivered, what changed, and what can be restored.

**Gate.** Survivors verified against their record BEFORE anything is condemned,
condemned files hashed on the way out, and every restore path proved to exist.

**Engines.** `archive.py`, `prove.py`.

---

## The order in one paragraph

Measure what arrived. Repair the picture before you grade it. Restore its
resolution while it is still small, and leave the enlargement for the master.
Conform the cut.
Composite on the repaired picture in the film's own space. Grade. Put the type
on the graded picture and measure it against the ground the audience sees.
Subtitle after the type is placed. Mix, and normalise loudness last. Build the
master and prove it against a prediction. Deliver the whole list. Archive so the
next version can go back.

## Three relationships with the rest of the toolkit

**WRAPPED.** This skill calls it and owns the method around it: colorgrade,
video-editing, davinci-resolve (including Super Scale), upscale for STILLS only,
remotion, logo-animate, the audio skills, ocr, font-tools, color-palette, the
still image editors.

**DELEGATED.** Named as the neighbour and never duplicated: image-gen, watch,
media, blender, voice and text-to-speech, translate, screenplay, presentations,
compress, cloud-sync, workflow, docs, spreadsheet.

**ABSORBED.** Built here because nothing existed: conform and timeline
interchange, the supers engine, subtitles entirely, loudness, delivery
verification, versioning and archive safety, and both resolution measurements
(whether a file carries the raster it claims, and whether an enlargement boils).

The boundary that keeps this skill honest: it owns METHOD, ORDER and PROOF, and
it writes an engine only where nothing exists. It must never reimplement
colorgrade, remotion or Resolve.
