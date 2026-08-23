# Method: how to work a post job with this skill

## The shape of every job

1. **Measure what arrived.** `spec.py probe` on every file, `spec.py claims` on
   every name. Never start from the email.
2. **Name the delivery facts.** `spec.py gate --profile ...`. If any are unknown,
   ask for the whole set in ONE list and stop. A standard's constant may
   default; a project's fact may not.
3. **Route the work.** `route.py find "the thing the client said"`. It returns
   the department, its gate, and the steps that come before it.
4. **Do the steps in order**, and prove each gate before starting the next.
5. **Prove the master against a PREDICTION**, not a description.
6. **Check the delivery list**, then hand to the person who sends it.
7. **Archive so the next version can go back.**

## The two disciplines

### Measurement discipline

- Every number carries the FILE it was measured on and the DECODE PATH it came
  through. Two arrays from different decode paths cannot be compared, and
  `_common.require_same_path` enforces it.
- **Notes are not the disk.** A note saying a file was deleted, sent or rebuilt
  records intent, not fact. Look at the artifact before repeating it.
- **A hash proves the pixels** and says nothing about how the file DECLARES them.
- **A new check must be run against an already approved file before it is
  believed.** If it fails there, the check is wrong, not the film.
- **Read every measurement against the frame's own generation floor**, never
  against zero.
- **Nothing is sent until the send confirms** for that exact path.
- When a check cannot separate two candidates, say that it BOUNDS but cannot
  RANK, and move the ranking to a measurement with real ground.

### The spec gate

Before any render, name: frame size; frame rate and whether it is true or
conformed; scan; colour primaries, transfer, matrix and range; codec and bit
depth; audio channel layout and loudness target WITH its gate; safe area
convention; aspect and any crop; and the deliverable list including textless and
sidecars.

What may default are the standards' own constants: the Rec.709 tags, the 93 and
90 per cent safe areas, the R128 target and true peak ceiling. What may never
default is a project fact.

One escape hatch, and it is the user's to open: when they say to assume typical
values, run on documented typical inputs, list every assumption first, and stamp
the whole result INDICATIVE.

## Two registers, one skill

**Client register.** Plain language, no file paths, no command lines. What was
wrong, what was done, what to look at, and what is still open. A client asking
"is it fixed" wants the answer first and the evidence second.

**Finishing register.** Full technical detail: the measurement, the file it was
taken on, the decode path, the threshold and where the threshold came from, and
what was NOT checked. Written so a colourist or an online editor can check it
line by line.

Read which one is wanted from the question, and never let a casual register
lower the standard of the measurement behind the answer.

## When something is wrong and you cannot see why

1. Look at the frame at 1:1. Crop it and look. Most of the failures in the log
   were found by eye and only then measured.
2. Ask what the check is anchored in. If it is anchored in the same model the
   build used, it cannot fail.
3. Ask which frames were COPIED and which were RE-ENCODED, and give the two
   groups different expectations.
4. Ask whether the thing you are measuring can even be measured from what you
   have. An outline cannot pin an aspect; a keyed matte cannot audit a track.
5. Read `reference/08_failures.md`. There is a good chance it is in there.

## Long work

Anything that will outlive a single reply must be launched DETACHED, at the
moment of launch, with marker files for done and failed plus a log. Deciding to
detach after a render has died is a decision that arrives one render too late.
On macOS there is no `setsid`; use a new session from the launcher itself.

Check what else runs on the machine overnight before starting something that
will still be going at 05:00.

## Working on a shared machine

Files, uncommitted changes and running work found on disk may have been produced
by another person's session. Never present shared machine state as this user's
own work. If the origin is unclear, say so plainly.
