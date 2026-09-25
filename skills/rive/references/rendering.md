# Rendering Rive to frames and video

`rive_render.py` is the only renderer you need; this page is how it works, why, and the numbers behind its defaults. Everything here was measured on the Mac mini (M4 Pro, 12 cores, 24 GB) with Rive CLI 1.1.1, @rive-app/webgl2 2.43.1 and ffmpeg 9.0.2 on 25 Sep 2026.

## Why frames and not the editor's video export

The editor exports MP4, GIF, WebM and image sequences through Rive's Cloud Renderer, on paid plans only, with no ProRes and no alpha mentioned. The CLI has no video export at all, but it can capture any frame of a project headlessly (`--screenshot`) after replaying time, pointer, keys and data. One capture per frame, in parallel, then ffmpeg: that is local, free, frame-exact, and can carry alpha.

## The CLI engine

For each frame the renderer runs `rive <copy-of-project> --quiet --screenshot=<png> [--viewport=WxH --fit=F] [--artboard=A] <frame arguments>`. The frame arguments are the whole timeline up to that frame's time, flattened (see `scripts/rivetimeline.py`), for example frame 45 of a 30 fps render with a click at 1.0 s:

```
--data=title=Rendered --advance=60 --pointer=move@560,560 --pointer=down@560,560 --pointer=up@560,560 --advance=27
```

Measured:

| | |
|---|---|
| one capture, 1080p | ~0.11 s wall, about 57 MB resident |
| one capture, 4K (`--viewport=3840x2160 --fit=contain`) | ~0.19 s, about 107 MB |
| cost against scene time | flat: a capture at 300 s costs the same as at 1 s |
| throughput, 1080p | 10.6 fps with 1 worker, 34 with 4, 58 with 8, 61 with 10, 64 with 12 |
| throughput, 4K | 32 fps with 8 workers |
| determinism | the same arguments give a byte-identical PNG |

Default workers: CPU count minus two, capped at ten. The project is copied into a private scratch folder first (paths that leave the project are rewritten to absolute), so parallel captures never write into your `build/` and alpha passes can edit the copy.

### Time

- The CLI steps scene time in fixed 1/60 s frames. A span that is a whole number of frames is passed as a frame count (`--advance=60`), anything else as milliseconds (`--advance=41.6667ms`), which the CLI runs as whole frames plus one shorter one. So 30 and 60 fps renders step exactly like the live player; 24 and 25 fps add one short step per frame.
- **Frame 0 is captured at 0.01 ms.** With no `--advance`, or `--advance=0`, a capture is the authored rest pose: the state machine has not applied its entry animation (a text keyed to opacity 0 on frame 0 measured fully visible at advance 0, invisible at 1 ms). Data binds do apply without an advance.
- `--start S` offsets the first frame's scene time, for rendering the middle of a long animation.

### Gestures

Each primitive costs scene time, measured with `--data-dump-every=1`:

| Gesture | Frames of 1/60 s |
|---|---|
| `--pointer=move/down/up/exit` | 1 each |
| a click (move, press, release; the listener fires on the release) | 3 |
| a drag with *s* steps (move, press, *s* moves, release) | *s* + 3 |
| `--key=K` (press and release) | 2 |
| `--key=K:down` (one phase) | 1 |
| `--gamepad=...` | 1 |
| `--semantic-action=tap@Label` | 2 |

`rive_render.py` starts each gesture at the time you give and, for a frame that falls inside one, includes only the steps already finished, so a press shows pressed mid-click and a drag shows mid-drag. Two gestures closer than their cost run back to back; the frames after them are captured that much late and the report lists them (`late_frames`). `rive_doctor.py` re-measures the click cost after a CLI update.

Coordinates are artboard coordinates, not output pixels, whatever `--size` is.

Keys are not text input: `--key=h` into a focused TextInput types nothing (measured), and no runtime supports TextInput yet (Rive's feature table, Sep 2026). Animate typing instead (`rive_recipes.py typing`).

### Data over time

- `--data PATH=VALUE` holds for the whole render. Paths are property names from the artboard's bound instance, `/` into nested view models (`card/price=12`). A path that does not exist, or a value the type cannot take (`playing=maybe`), makes the CLI exit 1 and the render stops.
- `--data-curve PATH=FILE[:KEY][@LO:HI]` gives each frame its own value; `--curves FILE` binds every curve in a curves file whose name is a property. `@LO:HI` maps 0..1 onto a range.
- **Level semantics on the CLI engine:** a frame's data is set before its run starts, so the frame shows the scene with that value. A transition that fires when a value changes starts at time zero of that run, not when the value changed, and an interpolating converter never animates. For event-style changes use the web engine (it sets values at their exact time) or author the change into the file.

### The timeline file

```json
{
  "events": [
    {"at": 0.5, "click": [560, 560]},
    {"at": 1.2, "drag": [200, 600, 200, 200], "steps": 30},
    {"at": 2.0, "key": "down"},
    {"at": 2.4, "key": "tab", "phase": "down", "mods": ["shift"]},
    {"at": 3.0, "semantic": "tap@Play"},
    {"at": 3.5, "gamepad": "button@south:down"},
    {"at": 4.0, "move": [100, 100]}
  ],
  "data": {"title": "Hello"},
  "curves": {
    "query": {"keys": [[0, ""], [0.9, "h"], [1.0, "ha"]], "interpolation": "hold"},
    "level": {"file": "curves.json", "key": "level", "range": [0, 100]},
    "bass": "curves.json:low"
  }
}
```

A curve is inline (`keys` with `linear` or `hold` interpolation, or `fps` + `values`) or points at a curves file. Numbers interpolate; strings, booleans and colours hold. `rive_recipes.py` writes typing and count curves in this format; `rive_audio.py` writes curves files.

### Size and fit

With no `--size` the output is the artboard's size. With `--size` at the artboard's aspect the scene is scaled (`--fit=contain`), which is how a 390x844 phone screen becomes a crisp 1170x2532 comp: vectors re-render at the new size, nothing is upscaled. At a different aspect the default is `--fit=layout`: a responsive artboard reflows, a fixed one renders at its own size pinned top-left. `contain` letterboxes, `cover` crops, `fill` stretches.

## Transparency

A capture is always opaque: alpha is 255 everywhere, and where the artboard draws nothing you get the CLI's backdrop `#1D1D1D`. `--alpha` therefore renders twice, with a solid black and then a solid white fill added as the artboard's first child (it paints under everything), and solves each pixel:

```
alpha = 255 - (white - black)          colour = black * 255 / alpha
```

in one ffmpeg graph (`ALPHA_GRAPH` in `rive_render.py`, two `blend` filters, `extractplanes`, `alphamerge`). Measured on a feathered glow, a radial gradient to clear, a 50% fill and text: per-channel alpha estimates agree within 2 codes (plain source-over), and the solved RGBA laid back over `#1D1D1D` matches a normal capture within 2.03 codes (mean 0.10). **ffmpeg's `unpremultiply=inplace=1` after `alphamerge` left the colour premultiplied here (errors up to 65 codes)**; the division is done with `blend` for that reason.

Every alpha render then captures three frames normally and checks that recomposite (`alpha_check` in the report). A scene that is not plain source-over over transparency (a blend mode on semi-transparent content) cannot be solved this way: the check fails and the render stops, naming each failing frame and its error (measured: a difference blend over a 50% fill, 143 codes off). The limit is 8 codes, or 40 dB PSNR where numpy is missing. `--allow-bad-alpha` writes the file anyway with the numbers in a warning, for a look by eye. Requirements: no opaque artboard background (refused), and no letterbox (refused with `contain`/`fit-width`/`fit-height`/`none`/`scale-down` at another aspect). The alpha is 8-bit, from 8-bit captures.

The web engine has real alpha in one pass (a transparent canvas read back as PNG).

## The web engine

For a `.riv` the CLI cannot open (there is no command that renders a bare `.riv`), `rive_render.py` drives Rive's low-level web runtime in headless Chromium: `RuntimeLoader.awaitInstance()`, `load()`, an artboard and a `StateMachineInstance`, then for each frame `advanceAndApply` in the same 1/60 s steps, pointer calls at the same times the CLI would make them, `artboard.draw()`, and `canvas.toDataURL()` **in the same task** (the WebGL drawing buffer is not preserved; read later it is empty). `scripts/web/engine.html` is that page; `scripts/riveweb.py` serves it from a private folder on a random localhost port.

- Runtime: `@rive-app/webgl2` 2.43.1, the Rive Renderer, pinned with its npm integrity hash in `scripts/web_runtime.json`, fetched once into `~/Library/Caches/rive-skill` (macOS) or `~/.cache/rive-skill` (Linux). `--web-runtime canvas` uses the Canvas2D build (no vector feathering).
- WebGL2 runs on SwiftShader (`--use-angle=swiftshader`): no GPU needed, the same on Linux. Renderer string in the report.
- Measured: 1280x720 at about 49 frames a second in one page; 1080p about 12 fps including start-up. Interiors match the CLI; edges are anti-aliased differently (mean 0.1-0.2 codes, under 0.5% of pixels past 8 codes). Timing matched: a click at 1.0 s rendered at 1.5 s showed the same animation phase in both engines (bars region mean difference 0.06).
- Limits: unsigned scripts are rejected by web runtimes; the runtime does not know which state machine is the artboard's default (it takes the first, and warns when there are several: pass `--state-machine`); keys, gamepad and semantic actions are not replayed (pointer only); `--fit layout` is treated as `contain`.

## Encoding

| Output | Settings |
|---|---|
| `.mp4` | libx264, preset slow, CRF 14 (`--crf`), yuv420p, BT.709 matrix, TV range, `+faststart`; odd sizes are padded by one pixel (reported) |
| `.mov` | prores_ks: 422 HQ by default (yuv422p10le), 4444 with `--alpha` (yuva444p10le, 16-bit alpha), `--prores proxy/lt/422/422hq/4444/4444xq`, vendor `apl0` |
| `.webm` | libvpx-vp9, CRF 20, yuva420p with `--alpha` (alpha is stored as side data; ffprobe shows `alpha_mode=1`) |
| `.gif` | palettegen/paletteuse (sierra dither), 1-bit transparency with `--alpha`, capped at 30 fps (`--gif-fps`) |
| PNG sequence | RGB, or straight-alpha RGBA with `--alpha` |

Colour, measured on ffmpeg 9.0.2:

- RGB to BT.709 TV-range YUV is exact through `scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int` (grey 29 becomes 10-bit luma 164 = 64 + 29/255 x 876).
- **Tagging trap:** `-color_primaries bt709 -color_trc bt709` alone leaves primaries and transfer `unknown` in the file on ffmpeg 9, because the untagged PNG frames' own properties win. `setparams=color_primaries=bt709:color_trc=bt709` at the start of the filter chain tags the file fully, and the decoded YUV is byte-identical either way (it tags, it does not convert). The renderer does this.
- **Verification trap:** the default swscale path from 10-bit ProRes to 8-bit RGB reads 2 codes low (29 as 27). Decode with `flags=accurate_rnd+full_chroma_int`, or to 16-bit, when you measure an encode.
- Code values are passed through: Rive's sRGB PNG values become the BT.709 file's values, the usual convention for graphics in an edit.

Audio: `--audio FILE [--audio-offset S]` muxes into `.mp4` (AAC 320k) or `.mov` (24-bit PCM), trimmed with `-t` to the video's exact length (never `-shortest`).

## The report and the refusals

`<output>.render.json` holds: tool, output, engine (CLI version and binary, or web runtime and renderer string), source (path, kind, SHA-256 of the project's files or the `.riv`), artboard (name, size, default state machine, view model), size, fit, fps, frames, the timeline (steps, data, curves used and unused), the exact CLI arguments of the first, middle and last frame, warnings, late frames, blank frames, alpha check, encode command, an ffprobe of the result (codec, profile, pixel format, frame count, colour tags) and the output's SHA-256.

The render stops when: the project does not build or `inspect` reports errors; `--alpha` meets an opaque background or a letterbox; every sampled frame (first, quarter, half, three quarters, last) is one flat colour (pass `--allow-blank` if that is intended); an `--alpha` solve does not recomposite (pass `--allow-bad-alpha` to write it anyway); a capture fails (the CLI's own message and exit meaning are shown).

## Linux

The Linux CLI build (linux-x64 only; arm64 hosts need emulation) ships only an OpenGL backend. On Mesa drivers (AMD radeonsi, Intel, llvmpipe) its generated GLSL has an identifier abutting a macro call (`c1(c)B`), which Mesa's preprocessor rejects, so no program links and every capture is the clear colour: rive-app/rive-runtime#92, open as of 24 Sep 2026. `--verify`, `--once` and `inspect` are unaffected.

- Workaround: bryanpinheiro/rive-mesa-glsl-fix (MIT), an `LD_PRELOAD` shim that rewrites `)ident` to `) ident` in every shader source. Tested by its author on Debian 13, Mesa 25.0.7, CLI 1.1.1.
- Headless software rendering with no display (from the George-RD/tools wrapper): `EGL_PLATFORM=surfaceless GALLIUM_DRIVER=llvmpipe MESA_LOADER_DRIVER_OVERRIDE=swrast`.
- The previewer window only opens on a real TTY.
- Neither was run on a Mesa GPU yet. `rive_doctor.py` shows it: its pixel check fails on a blank capture, and every render refuses all-flat frames with this reason attached.
- Measured on NVIDIA's own driver (Ubuntu 24.04, GTX 970, driver 580, CLI 1.1.1, 25 Sep 2026): captures draw through EGL on the NVIDIA device, `rive_doctor.py --web` passes, the web engine included (SwiftShader), and the live tests pass apart from the crash below.
- CLI 1.1.1 on Linux segfaults (exit -11) under `--data-dump-every` once a key press is in the run: a key the scene handles (the button template's Enter, Rive's own keyboard_menu sample), or any key followed by a drag. Without a key, and with the same arguments under `--screenshot` or an end-only `--data-dump`, it runs clean. No script here dumps per frame with keys; the live timing test skips its key-bearing times on Linux, and a crashed capture reports "the CLI crashed (SIGSEGV)".
- `rive_doctor.py --install` lays the CLI out as Rive's install.sh does. With the docs and samples anywhere but beside `versions/<version>/rive`, `rive docs` and `rive samples` fail ("not found beside the binary") and so does the doctor.
