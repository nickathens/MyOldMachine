# Delivery profile schema

A profile is a PROJECT FACT set, written down. The ones shipped here are
scaffolding taken from published standards and tagged accordingly; a real job
supplies its own, copied out of the client's delivery document, which always
wins. Start one with `python scripts/spec.py profile --template`.

Fields. Anything absent is "not specified", which is different from "any": an
unspecified field is a question to ask, and `spec.py gate` lists them.

    slug            file name without .json
    name            what a human calls it
    as_of           when the numbers were last confirmed
    source          where the numbers came from
    verify          true when the numbers are secondary and need confirming
    picture.width, .height          integers, the full raster
    picture.fps                     a RATIO string: "25", "24000/1001"
    picture.scan                    progressive | interlaced_tff | interlaced_bff
    picture.codec                   list of acceptable ffmpeg codec names
    picture.bit_depth               8 | 10 | 12 | 16
    picture.chroma                  "420" | "422" | "444" | "rgb"
    picture.primaries/.transfer/.matrix/.range   the four colour declarations
    picture.max_duration_s / .exact_duration_s   optional
    audio.codec                     list of acceptable codec names
    audio.sample_rate               48000
    audio.channels                  integer
    audio.layout                    "stereo", "5.1", ...
    audio.loudness.target_i         LUFS
    audio.loudness.tol_i            LU, one sided
    audio.loudness.max_tp           dBTP
    audio.loudness.max_lra          LU, or null
    audio.loudness.gate             "bs1770" | "dialog"
    safe.action / safe.title        fractions of the raster
    subtitles.*                     the timed text rules for this delivery
    items                           the deliverable list, slugs from deliver.py
