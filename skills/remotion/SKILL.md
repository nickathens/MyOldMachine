# Remotion

Programmatic motion graphics. Build videos as React components and render them to H.264 MP4. Animated titles, lower thirds, animated charts, kinetic typography, data builds for decks. This is the motion graphics tool in the toolkit: everything else is either video editing (video-editing / blender-video) or static art (algorithmic-art / p5.js).

Rendering is pure CPU: Remotion drives a headless Chromium frame by frame and encodes with libx264. No GPU encoder is touched, so it runs anywhere Node and Chromium do. Default output is 1920x1080 at 30fps.

Use this skill for: motion title cards, animated bar/number builds for decks, treatment interstitials, social captions with motion. Do NOT use it for static posters (use algorithmic-art), diagrams (use diagram), or simple cuts and overlays on existing footage (use video-editing).

---

## License (read before any commercial or client use)

Remotion is NOT plain MIT. It is free for individuals, non-profits, and for-profit companies with **three or fewer employees**. A for-profit company with **four or more employees** needs a paid Company License (about 100 USD/month minimum). A product that renders videos for paying customers (a SaaS use case) falls under the per-render Automators plan.

In short: personal use and small teams are covered by the free license. The moment this becomes a rendering feature inside a product sold to others, or the company grows past three people, it needs a paid license. Flag this before it ships in anything client facing. License terms: https://www.remotion.dev/docs/license

---

## Setup (one time per machine)

The render engine lives at `render-engine/` inside this skill, with dependencies pinned in `package.json`. `node_modules` is gitignored, so on a fresh checkout install once:

```bash
npm install --prefix "$SKILL_DIR/render-engine"
```

`deps.json` runs this automatically when the skill's dependencies are checked, so in most cases it is already installed. The first render downloads a Chrome Headless Shell (about 90 MB) into the npm/puppeteer cache and reuses it after. Requires Node.js (node + npm on PATH).

---

## Render a video

```bash
ENGINE="$SKILL_DIR/render-engine"

# Built-in defaults
node "$ENGINE/render.mjs" --comp TitleCard --out /tmp/title.mp4
node "$ENGINE/render.mjs" --comp BarChartBuild --out /tmp/chart.mp4

# Override any prop with inline JSON
node "$ENGINE/render.mjs" --comp TitleCard \
  --props '{"title":"LAUNCH DAY","subtitle":"SEASON ONE"}' \
  --out /tmp/launch.mp4

# Or load props from a file (cleaner for chart data)
node "$ENGINE/render.mjs" --comp BarChartBuild --props ./data.json --out /tmp/chart.mp4
```

`render.mjs` flags: `--comp` (required, the composition id), `--props` (inline JSON object or path to a .json file), `--out` (output path, defaults to `render-engine/out/<comp>.mp4`), `--codec` (default `h264`), `--concurrency` (e.g. `50%` or a bare integer; defaults to letting Remotion pick). On success the absolute output path is printed to stdout; progress and logs go to stderr.

Unicode renders correctly when the glyphs exist in the font. The default font is Montserrat; install it (or set a different system font in the component) if non-Latin scripts come out as boxes.

---

## Built-in compositions

### TitleCard (1920x1080, 5s)

Cinematic title: words spring up and fade in with a stagger, a rule draws under them, the subtitle rises in, the group settles. Dark background, bone-white ink, warm gold accent.

| prop | default | meaning |
|------|---------|---------|
| `title` | `REMOTION` | main line; split on spaces, each word animates separately |
| `subtitle` | `MOTION GRAPHICS` | letter-spaced uppercase line under the rule |
| `accent` | `#C9A84C` | rule and subtitle colour |
| `background` | `#0A0A0A` | near black |
| `ink` | `#F5F2EA` | title colour (bone) |

### BarChartBuild (1920x1080, 6s)

Animated bar chart: title rises in, bars spring up from a baseline with a stagger, value labels count up as the bars grow. Good for a deck data beat (cost, time, before/after).

| prop | default | meaning |
|------|---------|---------|
| `title` | `Render time by method` | chart heading |
| `data` | 3 bars | array of `{ "label": "...", "value": 0 }`; any length |
| `unit` | `s` | suffix appended to each value label |
| `accent` | `#C9A84C` | bar colour |
| `background` / `ink` | dark / bone | as above |

Example `data.json`:

```json
{ "title": "Budget by department", "unit": "k", "data": [
  { "label": "Camera", "value": 42 },
  { "label": "Post", "value": 28 },
  { "label": "Sound", "value": 11 }
] }
```

### CardFan / CardFan3D (1920x1080, 8s)

A fan of reward/loyalty cards that opens like a hand fan, holds the spread, then folds back to a stack. `CardFan` is the flat 2D fan; `CardFan3D` renders the same content in real CSS 3D space (perspective, per-card tilt and depth). Title, colours, and every card (number or gift icon, copy, button, gradient) are props, so the fan re-themes to any card set without touching the animation.

| prop | default | meaning |
|------|---------|---------|
| `titleA` / `titleB` | `My` / `Rewards` | two-tone heading |
| `cards` | 5 cards | array of card objects (gradient, ink, big number or `icon:"gift"`, title lines, sub, optional `button`) |
| `background` | dark teal | `[from, to]` radial background |

---

## Send the result to the user

```bash
python utils/send_to_telegram.py --user USER_ID --video /tmp/title.mp4 --caption 'Title card'
```

---

## Add a new composition

Three steps, no build tooling beyond what is installed:

1. Write `render-engine/src/<Name>.tsx`. Export the component and a defaults object. Use `useCurrentFrame()`, `useVideoConfig()`, `spring()`, and `interpolate()` from `remotion` for animation. Copy `TitleCard.tsx` as the starting shape.
2. Register it in `render-engine/src/Root.tsx`: import it and add a `<Composition id="<Name>" component={...} durationInFrames={...} fps={30} width={1920} height={1080} defaultProps={...} />`. The `id` becomes the `--comp` value.
3. Render to test: `node render.mjs --comp <Name> --out /tmp/test.mp4`, then extract a frame with ffmpeg (or use `still.mjs --comp <Name> --frame N`) and look at it before delivering.

Animation rule of thumb: drive everything off `frame`. `spring({frame: frame - delay, fps})` for natural motion, `interpolate(frame, [in, out], [from, to], {extrapolateLeft:'clamp', extrapolateRight:'clamp'})` for linear ramps. Stagger elements by subtracting an increasing delay from `frame`.

---

## Performance and notes

- Render time scales with cores and resolution: a 5s to 6s 1080p clip renders in well under a minute on a 4-core machine. The bundle step runs once per invocation; for batch renders of the same project this is the main fixed cost.
- 1080p30 is the default. For social verticals set `width`/`height` to 1080x1920 on the `<Composition>`. For a quick preview, drop to 1280x720.
- Always look at a rendered frame before sending. Headless Chromium uses system fonts; if a font is missing it silently substitutes.
- `node_modules`, `out/`, and the `.remotion/` browser cache are gitignored. The source (`src/`, `render.mjs`, `still.mjs`, `package.json`, `package-lock.json`) is the skill; reinstall with the setup command after a fresh checkout.
- Versions are pinned to Remotion 4.0.482 and React 18.3.1. Keep all `remotion` and `@remotion/*` packages on the same version when upgrading.
