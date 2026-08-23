# Conform: rates, timecode, drop frame, handles and interchange

The arithmetic the cut rests on. It fails silently, which is why it gets its own
engine and its own tests.

## Rates are ratios

23.976 and 29.97 are not rates. They are roundings of 24000/1001 and 30000/1001,
and a timecode built on the rounding drifts slowly enough to reach delivery.
`_common.rate()` parses every form into an exact Fraction and snaps the decimal
NTSC forms to the exact ratio.

| Written | Exact | Labels per second | Drop frame |
|---|---|---|---|
| 24 | 24 | 24 | no |
| 23.976, 23.98 | 24000/1001 | 24 | no standard scheme |
| 25 | 25 | 25 | no |
| 29.97 | 30000/1001 | 30 | yes |
| 30 | 30 | 30 | no |
| 50 | 50 | 50 | no |
| 59.94 | 60000/1001 | 60 | yes |
| 60 | 60 | 60 | no |

## Drop frame drops no pictures

It skips LABELS so the label keeps up with the wall clock. At 30000/1001 the
labels :00 and :01 are skipped at the top of every minute except every tenth
minute; at 60000/1001 it is four labels. Every picture is still there.

Consequences worth knowing by heart, all of them checked in `selftest.py`:

- 00:00:59:29 plus one frame is 00:01:00;02.
- One hour of drop frame timecode is 107892 frames.
- Frame 17982 is 00:10:00;00.
- A day of drop frame is 2589408 frames.
- One hour of NON drop at the same rate is 108000 frames, and that runs 3.6
  seconds long against the clock. That 3.6 seconds is the entire reason drop
  frame exists.
- 00:01:00;00 is not a legal drop frame label. A file full of them was written
  by something that treated drop frame as a cosmetic separator.

The separator matters: `:` for non drop, `;` for drop. A drop frame timecode
read as non drop, or the reverse, puts an hour long programme 3.6 seconds out
and every frame number after it points at the wrong picture.

    python conform.py tc to-frames 01:00:00:00 --fps 29.97df
    python conform.py tc from-frames 107892 --fps 29.97df
    python conform.py tc add 00:59:59:29 --frames 1 --fps 29.97df

## Out points are exclusive

CMX convention: the out point is the first frame NOT used. A tool that treats
them as inclusive is one frame long on every event, which is the most common
conform error there is. `conform.py handles` states this in its own output every
time it runs, because it is the assumption most worth surfacing.

    python conform.py handles --in 01:00:04:12 --out 01:00:09:00 --handle 12 --fps 24

Handles are pulled BEFORE the cut and AFTER it, and they run off the end of a
source more often than anyone expects. Pass `--source-first` and `--source-last`
and the tool clamps and tells you rather than producing a pull that cannot be
made.

## Start timecode is a convention, not a default

01:00:00:00 and 10:00:00:00 are both common. Which one this job uses is a
project fact. So is whether the master starts on the first frame of picture or
after a slate and countdown.

## Interchange, from least to most

**CMX 3600 EDL.** The lowest common denominator. Text, universally readable,
carries no effects, no multi layer video, no colour, and crucially NO FRAME
RATE. The FCM line says drop frame or not and nothing says 24 against 25. The
rate is a project fact and has to be supplied.

`conform.py edl read` parses it; `conform.py edl check` holds it against itself:
every event's source length must equal its record length unless there is a
deliberate speed change, and the record timeline must be contiguous. A gap is
black or a missing event, and either way somebody has to say which.

The limit of that check, stated in its own output: it checks the EDL against
ITSELF. A whole EDL read at the wrong rate is perfectly self consistent and
completely wrong.

**FCPXML.** Carries more: effects, multi layer, some colour. Application
specific in practice.

**AAF.** The Avid route, and the one that carries audio properly. Binary, and
the OpenTimelineIO adapter for it lives in a SEPARATE repository from core,
which surprises people who install core and expect AAF to work.

**OpenTimelineIO.** 0.18.1 on PyPI as of 2026-08-23, Apache 2.0, Python above
3.9, licence clean for paid client work. The right target when a job needs real
timeline interchange in both directions. This skill does not require it: adding
it is a decision to take when a job actually has AAF or FCPXML in it.

## Conformed rates and cadence

A file whose container rate and average rate disagree is the first sign that
something was conformed by repeating or dropping pictures. `spec.py probe`
flags it; `prove.py timeline` reads the file's own packet timestamps and says
whether the timeline is uniform.

A generated plate is very often a higher rate conformed down by DROPPING
pictures, which makes the whole scene lurch on a fixed beat. That lurch is real
motion the comp must follow, not noise to smooth away. See failure 23.

## Retiming subtitles across a rate change

Two different questions, and `subs.py retime` makes you name which:

- **scale**: the picture was re-timed, the film is now a different length, and
  every subtitle time scales with it.
- **keep**: the picture kept its running time, so the times do not move and only
  the frame boundaries do.

Getting this backwards is invisible at the head of the film and a second out by
the end.
