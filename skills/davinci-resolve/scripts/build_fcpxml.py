#!/usr/bin/env python3
"""Build a .fcpxml timeline that DaVinci Resolve imports directly.

Assembles video clips end to end (plus optional music on a connected audio
lane) entirely offline: Resolve does not need to be installed or running.
Import in Resolve via File > Import Timeline > Import AAF, EDL, XML. Media
relinks automatically because absolute file URLs are embedded.

Stdlib only, plus ffprobe (ships with ffmpeg) for media metadata.
"""
import argparse
import json
import subprocess
import sys
import xml.dom.minidom
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

FRAME_DURATIONS = {
    "23.976": Fraction(1001, 24000),
    "24": Fraction(1, 24),
    "25": Fraction(1, 25),
    "29.97": Fraction(1001, 30000),
    "30": Fraction(1, 30),
    "50": Fraction(1, 50),
    "59.94": Fraction(1001, 60000),
    "60": Fraction(1, 60),
}


def ffprobe(path):
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f"ffprobe failed on {path}: {run.stderr.strip()}")
    return json.loads(run.stdout)


def rational(value: Fraction) -> str:
    if value.denominator == 1:
        return f"{value.numerator}s"
    return f"{value.numerator}/{value.denominator}s"


def probe_clip(path: Path, frame_duration: Fraction):
    info = ffprobe(path)
    duration = Fraction(info["format"]["duration"]).limit_denominator(1_000_000)
    frames = int(duration / frame_duration)
    if frames < 1:
        sys.exit(f"{path.name} is shorter than one frame at this fps.")
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return {
        "path": path,
        "duration": frames * frame_duration,
        "width": int(video["width"]) if video else None,
        "height": int(video["height"]) if video else None,
        "has_video": video is not None,
        "has_audio": audio is not None,
    }


def file_url(path: Path) -> str:
    return "file://" + quote(str(path), safe="/")


def build(args):
    frame_duration = FRAME_DURATIONS[args.fps]
    clips = [probe_clip(Path(p).expanduser().resolve(), frame_duration)
             for p in args.clips]
    music = (probe_clip(Path(args.music).expanduser().resolve(), frame_duration)
             if args.music else None)

    first_video = next((c for c in clips if c["has_video"]), None)
    width = first_video["width"] if first_video else 1920
    height = first_video["height"] if first_video else 1080

    assets, spine = [], []
    for i, clip in enumerate(clips):
        rid = f"r{i + 2}"
        clip["rid"] = rid
        flags = []
        if clip["has_video"]:
            flags.append('hasVideo="1" format="r1"')
        if clip["has_audio"]:
            flags.append('hasAudio="1" audioSources="1" audioChannels="2"')
        assets.append(
            f'    <asset id="{rid}" name={quoteattr(clip["path"].stem)} start="0s" '
            f'duration="{rational(clip["duration"])}" {" ".join(flags)}>\n'
            f'      <media-rep kind="original-media" src={quoteattr(file_url(clip["path"]))}/>\n'
            f'    </asset>')

    total = Fraction(0)
    music_rid = None
    if music:
        music_rid = f"r{len(clips) + 2}"
        assets.append(
            f'    <asset id="{music_rid}" name={quoteattr(music["path"].stem)} start="0s" '
            f'duration="{rational(music["duration"])}" hasAudio="1" audioSources="1" audioChannels="2">\n'
            f'      <media-rep kind="original-media" src={quoteattr(file_url(music["path"]))}/>\n'
            f'    </asset>')

    timeline_total = sum((c["duration"] for c in clips), Fraction(0))
    for i, clip in enumerate(clips):
        children = ""
        if music and i == 0:
            music_dur = min(music["duration"], timeline_total)
            children = (f'\n          <asset-clip ref="{music_rid}" lane="-1" offset="0s" '
                        f'name={quoteattr(music["path"].stem)} duration="{rational(music_dur)}" start="0s"/>\n        ')
        spine.append(
            f'        <asset-clip ref="{clip["rid"]}" offset="{rational(total)}" '
            f'name={quoteattr(clip["path"].stem)} duration="{rational(clip["duration"])}" '
            f'start="0s" tcFormat="NDF">{children}</asset-clip>')
        total += clip["duration"]

    doc = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" frameDuration="{rational(frame_duration)}" width="{width}" height="{height}"/>
{chr(10).join(assets)}
  </resources>
  <library>
    <event name={quoteattr(args.event)}>
      <project name={quoteattr(args.name)}>
        <sequence format="r1" duration="{rational(total)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
{chr(10).join(spine)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
'''
    xml.dom.minidom.parseString(doc)  # fail loudly on malformed output
    out = Path(args.output).expanduser().resolve()
    out.write_text(doc)
    seconds = float(total)
    print(f"Wrote {out}")
    print(f"{len(clips)} clips, {seconds:.1f}s at {args.fps} fps"
          + (f", music: {music['path'].name}" if music else ""))
    print("Import in Resolve: File > Import Timeline > Import AAF, EDL, XML")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output", help="output .fcpxml path")
    ap.add_argument("clips", nargs="+", help="video clips, in timeline order")
    ap.add_argument("--name", default="CooCoo Rough Cut", help="timeline name")
    ap.add_argument("--event", default="CooCoo", help="event (bin) name")
    ap.add_argument("--fps", default="25", choices=sorted(FRAME_DURATIONS),
                    help="timeline frame rate")
    ap.add_argument("--music", help="audio file for a connected music track")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
