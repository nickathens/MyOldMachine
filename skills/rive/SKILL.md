# Rive

Rive interactive vector animation: scenes built as text with the Rive CLI, rendered to MP4, ProRes 4444 with alpha, WebM or GIF; supers, logo reveals, UI screens for comps, music visuals, web pages.

Write scenes as text (RML), check them, render them with real transparency, make versions from a table, turn SVG logos and music into motion, put them on a web page, and hand them to the Rive editor. Six tested templates (lower third, logo reveal, phone screen, music visualiser, counter, toggle button) are the fastest start.

A `.riv` is not a video. It is a small program that draws: it reacts to clicks, hovers, keys and data, and plays live in web pages, apps and game engines through Rive's free, MIT licensed runtimes. This skill uses that two ways: as a **motion graphics engine that renders frames** (supers, UI screens for comps, logo reveals, data cards, music visuals) and as **interactive graphics for the web** (a live logo on a site, a button, a presentation piece).

Measured on this Mac (M4 Pro, Rive CLI 1.1.1, 25 Sep 2026) unless a line says it comes from Rive's docs.

## Pick the right tool

| You want | Use |
|---|---|
| A super, UI screen, logo sting, data card or music visual as video, often with alpha | **rive** (this) |
| The same thing live in a web page, reacting to a pointer or data | **rive** (`rive_web.py`) |
| Motion graphics written as React components, H.264 only | remotion (paid licence past 3 employees) |
| A raster logo traced to SVG, animated as HTML | logo-animate, then `rive_svg.py` to bring the SVG here |
| Generative art from code (p5.js) | algorithmic-art |
| Anything that touches camera footage: comps, keys, grades, conforms | postproduction, colorgrade |
| Diagrams | diagram |

Never guess an element or a property name: every one is a real type in `rive schema`, and a build that succeeds proves only that the names exist. The checks below catch the rest.

## Setup and health

```bash
python3 skills/rive/scripts/rive_doctor.py            # tools, flags, a sample build and render
python3 skills/rive/scripts/rive_doctor.py --web      # also the web engine (headless Chromium)
python3 skills/rive/scripts/rive_doctor.py --install  # install the CLI the supported way, then check
```

On this Mac: Rive CLI 1.1.1 (`brew install --cask rive-app/tap/rive-cli`, update with `brew upgrade --cask rive-cli`; `rive update` does not work on a brew install) and the Rive editor 0.8.5940 (`brew install --cask rive`, self-updating, needs a Rive account signed in at the screen). On Linux x64, `--install` downloads the official build, checks its SHA-256 against Rive's release manifest, and lays it out as Rive's own install.sh does: `~/.rive/versions/<version>/` with the docs and samples beside the binary (the only place `rive docs` and `rive samples` look) and `~/.rive/bin/rive` for PATH.

**Run the doctor after every CLI update.** The CLI is a technical preview (launched 11 Sep 2026, twelve releases in its first 19 days, one flag already renamed), so a new version is not trusted until the doctor passes: it re-checks every flag these scripts pass, builds and renders a bundled sample, and re-measures the gesture timing the renderer depends on. The versions these scripts were measured against are in `scripts/rivelib.py` (`TESTED_CLI_VERSIONS`).

**Linux, before you trust a picture:** on Mesa drivers (AMD, Intel, llvmpipe) the CLI's captures come back as one flat colour: its shaders fail to compile on Mesa (open upstream bug rive-app/rive-runtime#92). Every script here refuses a render whose frames are all one colour and says so. The fix and the software-rendering setup are in `references/rendering.md`, Linux. On NVIDIA's own driver it renders: measured on Ubuntu 24.04 with a GTX 970 (driver 580), where the doctor passes, the web engine included, and so do the live tests, apart from one upstream crash noted there.

## What needs an account (and whose)

Nothing in the normal loop does: building, checking, rendering, the web engine and every script here run signed out and offline. A Rive login is needed only for `--publish` (a signed `.riv`, required when a file with scripts goes on the web), `--rev` (an editor file), `rive push` / `rive pull`, and `rive create --from-remote-file`.

The CLI keeps its login at `$XDG_CONFIG_HOME/rive/app.rive.cli/oauth.prod` (measured). One OS account hosts several people here, so the scripts point `XDG_CONFIG_HOME` at the session's private folder (`$JARVIS_USER_DIR/rive/config`): each person's Rive account stays theirs. Log in through the same environment, never with a bare `rive login`:

```bash
mkdir -p "$JARVIS_USER_DIR/rive/config"
XDG_CONFIG_HOME="$JARVIS_USER_DIR/rive/config" rive login     # opens a browser: once, at the Mac's screen
python3 skills/rive/scripts/rive_doctor.py --account           # who this session is logged in as
```

Plans (rive.app/pricing, read 25 Sep 2026; Rive's own docs page quotes different Voyager numbers, the live page wins): Free covers the editor with 3 collaborative files but **no `.riv` export, no video export and no `.rev` backup from the editor**. Cadet (about 9 USD a seat a month yearly, 17 monthly) unlocks exports. The CLI is free with no limits during its technical preview; Rive has said publish calls will likely be limited afterwards. Local builds and renders are not publish calls.

## The build loop

Rive's own advice, and it held up here: build outside-in in passes, check after every pass, patch rather than regenerate.

1. **Look things up, never guess.** `rive docs` (42 topics written for agents, version-matched to the installed CLI), `rive docs <topic>`, `rive schema <Type>`, `rive schema --search <text>`, `rive schema <Type> --animatable` / `--bindable`. 417 types; a misspelled element or attribute is a build error that names the right one.
2. **Pass 1, the wireframe:** every region as a filled box at its final size. Then structure, then text and content, then art and motion.
3. **After every pass run the gate:**

```bash
python3 skills/rive/scripts/rive_check.py <project> [--at 2.0] [--sizes 390x844,1280x720] \
    [--interaction click@X,Y] [--probe-binds]
```

It runs the three checks Rive says are not interchangeable, plus what none of them sees:

| Check | Catches | Blind to |
|---|---|---|
| `rive <dir> --verify` | syntax, Luau type errors, shaders, a `.riv` that would not re-import | anything that only shows when it runs or draws |
| `rive <dir> --test` (only when the project has Luau) | a failing `Tests` case, by name, line and message | whatever the tests do not assert |
| `rive inspect --json` problems | bind paths, missing references, state machine wiring, overlapping states | Luau, appearance |
| a capture | appearance | nothing visual |
| lint (this script) | the silent failures: units left undefined, cubic keys with no curve, unlabelled text styles, system fonts, keyboard phase masks, script inputs matching no field, feather in a fill | |
| `--interaction` | a control that does nothing, or only works one way | |
| `--probe-binds` | a view model property that drives nothing on screen | |

`--probe-binds` and `--interaction` found three real bugs while this skill was built: a bar bound to width instead of height (they grew sideways), a progress ring hidden under its own track (first sibling draws on top), and last session's demo play button that could start but never stop.

Start from a template when one is close:

```bash
python3 skills/rive/scripts/rive_new.py list
python3 skills/rive/scripts/rive_new.py lower_third ~/work/supers --set name="Ada Lovelace" --set title="Analyst"
```

| Template | What it is | Shows how to |
|---|---|---|
| `lower_third` | name and title on a plate, typed in, transparent, 5 s | hug layouts, text modifiers, colour binds, alpha |
| `logo_reveal` | a mark that draws on, fills and settles, 3 s | GroupEffect trim, feathered glow, `rive_svg.py --reveal` |
| `ui_screen` | phone screen: heading, search that types, results that scroll, 6 s | a component list from data, clipping viewport, caret in a row |
| `visualizer` | 12 spectrum bars, ring, glow, aura from a real track | converters, many binds, `rive_audio.py` curves |
| `counter` | a number counting up and a ring filling to value / goal | converter chains, formulas, tabular figures |
| `button` | hover, press, toggle, keyboard, cursor, screen-reader label | three state machine layers, listeners, focus |

## Rendering

```bash
R="python3 skills/rive/scripts/rive_render.py"
$R <project> -o still.png --at 1.5
$R <project> -o intro.mp4 --duration 6 --fps 30
$R <project> -o super.mov --alpha --duration 5 --fps 25 --data "name=Ada Lovelace"   # ProRes 4444
$R <project> -o ui.mov --size 1170x2532 --duration 6 --timeline typing.json            # 3x a phone
$R <project> -o viz.mp4 --fps 25 --duration 30 --curves curves.json --audio track.wav
$R hero.riv   -o hero.webm --alpha --duration 4 --state-machine Main                    # web engine
```

Outputs by extension: `.png` (one frame), a `%05d` pattern or a folder (PNG sequence, RGBA with `--alpha`), `.mp4` (H.264, 4:2:0, CRF 14), `.mov` (ProRes 422 HQ, or 4444 with alpha), `.webm` (VP9, alpha supported), `.gif`. Files are tagged BT.709. Every render writes `<output>.render.json`: engine and versions, the exact CLI arguments of the first, middle and last frame, hashes, warnings, and the checks below.

**Two engines, chosen by what you hand it.**

| | cli (a project folder) | web (a `.riv` file) |
|---|---|---|
| Renderer | Rive's own (Metal on a Mac, OpenGL through EGL on Linux), the same as the previewer | Rive's web runtime (@rive-app/webgl2 2.43.1) on SwiftShader |
| Speed, 1080p | 58-64 frames a second with 8-12 workers | about 12 fps including start-up |
| Transparency | solved from a black and a white pass | real, one pass |
| Scripts (Luau) | run, unsigned | rejected unless signed with `--publish` |
| Data mid-shot | per-frame values (level semantics) | changes at the exact time |
| Look | the reference | same content, edges anti-aliased differently |

Interiors match between engines; measured edge differences were a mean of 0.1-0.2 codes with under 0.5% of pixels past 8 codes. Deliver from the CLI engine when you have the project.

**Time.** Frame *k* is scene time *k/fps*, captured by its own headless run from zero, in the CLI's fixed 1/60 s steps plus one shorter step. Cost does not grow with scene time (a capture at 300 s costs the same 0.11 s as one at 1 s), so long renders scale linearly. **Frame 0 is captured at 0.01 ms, never 0**: with no advance, or `--advance=0`, the capture is the authored rest pose before the entry animation (a text keyed invisible on frame 0 still shows).

**Gestures** replay in order and cost scene time, measured with a per-frame data dump: a click is 3 frames (move, press, release; the listener fires on the release), move/down/up/exit 1, a key press 2, a drag of *s* steps *s*+3, an accessibility action 2. `--click 2.0@560,560`, `--drag 1.0@200,600>200,200:30`, `--key 3.0@down` start the gesture at that time; a `--timeline` JSON holds any mix plus data and curves (format in `references/rendering.md`). Typing into a text field cannot be simulated (keys are not text input), so on-screen typing is animated: `rive_recipes.py typing`.

**Data.** `--data PATH=VALUE` holds for the whole render. `--data-curve PATH=FILE[:KEY][@LO:HI]` and `--curves FILE` give each frame its own value. On the CLI engine that value is set before the frame's run, so it behaves as a level: the scene shows the value it is given. A transition triggered by the value changing starts at time zero of the run instead of when it changed; use the web engine or author the change into the file for that. A `DataConverterInterpolator` only eases changes made after binding, so a count-up render needs a curve (`rive_recipes.py count`), while a live page eases on its own.

**Size.** Default is the artboard's own size. `--size` at the same aspect scales it (use this for 2x/3x phone comps and 4K); a different aspect reflows a responsive artboard and pins a fixed one top-left unless you pass `--fit contain|cover|fill`. With `--alpha`, a fit that letterboxes is refused, because the CLI's backdrop would fill the bars.

**Transparency** (`--alpha`) needs an artboard without an opaque background fill. The CLI engine renders the scene twice, over black and over white, and solves alpha and straight colour per pixel with ffmpeg; then it re-renders three frames normally and checks the solved result recomposites onto them. Measured on glows, gradients to clear, a 50% fill and text: within 2 codes (mean 0.10). Anything that is not plain source-over (a blend mode over transparency) fails that check, and the render stops with the frame and the error rather than write it; `--allow-bad-alpha` writes it anyway, the numbers in a warning.

**Refused renders:** a project that does not build, an artboard whose sampled frames are all one flat colour (with the reason: nothing drawn, only background, or the Linux bug), a data path that does not exist (the CLI exits 1). Opaque output lays transparent areas over `--background` (default black) instead of the CLI's grey `#1D1D1D`.

## Versions from a table

```bash
python3 skills/rive/scripts/rive_versions.py <project> -t names.csv -o out/ --ext mov --alpha --duration 5 --fps 25
```

Columns are view model property paths; the `slug` (or `name`, or first) column names each file; columns that match no property are reported and ignored. Writes `index.json` (row, file, SHA-256) and `sheet.png`. Anything after `--` goes to `rive_render.py`.

## Logos and SVG

The editor imports SVG; the CLI cannot (SVG assets are editor-only and stripped from builds). `rive_svg.py` converts paths (every command, arcs included), rects, circles, ellipses, lines, polylines, polygons and `<use>`, bakes transforms into the points, keeps fills, strokes, dashes, opacity, fill rule and linear/radial gradients, and reverses the paint order (SVG paints the last element on top, Rive the first). Checked against Chromium drawing the same SVG: mean 0.3 codes of difference, edges only.

```bash
python3 skills/rive/scripts/rive_svg.py logo.svg -o logo.rml --fit 600 --center 960,540   # a fragment
python3 skills/rive/scripts/rive_svg.py logo.svg --project ~/work/sting --size 1920x1080 --reveal --ink FFF2F2F2
```

`--reveal` builds the `logo_reveal` animation around any mark. Text in an SVG is not converted: outline it first (a raster logo goes through the logo-animate skill's vectoriser first).

## Fonts

Rive ships no fonts and has no fallback: a text style without a font file renders nothing, and building a `.riv` embeds the font, which redistributes it. Never use one from the system font folders: `rive_check.py` fails a project whose font file is in one, but it cannot recognise a copy, so a copied system font is on you.

```bash
python3 skills/rive/scripts/rive_fonts.py add "Space Grotesk" --project ~/work/intro --weight 700
python3 skills/rive/scripts/rive_fonts.py info font.ttf       # names, axes, instances, the default-weight trap
```

`add` fetches from Google Fonts' own repository with its licence file, and prints the `FontAsset` and a `TextStylePaint` with `familyName`/`styleName` (the editor labels its menus from these) and the packed axis tags. Variable fonts render their default instance when no weight axis is set, and that default is often light (Space Grotesk 300, Montserrat 100), so without `--weight` the snippet sets 400 and says so.

## Music

Rive plays sound but cannot analyse it, and captures are silent, so a music visual is measured first and driven as data:

```bash
python3 skills/rive/scripts/rive_audio.py track.wav -o curves.json --fps 25 --log-bands 12
python3 skills/rive/scripts/rive_render.py visualizer -o viz.mp4 --fps 25 --duration 30 --curves curves.json --audio track.wav
```

Curves per video frame, each 0..1: `level` (RMS with attack/release), `peak`, `onset`, `low`/`mid`/`high`, and `b1..bN` pitch-spaced bands. `--curves` binds every curve named like a view model property. The track is muxed back in, trimmed to the video (never `-shortest`).

## Motion helpers

`rive_recipes.py typing "text" --path query -o typing.json` (a seeded human typing rhythm as a data curve; a caret in a hug row follows it), `rive_recipes.py keys "text" --object 0:34` (the same baked into the file as `KeyFrameString` keys, for a page), `rive_recipes.py count --path value --to 1250 -o count.json` (an eased count-up). More patterns, all used in the templates, are in `references/motion_recipes.md`.

## The web

```bash
W="python3 skills/rive/scripts/rive_web.py"
$W info file.riv                                    # artboards, timelines, state machines, view models, assets
$W page file.riv -o site/ --title "Intro" --state-machine Main [--controls] [--single-file]
$W verify site/index.html --click 560,560          # loads it like a visitor, clicks, reports
```

Pages use @rive-app/webgl2 (the Rive Renderer; vector feathering only renders there, not in the canvas package), pinned in `scripts/web_runtime.json` and fetched with an integrity check into a user cache, never into the repo. `--controls` adds a live panel for the view model, the quickest way to show what the data does; `--single-file` inlines everything (about 3.6 MB). **Web runtimes reject unsigned scripts**, and nothing local warns you: a file with Luau that goes on the web must be built with `rive <dir> --publish` (login). Files without scripts are fine unsigned. The web runtime cannot see which state machine is the default, and with none named it plays the first timeline, so listeners and binds do nothing. A page therefore plays the artboard's first state machine unless `--state-machine` names another, and says so in the console when there are several.

## Handing work to the editor

`rive <dir> --once --rev=file.rev` (login) writes a file a designer opens in the editor; `rive push` sends the project to a file in the account (every push is a revision; the push is authoritative over editor edits); `rive pull --yes` brings editor changes back; `rive create <dir> --from-rev=file.rev` turns any editor file into an RML project, the best way to learn how a designer built something. Before handing over, give every artboard and state an `x`/`y` (they stack at the origin otherwise; `inspect` warns) and every text style `familyName`/`styleName`. The editor, its AI agent and its MCP connection (untested here) are in `references/editor.md`.

## The silent failures that matter most

Every one of these builds clean. The full list, with how to detect each, is in `references/rml.md`.

- **No `defaultStateMachineId`**: binds never apply and pointer input never arrives, but the first timeline still plays, so the file looks alive.
- **The first sibling draws on top** (the reverse of HTML and SVG); inside one shape the last paint is on top.
- Rotation is **radians**; timeline duration is **frames** (fps 60 by default); transition duration is **milliseconds**; `exitTimeIsPercetange` is misspelled in the format and must be written that way.
- The keyframe element must match the property type (`KeyFrameDouble`, `KeyFrameColor`, `KeyFrameString`, `KeyFrameId`...), and `cubic` needs a `CubicEaseInterpolator` child; the ease belongs on the segment's **first** key; `hold` is the default.
- A `LayoutComponent` needs its style **nested and named by `styleId`**; padding, gaps and insets need `*UnitsValue="points"`; a shape inside a fixed or fill box is stretched to it unless wrapped in a `Node` or given a `LayoutParticipant`.
- Text needs a font file, a style with a `Fill`, and a run with `styleId`; a variable font renders its default instance.
- `Feather` inside a `Fill` renders nothing; feather a `Stroke`.
- Bind the property that exists on the target (`width` is on `Rectangle`, key 20; height 21; not on the `Shape`); a number cannot drive text without `DataConverterToString`.
- Every listener under the pointer fires; a transparent fill still catches clicks; author both directions of a toggle.

## Rules on this machine

- Renders and captures run on a private copy (`rive_skill_*` folders from `mkdtemp`), removed by exact path when done. A project is only read, except that `rive_check.py` writes its pictures to `<project>/build/check` unless `--out` says where. Nothing here kills processes by name, and there is no stop hook.
- Analytics are off per call (`RIVE_ANALYTICS=off`); consent was never given, so the stored setting is left alone.
- The repo is public: templates and examples use neutral names and colours. Client work lives in the client's project folder, not here.
- `rive <dir>` with no flags opens the live previewer window, which only helps someone at the Mac's screen; start it in the background once if someone is watching the screen, never from a headless session.

## References

- `references/rml.md`: how RML works, the property keys used most, patterns for layout, text, data, converters and state machines, and every silent failure with its detection.
- `references/rendering.md`: engines in depth, timing and gesture costs, the timeline JSON, alpha, encoding and colour tags, Linux, measured numbers.
- `references/motion_recipes.md`: easing presets, type-on and cascades, draw-on, glows and shadows, seamless loops, counters, typing with a caret, lists, scrolling.
- `references/web.md`: runtime packages and what each supports, the page builder, data from JavaScript, signing, embedding.
- `references/editor.md`: the desktop app, plans and exports, push/pull, the AI agent, MCP.
- `references/use_cases.md`: where Rive fits film, TV, broadcast, music and client work here, and where it does not.
- `references/sources.md`: what was read and measured, the GitHub landscape with licences, versions.
