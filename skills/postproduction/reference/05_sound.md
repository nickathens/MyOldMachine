# Sound: the loudness the platform will measure

## EBU R128, read in the primary text

R128 version 5, Geneva, November 2023, read in full on 2026-08-23. The
recommendation itself, not a summary of it:

- Programme Loudness normalises to a **Target Level of -23.0 LUFS**.
- The often quoted plus or minus 1.0 LU is **not a general tolerance**. R128
  allows it only "where attaining the Target Level is not achievable
  practically (for example, live programmes)". For a file based deliverable the
  target is -23.0.
- The only other tolerance in the document is **plus or minus 0.2 LU** for
  measurement error in a QC workflow.
- **True Peak shall not exceed -1 dBTP** during production (linear audio),
  measurement tolerance plus or minus 0.3 dB for a signal band limited to
  20 kHz. Distribution systems with data reduction may set a LOWER ceiling; see
  EBU Tech 3344.
- Measurement is the **level gated** method of ITU-R BS.1770 equation 7,
  relative gate -10 LU since R128 v2, over the signal **in its entirety** and
  explicitly **without emphasis on speech**.
- LUFS and LKFS are the same unit.
- Four supplements: **s1** short form (adverts, promos), **s2** streaming, **s3**
  radio, **s4** cinematic content.

## The platforms are a different measurement, not a different number

One large VOD platform specifies **-27 LKFS, plus or minus 2 LU, DIALOG GATED**,
true peak not above **-2 dBTP**, measured over the full programme per ITU-R
BS.1770-1, and the same figures apply to original mixes, dubs and audio
description. US broadcast is -24 LKFS under ATSC A/85. Music streaming clusters
near -14 LUFS and is a different world with different rules.

**A mix delivered at -23 ungated is not a -27 dialog gated mix minus 4.** The
gate changes what is being measured, and the difference between the two numbers
is not a constant. Ask which target AND which gate before mixing, not after.

`audio.py` measures BS.1770 level gated loudness, which is what R128 asks for.
It is not a dialogue gated meter and it refuses to pretend: against a dialog
gated profile it reports what it measured, names the gap, and says the number
has to come from a meter that gates on dialogue. It will also refuse to
NORMALISE to a dialog gated target rather than land the film in the wrong place.

## Normalise last

Every trim moves the integrated measurement, so loudness is measured and set on
the FINAL cut, not on the mix stem and not before picture lock.

    python audio.py measure FILM.mov
    python audio.py check FILM.mov --profile broadcast_hd_r128
    python audio.py normalise FILM.mov --profile broadcast_hd_r128 --out OUT.mov

Normalising asks for CONSTANT GAIN (loudnorm linear mode): a delivered film
should be turned up or down, not reshaped. When loudnorm cannot reach the target
within the true peak ceiling by constant gain it falls back to dynamic mode,
which reshapes the mix. That is a mix decision, not a technical one, and
`audio.py` reads the normalisation type out of loudnorm's own output and says
which one happened rather than swallowing it.

Always measure the OUTPUT. The container was rewritten, so the picture needs
re-checking too.

## Channels

Channel COUNT and layout are readable. Channel ORDER inside a layout is not: a
file can be tagged 5.1 with the centre and the LFE swapped and every tool will
agree with it. Confirm the order against the mix's own routing sheet.

    python audio.py layout FILM.mov --profile vod_uhd_dialog_gated

## Stems

When a delivery names stems, they must SUM to the mix. A stem set that does not
sum is the classic late discovery, and it is a two minute check: sum them and
measure the difference against the mix.

## The neighbouring skills

`audio-editing` for cuts, fades and format conversion. `stems` for separating a
mix that arrived without them, which is a rescue rather than a delivery route.
`audio-analysis` for BPM, key and visualisation. `sound-design` for synthesis.
`text-to-speech` for scratch VO and for a temp track that is honest about being
temp.
