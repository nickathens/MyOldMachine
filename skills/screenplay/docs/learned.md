# Screenplay Skill: Learning Log

This file is updated as the skill is used. Things discovered during actual writing sessions.

## Tools
<!-- Edge cases, workarounds, better parameters for screenplain/afterwriting -->

- 2026-09-27: the PDF standard Courier that screenplain uses has no Greek (accented vowels printed as black boxes), and afterwriting's fonts print Greek as blank space. Export now sets such scripts in Nimbus Mono PS (or another Courier style face with the letters) with a per letter fallback; afterwriting is refused for them.
- afterwriting exits 0 when it cannot read the script and writes nothing, so export checks for the file itself.
- screenplain splits a long speech across a page break with no (MORE)/(CONT'D); afterwriting adds them.

## Craft
<!-- Writing lessons learned from actual script sessions -->

## Formatting
<!-- Formatting gotchas discovered in practice -->

## Projects
<!-- Per-project notes -->
