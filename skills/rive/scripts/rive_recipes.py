#!/usr/bin/env python3
"""Small generators for motion Rive has no single element for.

  typing    a string that types itself in at a human rhythm, as a data curve
            for rive_render.py (--timeline). The text run is bound to a view
            model string and each frame gets the prefix typed so far; a caret
            placed after the text in a hug row follows it.
  keys      the same typing baked into the file instead: KeyFrameString keys
            on a TextValueRun (propertyKey 268), for a page that has to type
            with no host code.
  count     a number that counts from A to B with an ease, as a data curve
            (a counter, a price, a score).

  rive_recipes.py typing "rive cli" --path query --start 0.8 -o typing.json
  rive_recipes.py keys "rive cli" --object 0:34 --start 0.8
  rive_recipes.py count --path value --from 0 --to 1250 --start 0.5 --duration 1.6 -o count.json

Typing timing is seeded, so the same command gives the same rhythm; change
--seed for a different take.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

EASES = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "in": (0.42, 0.0, 1.0, 1.0),
    "out": (0.0, 0.0, 0.58, 1.0),
    "inout": (0.42, 0.0, 0.58, 1.0),
    "expo-out": (0.16, 1.0, 0.3, 1.0),
}


def typing_times(text: str, start: float, cps: float, jitter: float, seed: int,
                 word_pause: float, first_delay: float = 0.0) -> list[tuple[float, str]]:
    """(time, prefix) after each keystroke. Spaces and punctuation get a pause."""
    rng = random.Random(seed)
    t = start + first_delay
    keys = []
    base = 1.0 / max(cps, 0.1)
    for i, ch in enumerate(text):
        keys.append((round(t, 4), text[: i + 1]))
        step = base * (1.0 + rng.uniform(-jitter, jitter))
        if ch == " ":
            step += word_pause * rng.uniform(0.6, 1.4)
        elif ch in ".,;:!?":
            step += word_pause * 1.6
        t += max(0.03, step)
    return keys


def bezier_y(ease: tuple, x: float) -> float:
    """y of a CSS cubic-bezier at x, by bisection."""
    x1, y1, x2, y2 = ease

    def coord(t, a, b):
        return 3 * a * t * (1 - t) ** 2 + 3 * b * t * t * (1 - t) + t ** 3

    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if coord(mid, x1, x2) < x:
            lo = mid
        else:
            hi = mid
    return coord((lo + hi) / 2, y1, y2)


def count_keys(a: float, b: float, start: float, duration: float, ease: str, decimals: int,
               rate: float = 60.0) -> list[tuple[float, float]]:
    shape = EASES[ease]
    n = max(1, int(round(duration * rate)))
    keys = [(round(start, 4), round(a, decimals))]
    for i in range(1, n + 1):
        x = i / n
        keys.append((round(start + x * duration, 4), round(a + (b - a) * bezier_y(shape, x), decimals)))
    return keys


def string_keys_rml(keys: list[tuple[float, str]], object_id: str, fps: int = 60) -> str:
    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

    rows = ['        <KeyFrameString value="" frame="0" interpolationType="hold"/>']
    rows += [f'        <KeyFrameString value="{esc(v)}" frame="{int(round(t * fps))}" interpolationType="hold"/>'
             for t, v in keys]
    return (f'<KeyedObject objectId="{object_id}">\n    <KeyedProperty propertyKey="268">\n'
            + "\n".join(rows) + "\n    </KeyedProperty>\n</KeyedObject>\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("typing", "keys"):
        p = sub.add_parser(name)
        p.add_argument("text")
        p.add_argument("--start", type=float, default=0.5, help="seconds before the first key")
        p.add_argument("--cps", type=float, default=11.0, help="characters per second")
        p.add_argument("--jitter", type=float, default=0.35, help="0 = metronome, 0.5 = loose")
        p.add_argument("--word-pause", type=float, default=0.08)
        p.add_argument("--seed", type=int, default=7)
        if name == "typing":
            p.add_argument("--path", required=True, help="the view model string the text run is bound to")
            p.add_argument("-o", "--output", required=True)
            p.add_argument("--initial", default="", help="value before the first key")
        else:
            p.add_argument("--object", required=True, help="the TextValueRun id to key")
            p.add_argument("--fps", type=int, default=60, help="the animation's fps")
    c = sub.add_parser("count")
    c.add_argument("--path", required=True)
    c.add_argument("--from", dest="a", type=float, default=0.0)
    c.add_argument("--to", dest="b", type=float, required=True)
    c.add_argument("--start", type=float, default=0.0)
    c.add_argument("--duration", type=float, default=1.5)
    c.add_argument("--ease", choices=sorted(EASES), default="expo-out")
    c.add_argument("--decimals", type=int, default=0)
    c.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "count":
        keys = count_keys(args.a, args.b, args.start, args.duration, args.ease, args.decimals)
        doc = {"curves": {args.path: {"keys": keys, "interpolation": "hold"}}}
        Path(args.output).write_text(json.dumps(doc) + "\n", encoding="utf-8")
        print(f"wrote {args.output}: {args.path} {args.a:g} -> {args.b:g} over {args.duration:g} s ({len(keys)} keys)")
        return 0
    keys = typing_times(args.text, args.start, args.cps, args.jitter, args.seed, args.word_pause)
    if args.cmd == "keys":
        sys.stdout.write(string_keys_rml(keys, args.object, args.fps))
        return 0
    doc = {"curves": {args.path: {"keys": [[0.0, args.initial]] + [[t, v] for t, v in keys],
                                  "interpolation": "hold"}}}
    Path(args.output).write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(keys)} keystrokes, {keys[0][0]:.2f}-{keys[-1][0]:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
