# Rive on the web

A `.riv` plays in a web page through Rive's open-source runtime (rive-app/rive-wasm, MIT): one JavaScript file, one WebAssembly file, a `<canvas>`. `rive_web.py` builds, verifies and describes; this page is what to know around it.

## Which package

| Package | Draws with | Use when |
|---|---|---|
| `@rive-app/webgl2` (default here) | the Rive Renderer on WebGL2, the same renderer as the editor | almost always; the only one that draws vector feathering |
| `@rive-app/canvas` | the browser's Canvas2D | many graphics on one page (no WebGL context limit), heavy blend modes on phones |
| `@rive-app/canvas-lite` | Canvas2D, no text, layout, audio or scripting | tiny icons with none of those |
| `@rive-app/canvas-single` | Canvas2D with the wasm inlined | one request instead of two |
| `@rive-app/webgl` | | deprecated after 2.37.0 |

Sizes (Rive, Jan 2026, brotli): canvas-lite 222 KB, canvas 567 KB, webgl2 648 KB compressed. Pinned here: 2.43.1 (23 Sep 2026), `scripts/web_runtime.json`, fetched with its integrity hash into a user cache. React has `@rive-app/react-webgl2`; Flutter, iOS, Android, React Native, Unity, Unreal, Defold and C++ have their own runtimes.

What the web runtimes support (Rive's feature table, read 25 Sep 2026), against what the CLI can author:

| Feature | webgl2 / canvas since | Note |
|---|---|---|
| data binding, lists, images, artboards | 2.30.3 | |
| layouts, N-slicing | 2.23 | not canvas-lite for layouts |
| scripting | 2.34.0 | signed files only |
| semantics (screen readers) | 2.39.0 | |
| global view models, data-bound fonts | 2.39.2 | |
| GPU canvas (scripted WGSL) | webgl2 2.42.0 | not canvas; the web allows one GPU canvas per file (the Clihelp CRT project found a nested second one gets nil) |
| focus (keyboard navigation) | 2.43.1 | |
| vector feathering | webgl2 2.26.0 | **not canvas** |
| text input | none yet | the CLI's TextInput component does not run on any runtime |

A file from a newer editor or CLI still loads in an older runtime; features the runtime does not know are skipped.

## Building a page

```bash
python3 skills/rive/scripts/rive_web.py page build/intro.riv -o site/ --title "Intro" --state-machine Main
python3 skills/rive/scripts/rive_web.py page build/card.riv -o card.html --single-file --controls --data title=Hello
```

Folder mode writes `index.html`, `rive.js`, `rive.wasm` and the `.riv` (cacheable, best for a site or a surge deploy). `--single-file` inlines all three into one HTML file (about 3.6 MB with a font; good as an attachment). `--controls` builds a small panel from the view model at runtime: text fields for strings, sliders for numbers (0..100), checkboxes, colour pickers, trigger buttons, enum menus, kept in sync when the file changes a value itself. `--fit` is `contain` by default; `layout` makes a responsive artboard reflow to the window. The page binds the file's default view model instance when the file has one (`autoBind`; on a file without one it would log a console error) and calls `resizeDrawingSurfaceToCanvas()` on load and resize, so it is sharp on a Retina screen.

**Name the state machine when the artboard has more than one** (`--state-machine`). The runtime cannot see the artboard's default one, and with none named it plays the first timeline and warns that the next major version will change this (read in rive.js 2.43.1): listeners and binds are then dead, and the button template's page ignored a click (measured). So the page reads the file first and plays the artboard's first state machine unless one is named, with a console note when there are several. It passes the name with the singular `stateMachine` option; the plural `stateMachines` is deprecated in 2.43.1.

## Signing: the one that bites

Web runtimes and Rive's CDN reject unsigned scripts, and nothing local warns you: a CLI `--once` build with Luau plays in the previewer and in `rive_render.py`, then shows nothing scripted on a page. Build web files that contain scripts with `rive <dir> --publish` (needs `rive login`; fails closed; at most 100 scripts and 10 MB of compiled scripts). Files without scripts are fine unsigned; every template here is script-free for that reason. `rive_web.py verify` reports load errors and console messages that mention signing.

## Proving a page works

```bash
python3 skills/rive/scripts/rive_web.py verify site/index.html --click 560,560 --shots checks/
```

Loads the page in headless Chromium with the real clock, waits, screenshots, fails on a load error, a console error or a blank picture, and with `--click` (artboard coordinates, mapped through the page's contain fit) clicks and reports whether the picture changed. It looks twice before clicking: a page that changes on its own (a spinning logo) cannot show what a click did, so the answer is then `null` with a note; `rive_check.py --interaction` on the project compares at exact scene times instead. Run it before sending a link.

## Talking to the file from JavaScript

With `autoBind: true` the bound view model instance is `r.viewModelInstance`:

```js
const vmi = r.viewModelInstance;
vmi.string("title").value = "Hello";
vmi.number("progress").value = 0.75;
vmi.boolean("on").value = true;
vmi.color("accent").value = 0xFFC9A84C | 0;
vmi.enum("mode").value = "running";
vmi.trigger("celebrate").trigger();
vmi.number("card/price").value = 12;          // a nested view model
vmi.boolean("on").on(() => console.log("changed in the file"));
```

Also: `r.viewModelByName(name).instanceByName(name)` and `r.bindViewModelInstance(vmi)` to switch instances, `r.contents` (artboards, animations, state machines), `r.bounds`, events through `rive.EventType.RiveEvent`, `r.cleanup()` when removing it. Legacy state machine inputs (`stateMachineInputs`) still work but are deprecated. Assets can be loaded by the page instead of embedded (`assetLoader`), which is how a font or image is swapped per locale or client.

## In sites and presentations

- A plain site or a surge-deployed presentation: the folder from `rive_web.py page`, or the embed snippet from it.
- Webflow and Framer have official Rive plugins; Rive's hosted embed links (an iframe, no API access) need the Voyager plan.
- Several graphics on one page with webgl2: set `useOffscreenRenderer: true` on each so they share one WebGL context (browsers cap contexts), or use the canvas package.
- Pause graphics that scroll off screen, and consider `prefers-reduced-motion`: the same file can carry a calmer artboard or state for those users.
- Keyboard focus and screen-reader semantics work on webgl2/canvas 2.43.1; test them with `rive <dir> --key=...` and `--semantics=-` before shipping.
