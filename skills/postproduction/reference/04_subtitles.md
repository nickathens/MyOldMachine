# Subtitles and captions

A whole department that had no tool in this toolkit before, and the cheapest one
to get wrong: every fault is invisible in the edit and obvious to the audience.

## Formats

| Format | What it is | Carries |
|---|---|---|
| SRT | The lowest common denominator | Times and text. Nothing else, reliably. |
| WebVTT | The web one | Times, text, cue settings, regions, some styling |
| TTML / IMSC | The broadcast and platform one | Times, text, regions, styling, images |
| iTT | Apple's constrained TTML profile | A TTML subset with its own rules |
| STL (EBU 3264) | Legacy European broadcast | Fixed record format, character set limits |

IMSC 1.2 is a W3C Recommendation of 4 August 2020, fetched 2026-08-23. It
defines a Text Profile and an Image Profile, and a document cannot conform to
both: they are different ways of representing a subtitle. Whether a later text
profile revision has advanced is [VERIFY] at w3.org before quoting a version.

`subs.py` reads SRT, WebVTT and TTML into one shape and writes all three. The
round trip is tested: times to within 2 ms and text exactly.

    python subs.py read FILM.srt
    python subs.py convert FILM.srt --to ttml --out FILM.ttml

## The numbers, and where they actually live

The widely quoted figures for one large platform, from its ENGLISH style guide,
fetched 2026-08-23: 42 characters per line, a maximum of two lines, 20
characters per second for adult programmes and 17 for children's.

**Those numbers live in the PER LANGUAGE guides, not in the general
requirements.** The general requirements page carries the durations, the two
line maximum, centre justification top or bottom, and the glyph list. Cite the
language guide. Greek, and every other language, has its own numbers and they
are not the English ones.

Durations from the general requirements: minimum five sixths of a second,
maximum 7 seconds.

## State the counting rule with every reading speed

Two tools that disagree only about whether spaces count will disagree about
whether a file passes, and the argument is unresolvable unless somebody says
which rule they used. `subs.py check` prints its rule every time it runs.
Default here: every character counts except the line break. `--no-count-spaces`
switches it.

## What the checker checks

    python subs.py check FILM.srt --profile broadcast_hd_r128 --fps 25

- reading speed against the limit
- line count and line length
- minimum and maximum duration
- gap to the next event, and any overlap
- with `--fps`, whether every time lands on a frame boundary

Frame boundaries matter on a burn in and on any format that stores frames rather
than milliseconds. A subtitle timed to 3.020 s at 25 fps is at frame 75.5, which
is not a place.

## Timing is fixable, text is not

`subs.py` will move times: shift, retime, and report a bad gap. It will NOT
rewrite anybody's words, split a line, or shorten a sentence to hit a reading
speed. Those are editorial decisions with a language in them, and a tool that
makes them quietly will make them wrong in Greek.

When the reading speed fails, the honest options are: hold the subtitle longer
if the picture allows, split it across two events, or cut words. All three are
somebody's decision.

## Collisions with supers

The most predictable client note there is, and it can be found before anyone
watches:

    python subs.py collide FILM.srt --supers PLAN.json --fps 25

It compares TIME. Whether they also share SPACE depends on where the burn in or
the player puts the subtitle, which is why `subs.py burn` prints the geometry it
will use rather than accepting a default.

The three honest fixes: move the subtitle, move the super, or push the subtitle
to the top of frame for that span. Say which you did.

## Burning in

    python subs.py burn FILM.srt --video FILM.mov --raster 1920x1080

MarginV is set to the TITLE SAFE inset so the bottom line cannot fall outside
it. Burning in is destructive: keep a textless master, and never burn into the
only copy. A film with burned in subtitles cannot be localised without going
back to the textless, which is exactly why the textless is on the delivery list.

## Greek specifics

Greek runs longer than English for the same content, typically by a fifth,
which turns a compliant English subtitle into a two line Greek one and pushes
the reading speed up. Plan for it at the timing stage rather than discovering it
at QC. Greek final sigma, accented capitals and the ano teleia all exist in the
common subtitle glyph lists but a legacy STL character set may not carry them;
check before committing to STL.
