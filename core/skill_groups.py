"""Presentation layer for the skill menu the model is handed each turn.

Both menu builders (core.skill_loader.SkillManager.build_context, and the
per-user core.skill_overlay.build_user_context that bot.py calls) list every
skill as one line. Sorted by name, that put related skills far apart, cut 19
lines mid-word at the loader's 200 characters, and said nothing about which
sibling to open when several overlap. This module adds the three things that
fix it, and nothing else:

  GROUPS          thirteen domains, in print order, each naming its skills.
                  Every shared skill sits in exactly one group (enforced by
                  tests/test_skill_groups.py), so related skills print together.
  SUMMARIES       a curated one-line summary for each skill whose SKILL.md
                  opening paragraph runs past the 200-char cut, so its line
                  reads as a finished sentence. Each is grounded in that
                  skill's own opening paragraph.
  DISAMBIGUATION  one "Pick:" line per group whose siblings overlap, saying
                  which skill does what. Printed under the group header.
  SEE_ALSO        the one sentence of a curated summary that sends the reader
                  to another skill, kept apart so it can be left out.

Pointers to other skills are written {skill}. A skill can be off a user's
menu: blocked_skills in users.json, an "off" override, or disabled in its
config.json. A Pick clause or SEE_ALSO sentence that points at it is then
dropped, so the menu never names a skill it does not list. Hiding is not a
lock: every turn runs as the same OS user (AGENTS.md, soft multi-user), and
the menu only decides what the model is shown. A "name-only" skill keeps its
name but gives up its description, and a Pick clause is one, so its clause
goes too. Most skill names are also plain words ("research", "charts",
"calendar"), so word matching cannot tell a pointer from prose; the braces are
the signal, and they never reach the menu.

This changes menu wording only, never a skill's behaviour, scripts or
SKILL.md. A skill missing from GROUPS still prints, under "Other", so nothing
is ever left off, and the test fails so the gap gets a deliberate home.
"""
from __future__ import annotations

import re

HEADER_NOTE = ('Skills are grouped by domain. Each group\'s "Pick:" line says '
               "which sibling to reach for when several overlap.")

# ── the thirteen domains, in print order ───────────────────────────────────
GROUPS: list[tuple[str, list[str]]] = [
    ("Music", [
        "music-theory", "algorithmic-composition", "midi", "midi-to-audio",
        "audio-to-midi", "sheet-music",
    ]),
    ("Audio & Voice", [
        "sound-design", "stems", "audio-editing", "audio-analysis",
        "text-to-speech", "voice",
    ]),
    ("Film & Video", [
        "screenplay", "video-editing", "davinci-resolve", "blender-video",
        "colorgrade", "postproduction", "remotion", "rive", "adobe", "watch",
    ]),
    ("Image", [
        "image-gen", "image-editing", "background-removal", "upscale",
        "gimp", "inkscape", "color-palette", "mascot",
    ]),
    ("Game & 3D", [
        "blender", "godot", "img2threejs", "sprite-gen",
    ]),
    ("Web & Design", [
        "web-build", "impeccable", "algorithmic-art", "presentations",
        "diagram", "logo-animate", "icon-gen", "font-tools", "lighthouse",
    ]),
    ("Docs & Writing", [
        "docs", "pdf", "ocr", "spreadsheet", "summarize", "notes", "mempalace",
    ]),
    ("Code & Dev", [
        "coding", "git", "code-scaffold", "database", "docker-services",
        "mcp-builder", "api-test", "regex", "workflow",
    ]),
    ("Security", [
        "security-audit",
    ]),
    ("Research & Data", [
        "research", "last30days", "trading", "price-monitor", "scraper",
        "rss", "charts",
    ]),
    ("Comms & Office", [
        "email", "calendar", "google-workspace", "translate", "clipboard",
        "bookmarks", "qrcode", "weather",
    ]),
    ("Greece", [
        "greek-engineer", "greek-law",
    ]),
    ("System & Files", [
        "browser", "media", "downloads", "torrent", "cloud-sync",
        "compress", "vpn", "screenshot-diff",
    ]),
]

# ── curated summaries (the 19 openings the 200-char cut was mangling) ───────
# A pointer to an easily confused sibling goes in SEE_ALSO, never here, so it
# can be dropped for a user who cannot see that sibling.
SUMMARIES: dict[str, str] = {
    "adobe":
        "Photoshop, After Effects, Illustrator and the rest of Creative Cloud "
        "on macOS: installing them, and what can be driven without a human at "
        "the screen.",
    "algorithmic-art":
        "Generative visual art with p5.js, output as a self-contained HTML page "
        "you can render to PNG, capture as video, or embed. For generative "
        "posters and code-as-art, not charts or diagrams.",
    "colorgrade":
        "Colour grade a whole video unattended: returns the graded file, a "
        "before/after contact sheet, a consistency report, and one .cube LUT per "
        "shot for Resolve.",
    "davinci-resolve":
        "Drive DaVinci Resolve for real editorial work: build timelines from "
        "media, hand off rough cuts, queue renders.",
    "google-workspace":
        "Drive, Docs, Sheets and Slides (plus Tasks, Forms, Keep, Meet) via "
        "Google's gws CLI, the only skill that reads or writes them.",
    "greek-engineer":
        "Engineering practice in Greece across civil, architect, electrical, "
        "mechanical and environmental: regulations plus self-checking calculation "
        "engines that show their work and state limits.",
    "greek-law":
        "Greek law research, drafting and explanation from primary Greek sources, "
        "citations disciplined. Plain Greek for a person, pleadings checked for "
        "αοριστία for a lawyer. Not legal advice.",
    "img2threejs":
        "Rebuild a reference image of an object or character as animation-ready "
        "procedural Three.js (TypeScript) code, not photogrammetry.",
    "last30days":
        "What people actually said in the last ~30 days across Reddit, YouTube, "
        "Hacker News, Polymarket and GitHub, engagement-ranked. Recent chatter and "
        "buzz.",
    "logo-animate":
        "Vectorize a raster logo to a clean SVG, then choreograph a brand "
        "animation (logo reveal, splash, intro, hover or idle state) as standalone "
        "HTML, GIF or MP4.",
    "mascot":
        "Design one very simplified cute mascot, brand character or app-icon "
        "creature as a square image: thick rounded forms, two colours plus a solid "
        "background, readable at 32x32.",
    "mcp-builder":
        "Build Model Context Protocol (MCP) servers: expose tools, resources and "
        "prompts to MCP-aware clients (Claude Desktop, Claude Code, Cursor, OpenAI "
        "Agents, custom hosts).",
    "postproduction":
        "The whole back end of a film: ingest, repair, resolution, conform, "
        "compositing, colour, supers, subtitles, sound, mastering, delivery, "
        "archive. An ordered spine with a gate per step.",
    "presentations":
        "Build presentation deliverables: treatments, lookbooks, pitch decks, "
        "proposals, bid microsites, scrollytelling, brand and personal sites.",
    "remotion":
        "Programmatic motion graphics: build videos as React components rendered "
        "to MP4 (animated titles, lower thirds, charts, kinetic typography, data "
        "builds for decks).",
    "security-audit":
        "Defensive source-code security audit of a codebase you own or are "
        "authorized to review: hunts for exploitable vulnerabilities and writes "
        "a verified, machine-readable findings report.",
    "trading":
        "Market research, technical analysis, screening and backtesting for "
        "stocks, ETFs and crypto. Free data (yfinance, ccxt, TradingView), TA-Lib "
        "indicators, backtesting.py, quantstats tearsheets.",
    "upscale":
        "Upscale images and video: free locally with Real-ESRGAN, or paid via "
        "Magnific behind a quote. A measured workflow for choosing how to "
        "upscale, not just a button.",
    "watch":
        "Watch any video: downloads with yt-dlp, extracts frames with ffmpeg and "
        "pulls a timestamped transcript (captions first, Whisper as the "
        "fallback). Use to analyse or summarise a video.",
}

# ── pointer sentences split off curated summaries ───────────────────────────
# Rendered after the summary only when every skill it names is on the menu.
SEE_ALSO: dict[str, str] = {
    "davinci-resolve":
        "For quick mechanical cuts use {video-editing}.",
    "google-workspace":
        "For mail and calendar use the {email} and {calendar} skills.",
    "img2threejs":
        "For hand meshes use {blender}; for a flat image use {image-gen}.",
    "last30days":
        "For broad digging use {research}.",
    "presentations":
        "Video reels live in {video-editing}.",
}

# ── one "Pick:" line per group where siblings overlap ───────────────────────
# Each clause starts with the skill it is about, marked. A clause that points
# at a skill missing from the user's menu is dropped; the rest still print.
DISAMBIGUATION: dict[str, list[str]] = {
    "Music": [
        "{music-theory} analyses existing music (chords/key)",
        "{algorithmic-composition} generates new MIDI",
        "{midi} edits existing MIDI (transpose, tempo, merge)",
        "{sheet-music} engraves MIDI to a printable score",
        "{midi-to-audio} renders MIDI to sound",
        "{audio-to-midi} transcribes audio to MIDI",
    ],
    "Audio & Voice": [
        "{stems} splits a mix into parts",
        "{audio-editing} cuts/fades/converts",
        "{audio-analysis} measures BPM/key/loudness",
        "{voice} transcribes speech IN",
        "{text-to-speech} speaks text OUT",
        "{sound-design} synthesises new sounds",
    ],
    "Film & Video": [
        "{video-editing} for cuts, resize, text and audio",
        "{image-gen} generates a NEW clip with AI",
        "{davinci-resolve} when the result must live in a Resolve project",
        "{postproduction} for subtitles, supers, conform, mastering and delivery",
        "{colorgrade} for a whole-video colour pass",
        "{blender-video} for VSE edits",
        "{remotion} for motion graphics built in React",
        "{rive} for vector motion graphics (lower thirds, logo stings, UI "
        "screens) with alpha, or live on a web page",
        "{adobe} renders After Effects templates (macOS)",
        "{watch} to analyse an existing video",
    ],
    "Image": [
        "{image-gen} makes NEW images, video, 3D and audio with AI, from text "
        "or a reference",
        "{image-editing} edits or recolours REAL pixels",
        "{background-removal} cuts out a subject",
        "{upscale} raises resolution",
        "{gimp}/{inkscape} for scripted raster/vector work",
        "{mascot} for a brand character",
        "{ocr} pulls text out of an image",
    ],
    "Game & 3D": [
        "{blender} for hand-built meshes and renders",
        "{godot} for game logic and scenes",
        "{img2threejs} rebuilds a reference image as Three.js code",
        "{sprite-gen} for 2D game sprites",
    ],
    "Web & Design": [
        "{web-build} for static sites (Hugo)",
        "{impeccable} for high-end frontend design polish",
        "{presentations} for decks, treatments and microsites",
        "{diagram} for system maps",
        "{algorithmic-art} for generative visuals",
        "{logo-animate}, {icon-gen} and {font-tools} for brand assets",
        "{lighthouse} to audit a site",
    ],
    "Docs & Writing": [
        "{docs} converts files to markdown",
        "{pdf} manipulates PDFs",
        "{ocr} pulls text from an image",
        "{spreadsheet} for Excel/ODS",
        "{summarize} condenses a URL",
        "{notes} for your own knowledge base",
        "{mempalace} searches your past conversations",
    ],
    "Code & Dev": [
        "{coding} is the build/debug methodology",
        "{git} for version control",
        "{code-scaffold} for boilerplate",
        "{database} for SQLite",
        "{docker-services} for containers",
        "{mcp-builder} for MCP servers",
        "{api-test} for HTTP",
        "{regex} for patterns",
        "{workflow} for YAML pipelines",
    ],
    "Research & Data": [
        "{research} for broad multi-source digging",
        "{last30days} for recent (~30 day) social chatter",
        "{trading} for markets and technical analysis",
        "{price-monitor} to track one price over time",
        "{scraper} for a JS-rendered page",
        "{rss} for feeds",
        "{charts} for terminal bar charts",
    ],
    "Comms & Office": [
        "{email} for Gmail",
        "{calendar} for Google Calendar",
        "{google-workspace} for Drive/Docs/Sheets/Slides",
        "{translate} for machine translation",
        "{clipboard} bridges phone and desktop copy",
    ],
    "Greece": [
        "{greek-law} for law, contracts and legal drafting",
        "{greek-engineer} for engineering practice and building calculations",
    ],
    "System & Files": [
        "{browser} for a persistent headless browser session",
        "{media} for screenshots, and screen recording on Linux",
        "{downloads} and {torrent} to fetch files",
        "{cloud-sync} for rclone",
        "{compress} for archives",
        "{vpn} for ProtonVPN",
        "{screenshot-diff} to compare two images",
    ],
}

_POINTER = re.compile(r"\{([a-z0-9_-]+)\}")


def refs(text: str) -> set[str]:
    """The skills a menu text points at: the names written in {braces}."""
    return set(_POINTER.findall(text))


def plain(text: str) -> str:
    """The text as the menu prints it, braces removed."""
    return _POINTER.sub(r"\1", text)


def pick_line(group: str, shown) -> str | None:
    """The group's "Pick:" line, minus clauses that point at a skill not in
    `shown`. None when nothing is left."""
    kept = [plain(c) for c in DISAMBIGUATION.get(group, ()) if refs(c) <= shown]
    return f"Pick: {'; '.join(kept)}." if kept else None


def summary(name: str, shown=None) -> str | None:
    """The curated summary, with its SEE_ALSO sentence when every skill that
    sentence names is in `shown` (None: no filter). None if not curated."""
    text = SUMMARIES.get(name)
    if text is None:
        return None
    see = SEE_ALSO.get(name)
    if see and (shown is None or refs(see) <= shown):
        text = f"{text} {plain(see)}"
    return text


def group_of() -> dict[str, str]:
    """skill name -> group name."""
    return {skill: name for name, skills in GROUPS for skill in skills}


def grouped(lines: dict[str, str], described=None) -> list[str]:
    """The menu body for finished skill lines (name -> its bullet line).

    One "### Group" block per group with a skill in `lines`, in GROUPS order,
    led by its Pick line. A Pick clause prints only when every skill it names
    is in `described` (default: every skill in `lines`; never wider). Skills
    in no group follow under "Other", so nothing is left off."""
    shown = set(lines)
    described = shown if described is None else set(described) & shown
    out: list[str] = []
    placed: set[str] = set()
    for group, names in GROUPS:
        members = [n for n in names if n in lines]
        if not members:
            continue
        out.append(f"### {group}")
        pick = pick_line(group, described)
        if pick:
            out.append(pick)
        out.extend(lines[n] for n in members)
        out.append("")
        placed.update(members)
    leftover = [n for n in lines if n not in placed]
    if leftover:
        out.append("### Other")
        out.extend(lines[n] for n in leftover)
        out.append("")
    return out
