#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs roll: each cue shows the previous line above the new one,
and a 10 ms cue then repeats the new line alone, so every spoken line arrives
two or three times. A cue's leading line that repeats the last line of the cue
before it is dropped, then consecutive identical cues are merged.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path


# WebVTT allows the hours to be left out (00:01.000); YouTube always writes
# them, other sites often do not, and a file of hourless cues parsed to nothing.
TS_RE = re.compile(
    r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    last_line: str | None = None
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        # A cue ends at an EMPTY line. YouTube opens every word-timed cue
        # with a line holding one space; ending the cue there dropped each
        # spoken line and kept only its 10 ms repeat, stamped at the END of
        # the phrase instead of its start.
        while i < len(lines) and lines[i] != "":
            cleaned = html.unescape(TAG_RE.sub("", lines[i])).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        shown = cue_lines
        if shown and last_line is not None and shown[0] == last_line:
            shown = shown[1:]  # the rolled-up line from the cue before
        if cue_lines:
            last_line = cue_lines[-1]
        cue_text = " ".join(shown).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] >= lo and seg["start"] <= hi]


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        # the same shape as the frame list's t=: H:MM:SS past an hour
        hours, rem = divmod(int(seg["start"]), 3600)
        minutes, seconds = divmod(rem, 60)
        stamp = f"[{hours}:{minutes:02d}:{seconds:02d}]" if hours else f"[{minutes:02d}:{seconds:02d}]"
        lines.append(f"{stamp} {seg['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
