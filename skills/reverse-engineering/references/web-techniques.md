# Tier 1: web reverse engineering

How a web page or app works: its stack, the calls it makes, what moves and why, the code
it ships, and any WebAssembly it runs. Everything here either reads bytes (safe on any
input) or runs a page you may analyse; code we do not trust runs only in `sandbox.py`.

```bash
RE=skills/reverse-engineering/scripts
```

The order matters. Watch before you read: the first version of the Supreme dossier read
the code first, kept the question's premise ("how does scroll drive it") and got three
things wrong. Watching the page run for one minute showed the page cannot scroll at all.

## 1. Fingerprint the stack

```bash
python $RE/fingerprint.py --url https://site.com
```

Static fetch, no JavaScript runs. Matches a signature table against headers and the HTML
body, and records the literal that matched plus a certainty (`definitional`, `strong`,
`weak`). Every static download in this tier (fingerprint, libscan, unminify) goes through
`webfetch.py`: the body is read up to a cap, a redirect's own body is never read (requests
unpacks it whole, even with redirects turned off; 597 KB of gzip in a 302 cost 618 MB), and the
whole fetch ends at a 180 s deadline (the timeout is per read, so a server sending one byte
inside each timeout never let a download end). A charset that is not a text codec (`zlib`,
`base64`, `idna` on random bytes) falls back to UTF-8 instead of failing the fetch.
Every pattern is bounded and the page's tags are walked in one pass, so a hostile page of
unclosed tags cannot stall a scan (libscan once took 57 s on 160 KB, hours at its cap). For a JS-rendered page, capture the rendered DOM with the browser skill first and
pass `--html`. `--emit-claims` prints dossier claims.

## 2. Capture the network

```bash
python $RE/capture_net.py --url https://site.com --out idle.json             # load only
python $RE/capture_net.py --url https://site.com --scroll --out scrolled.json
```

Every request with method, type, status and size. Always take an idle capture as well as a
driven one: a request that appears in both is not caused by your scrolling. (Supreme's 34
`?_rsc=` requests are Next.js prefetches that fire on load; the first dossier credited them
to scrolling because it never ran an idle load.)

## 3. Watch what moves, and why

```bash
python $RE/watch_page.py --url https://site.com --seconds 25 --control \
  --action "4:wheel:800" --action "8:key:ArrowRight" --action "13:click:button[aria-label='Next slide']" \
  --out run.json
python $RE/watch_page.py --url https://site.com --reduced-motion --seconds 25
python $RE/watch_page.py --url https://site.com --viewport 390x844 --seconds 15 --shots /tmp/shots --shot-every 1
```

A recorder injected before any page script logs, on the page's own clock (ms since
navigation): class, style and attribute changes (with a stable node id and a short label),
every `<video>`/`<audio>` play, pause, ended and seek with `currentTime`, CSS transitions
and animations with the elapsed time the browser reports, every timer the page sets with a
delay of 250 ms or more and whether it fired or was cleared, inputs the page received, and
scroll positions. After the run it snapshots the scroll layout: can the document scroll,
inner scroll containers, sticky elements, scroll snapping, and CSS scroll-driven animations
(`animation-timeline`).

`--control` runs the same page idle first. For each action it compares the action's window
with the same window of the idle run (aligned on the settle point, widened by 250 ms):
a change absent from the idle run, or far beyond jitter, is an answer to the input. A few
extra animation frames on an element that animates in both runs are not (that false answer
was found on Supreme's caption and fixed).

Where the input lands matters as much as what follows it. The pointer rests at the centre of
the viewport from the moment the page loads, in every run alike, so a wheel goes to whatever
sits in the middle of the screen; `hover:SELECTOR` or a click moves it. Each comparison lists
`received_by`, the elements the input reached: read it before trusting a "no change". (The
first Supreme wheel test fired at the page's top-left corner, landed on the site header and
never reached the reel.) An action that failed, such as a click on a selector that was never
found, gets "action failed: no verdict".

What the summary tells you, fast:

- `document scrolls: False` and no scroll containers (down or sideways): scroll cannot be
  the driver.
- Regular gaps between `play` events plus a matching `timer timeout N ms ... fired`: a clock.
- `requestAnimationFrame per second` near 0 with `transition opacity 0.8s`: CSS transitions,
  not a JavaScript animation loop. Near 60 while still: a loop is running.
- An action whose verdict is `answered the input` lists exactly what it changed.

The tool cannot hide the tab. Behaviour on `visibilitychange` stays a code reading.

## 4. Name the libraries inside minified code

```bash
python $RE/libscan.py --capture idle.json --all     # scripts from a capture, fetched again
python $RE/libscan.py --dir downloads/ --emit-claims
python $RE/libscan.py --url https://site.com        # the page's own <script> files only
```

Never grep for a package name: minifiers drop module names first. `framer-motion` is not in
a site that ships Framer Motion, and `data-framer-appear-id` is not either, because Motion
builds it at runtime from `"data-" + camelToDash("framerAppearId")`. The markers that work
are strings the library needs at runtime: its error messages, the classes and attributes it
writes, property names on its own objects.

Every marker is proven by `proof/libscan/prove.py`:

```bash
cd skills/reverse-engineering/proof/libscan
systemd-run --user --scope -p MemoryMax=4G python prove.py build   # npm install + 76 builds
python prove.py suggest --only embla-9-rc embla-8                 # candidate strings
python prove.py verify --write                                    # PASS only on a perfect matrix
```

Each `entries/*.js` is a small realistic use of one library, built with esbuild and with
webpack (production), with older majors swapped in by bundler alias; the libraries'
published minified files are a third kind of sample, and plain JavaScript and Preact are
controls with no covered library. `verify` requires every library to be found in exactly the
samples that contain it, rejects a marker seen nowhere, a marker that appears once in a
sample without its library, and a marker written in an entry file. When the proof disagreed
with an expectation, read the sample before changing anything: Locomotive Scroll 5's
published file loads Lenis from outside rather than carrying it, so the expectation was
wrong, not the marker.

Real sites corroborated it (2026-10-12): the expected library was found on every site whose
page actually loads it, and every extra detection was confirmed by reading the code at the
cited offset (Three.js revisions 182 and 172 on lenis.darkroom.engineering and animejs.com,
GSAP and Lottie on barba.js.org, jQuery on the hls.js demo). Misses were pages that do not
load the library (plyr.io is now a deprecation notice; embla's home page loads only its
package metadata), and versions outside the covered list (pixijs.com's header runs a very old
Pixi), which libscan reports as absent or as a one-marker `trace`.

Turbopack (Next.js) keeps module export names as strings in its registration lists
(`e.s(["Startscreen",0,function(...)`), which makes component names visible in Next.js 15+
bundles. webpack production builds mangle them.

## 5. Read the code

```bash
python $RE/unminify.py --url https://site.com/_next/static/chunks/x.js --out-dir recovered/   # map first
python $RE/unminify.py --file x.js --out-file x.readable.js
python $RE/unminify.py --file bundle.js --unpack --out-dir modules/
python $RE/unminify.py --file obf.js --deobfuscate --out-file obf.clean.js
```

Order: a source map with `sourcesContent` (the author's real files, paths sanitised so a
hostile map cannot write outside the folder; for a local file, a map is read only from inside
that file's own folder, as a regular file under the size cap, because the map's name comes
from the untrusted file and `sourceMappingURL=/dev/zero` once read until memory ran out; two
sources that strip to one path, as webpack's `file?hash` names do, are written as `file` and
`file~2` instead of the second overwriting the first, and a file and a folder of one name skip
the one that cannot be written instead of ending the recovery), then
wakaru 1.14.0 (Rust decompiler: `1e4`
back to `10000`, comma chains to statements, `"u"<typeof x` to a typeof test, names
recovered from destructuring; `--unpack` splits webpack, browserify and esbuild bundles and
writes `provenance.json`, the byte ranges each module came from), then js-beautify, then the
raw text unchanged. wakaru reads and never runs.

Deliberate obfuscation (javascript-obfuscator's hidden string tables) needs the code's own
decoder to run. `--deobfuscate` runs webcrack 2.16.0 for that, inside `sandbox.py`'s
offline container, with the input mounted read-only. In testing wakaru left the hidden
strings hidden and webcrack recovered them.

To find the mechanism in a chunk, search for what the watching step measured: the timer
delay (`1e4`, `6e4`), the transition class (`duration-[800ms]`), the aria labels of the
controls (`Pause slideshow`). Cite what you quote by file and byte offset; `validate_dossier.py
--evidence` will re-read it.

## 6. WebAssembly

```bash
python $RE/wasm_inspect.py module.wasm                 # summary
python $RE/wasm_inspect.py module.wasm --out m.json    # everything
python $RE/wasm_inspect.py module.wasm --disasm 202    # one function: offset | bytes | instruction
python $RE/wasm_inspect.py module.wasm --decompile tick
```

Pure Python for the structure: every function's name (when a name section exists),
signature and body offset and size, imports and exports, data segments and the readable
strings in them with file offsets, and the `producers` section (language and compiler). wabt
gives one function's disassembly with file offsets and bytes (ground truth) or its decompiled
text (a reading aid only: wasm-decompile names parameters one way in the signature and
another in the body). wabt is native code reading a hostile file, so it runs in the sandbox's
tools image (wabt 1.0.36); `--host-tools` uses the host's apt wabt 1.0.34 for a module you
trust. The two versions print the same bytes with different operand text (1.0.36 numbers a
function's locals after its parameters, `local[3..25]`; 1.0.34 printed `local[1..23]`), so
cite offsets and bytes, not the operand text. The parser's body offsets agree with
wasm-objdump's on real modules (e.g. draco_decoder.wasm, function 202 at 0x1ad86).

## 7. The dossier

```bash
python $RE/validate_dossier.py dossiers/site.json --evidence downloads/
python $RE/dossier.py dossiers/site.json --out dossiers/site.md
```

One JSON per target; see `references/verification.md` for the claim kinds.

## 8. Rebuild to check the reading

A reading is only as good as what it predicts. Write the smallest version of the mechanism
in our own code with our own media, then measure the original and the rebuild with the same
instrument: for a timed media sequence that means dwell, overlap, fade, the runtime timers,
wheel and keys, any next control, sound on, reduced motion and the phone layout, side by
side, each with a stated tolerance. A reading is trusted only when the rebuild behaves the
same on every measure.
