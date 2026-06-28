# Logo Animate

Turn a raster logo (PNG, JPG, WebP, or screenshot) into a clean minimal SVG, then into a choreographed brand animation delivered as standalone, dependency free HTML (and GIF or MP4). It vectorizes the mark itself, structures the geometry for motion, and choreographs it with Disney's twelve principles of animation. Use it to animate a logo, build a logo reveal, splash screen, or brand intro, add motion to a vectorized mark, or create a loading, idle, or hover state for a brand mark.

This is the logo specialist in the toolkit. It is the only tool here that takes a raster logo and produces the vector itself. For everything else, pick the right neighbour:

- General motion graphics to MP4 (title cards, animated charts, kinetic typography, deck data builds): use **remotion**.
- Generative or code as art motion (p5.js sketches, particle fields, posters): use **algorithmic-art**.
- AI generated, non deterministic motion from a still (camera drift, ambient life): use **image-gen** image to video.
- Static vector creation or conversion only: use **inkscape**.

The output is deterministic, infinitely crisp vector motion that runs in any browser with no dependencies, plus a verified static SVG that the animation lands on exactly (the Final Frame Contract).

---

## License (a clean advantage)

The source, pixel2motion by Nolan Lai, is MIT licensed. Unlike remotion (which needs a paid license for a company of four or more, and is metered per render inside a product), this skill is free for commercial and client work with no headcount or per render cost. See `ATTRIBUTION.md` and `LICENSE`. That makes it the right default for CooCoo client logo work.

---

## Setup

This skill depends on Pillow, numpy, and Playwright (declared in `deps.json`) plus a Chromium browser. Install the browser once with `playwright install chromium`.

Two scripts (`capture_motion_frames.py`, `probe_motion_continuity.py`) drive a real browser through Playwright for deterministic motion QA. `render_overlay.py` renders SVG through headless Chrome directly; it auto detects the browser and falls back to the Chromium that Playwright manages, or set `CHROME_BIN` to a binary path.

Run the scripts by absolute path and work in a scratch directory so outputs do not land in the skill folder:

```bash
SK="$SKILL_DIR"
mkdir -p /tmp/logo-work && cd /tmp/logo-work
```

---

## The pipeline (three phases)

### Phase 1, Pixel: read the source, write the brief

Look at the raster logo. Record its size, colours, and background, then name the semantic parts (mark, wordmark, letters, dot, swoosh). Each part is an actor in the animation. Write a short motion brief before touching geometry: three brand personality words, the usage context (splash 1200 to 2000ms, header reveal 300 to 800ms, loading loop, hover 150 to 300ms), and a choreography sketch of which parts move and in what order. `references/motion-personality.md` maps personality words to timing scale and easing tokens.

### Phase 2, Vector: fit minimal geometry, structured for motion

Fit the lowest complexity geometry that explains the mark: primitives first, a few analytic curves where the source has real shape changes, and trace only for irregular silhouettes. Minimal smooth geometry is what makes a logo animatable. A mark built from three clean parts choreographs; one built from four hundred traced stair steps cannot.

```bash
# starter trace and measurement mask (inspect and simplify, never final art by default)
python3 $SK/scripts/raster_logo_trace.py source.png --out outputs

# render the SVG in a real browser, overlay it on the source, report IoU
python3 $SK/scripts/render_overlay.py logo.svg source.png \
  --out outputs/fit_iterations/02_refined_overlay.png \
  --render-out outputs/final_render.png --report outputs/fit_metrics.json

# Bezier smoothness audit before accepting complex paths
python3 $SK/scripts/svg_path_audit.py logo.svg --out-svg bezier_segments.svg --report bezier_audit.json

# closed or self intersecting variable width ribbons (infinity marks, scripts, monograms)
python3 $SK/scripts/fit_ribbon_centerline.py source.png --seeds seeds.json --out-dir outputs/ribbon_fit

# geometry QA strip: source, then iterations, then final render
python3 $SK/scripts/overlay_progress_strip.py --source source.png --dir outputs/fit_iterations \
  --pattern "*overlay*.png" --final-image outputs/final_render.png --out outputs/overlay_progress_strip.png
```

The smoothness gate is a hard requirement: a smooth source must ship as smooth vector geometry. A jagged trace with a high IoU is rejected unless the source is genuinely pixel art. IoU is a trend signal, not a pass or fail threshold; judge every overlay with vision. Structure the SVG for motion as you fit: give each semantic part a stable id (`#mark`, `#wordmark`, `#dot`), split paths along animation seams, and put `pathLength="1"` on any stroke that will draw on.

### Phase 3, Motion: choreograph with the twelve principles

Choreograph against `references/twelve-principles-for-logos.md`. Shape the timeline 20 percent anticipation, 50 percent action, 30 percent settle. Stagger overlapping parts, and never let all parts start or stop on the same frame (the most common mechanical motion mistake).

```bash
# the main animated deliverable: standalone showcase HTML
python3 $SK/scripts/animate_svg_showcase.py logo.svg --css motion.css --out logo_motion.html \
  --title "Logo Motion" --duration-hint 1500

# static HTML check of the JavaScript DOM reconstruction
python3 $SK/scripts/svg_to_js_html.py logo.svg --out logo_static.html --title "Logo"

# minimal fallback HTML (debug and QA only, not the preferred deliverable)
python3 $SK/scripts/animate_svg_html.py logo.svg --css motion.css --out logo_motion_minimal.html \
  --title "Logo Motion" --duration-hint 1500

# deterministic frame capture, strip, and final frame diff (Playwright)
python3 $SK/scripts/capture_motion_frames.py logo_motion.html \
  --times 0,300,700,1000,1250,1500 --out outputs/motion_frames \
  --strip outputs/motion_strip.png --compare-final outputs/final_render.png

# easing probe: is the curve the browser runs the curve you designed? (Playwright)
python3 $SK/scripts/probe_motion_continuity.py logo_motion.html \
  --times 500,700,900 --probe "#draw-stroke:stroke-dashoffset"

# ink delta continuity sweep across handoffs and crossings (Playwright)
python3 $SK/scripts/probe_motion_continuity.py logo_motion.html --ink-sweep 850:1010:10
```

Two rules that catch the silent failures:

- Inside CSS `@keyframes`, timing functions must be literal `cubic-bezier(...)` values. A `var()` token there is silently dropped by Chromium and the motion degrades to linear with no error. Keep tokens for the `animation` shorthand, write literal easing in keyframes, and verify with the easing probe.
- Reduced motion is mandatory: under `prefers-reduced-motion: reduce` the logo must appear immediately in its final static state.

Verify before delivering: capture the motion strip and look at it (nothing should clip mid flight), and confirm the final captured frame matches the verified static render (the Final Frame Contract). For loops, the last keyframe state must equal the first.

---

## Send the result to the user

The deliverable is usually the HTML (live, crisp, embeddable). For Telegram, send the HTML as a document, or build a GIF or MP4 from the captured frames and send that:

```bash
python3 utils/send_to_telegram.py --user USER_ID \
  --document logo_motion.html --caption 'Logo animation'
```

---

## References (the encoded craft)

- `references/twelve-principles-for-logos.md`: each Disney principle applied to logos, with parameter ranges
- `references/motion-personality.md`: brand personality mapped to timing, easing, exaggeration, principle emphasis
- `references/reveal-patterns.md`: the choreography pattern library, including the split fill recipe for self crossing marks
- `references/html-delivery-template.md`: the required final HTML structure and QA hooks
- `references/ribbon-fitting.md`: closed and self intersecting variable width ribbon fitting

## Attribution

Ported from pixel2motion by Nolan Lai (MIT). The network only video exporter was intentionally left out; everything here runs locally. See `ATTRIBUTION.md`.
