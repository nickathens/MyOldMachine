# Algorithmic Art

Generate visual art programmatically using p5.js. Output is a self-contained HTML page (single file, p5 loaded from CDN) that can be rendered to PNG, captured as video, embedded in a treatment, or shipped as an interactive artifact.

The skill is for **creative output** (generative posters, motion graphics, code-as-art) -- not for charts, diagrams, or UI. For diagrams use the `diagram` skill. For charts use `charts`. For data viz the user can interact with, write a custom HTML/JS file directly.

---

## Composition

Three steps: write the sketch -> wrap in HTML -> render or send.

```bash
# 1. Write your draw body to a JS file (or pipe via stdin)
cat > /tmp/sketch.js <<'EOF'
background(0, 30);
let n = 200;
for (let i = 0; i < n; i++) {
  const x = noise(i * 0.05, frameCount * 0.005) * width;
  const y = noise(i * 0.07 + 99, frameCount * 0.003) * height;
  stroke(255, 80);
  point(x, y);
}
EOF

# 2. Wrap it in a self-contained HTML page
S="python $SKILL_DIR/scripts"
$S/art_scaffold.py --draw /tmp/sketch.js --seed 42 --width 1080 --height 1080 \
  -o /tmp/sketch.html

# 3a. Render a still
$S/art_render.py /tmp/sketch.html -o /tmp/sketch.png

# 3b. Or capture video (uses the media skill -- ffmpeg x11grab)
python skills/media/scripts/record_video.py \
  --url file:///tmp/sketch.html --output /tmp/sketch.mp4 --duration 8

# 4. Send
python utils/send_to_telegram.py --user USER_ID --photo /tmp/sketch.png
python utils/send_to_telegram.py --user USER_ID --document /tmp/sketch.html
```

## Reproducibility

Always use a seed. The scaffold sets `randomSeed(SEED)` and `noiseSeed(SEED)` automatically, but if you call any other random source (e.g. `Math.random`, `WebGL` noise functions, `Date.now()`), reproducibility breaks. Stick to p5's `random()`, `randomGaussian()`, `noise()`.

Different seeds explore the parameter space. To find good ones, render seeds 1..30 in batch and pick the best:

```bash
for s in $(seq 1 30); do
  $S/art_scaffold.py --draw /tmp/sketch.js --seed $s --width 600 --height 600 \
    -o /tmp/grid_$s.html
  $S/art_render.py /tmp/grid_$s.html -o /tmp/grid_$s.png \
    --width 600 --height 600
done
```

## Patterns that consistently produce good output

### Flow field (vector field guides particles)

```javascript
background(0, 12);                                    // Slow trail
const scale = 0.005;
for (let i = 0; i < 800; i++) {
  const x = random(width);
  const y = random(height);
  const angle = noise(x * scale, y * scale) * TWO_PI * 2;
  stroke(255, 30);
  line(x, y, x + cos(angle) * 8, y + sin(angle) * 8);
}
```

### Particle system

```javascript
let particles = [];
function setup() {
  for (let i = 0; i < 300; i++)
    particles.push({ x: random(width), y: random(height), vx: 0, vy: 0 });
}
function draw() {
  background(0, 20);
  for (const p of particles) {
    const a = noise(p.x * 0.003, p.y * 0.003, frameCount * 0.005) * TWO_PI;
    p.vx = lerp(p.vx, cos(a), 0.05);
    p.vy = lerp(p.vy, sin(a), 0.05);
    p.x += p.vx; p.y += p.vy;
    if (p.x < 0) p.x = width; if (p.x > width) p.x = 0;
    if (p.y < 0) p.y = height; if (p.y > height) p.y = 0;
    stroke(255, 60); point(p.x, p.y);
  }
}
```

### Recursive geometry (subdivision, fractals)

```javascript
function subdivide(x, y, w, h, depth) {
  if (depth <= 0 || w < 4) {
    fill(random(255), 80);
    rect(x, y, w, h);
    return;
  }
  if (random() < 0.5) {
    const sw = random(w * 0.3, w * 0.7);
    subdivide(x, y, sw, h, depth - 1);
    subdivide(x + sw, y, w - sw, h, depth - 1);
  } else {
    const sh = random(h * 0.3, h * 0.7);
    subdivide(x, y, w, sh, depth - 1);
    subdivide(x, y + sh, w, h - sh, depth - 1);
  }
}
```

### L-systems (organic structures)

```javascript
function lsystem(axiom, rules, iters) {
  let s = axiom;
  for (let i = 0; i < iters; i++) {
    s = [...s].map(c => rules[c] || c).join("");
  }
  return s;
}
const sentence = lsystem("F", { F: "FF+[+F-F-F]-[-F+F+F]" }, 4);
// Then walk the string, push/pop matrix on [/], rotate on +/-, line forward on F.
```

### Voronoi / Worley

```javascript
const sites = [];
for (let i = 0; i < 30; i++) sites.push({ x: random(width), y: random(height) });
loadPixels();
for (let y = 0; y < height; y++) {
  for (let x = 0; x < width; x++) {
    let nearest = Infinity;
    for (const s of sites) {
      const d = (s.x - x) ** 2 + (s.y - y) ** 2;
      if (d < nearest) nearest = d;
    }
    const c = constrain(map(sqrt(nearest), 0, 200, 0, 255), 0, 255);
    set(x, y, color(c));
  }
}
updatePixels();
```

## Aesthetic guardrails

These map onto a cinematic, restrained, B&W-with-selective-color sensibility.

- **Default to black background, low-alpha strokes** (`stroke(255, 30)`). Trails are usually better than solid fills.
- **Avoid raw `random()` colour** -- use a palette. Pull palette colours from `color-palette` skill or hand-pick 3-5 hex values.
- **Slow trails > hard clears** for animated sketches. `background(0, 12)` instead of `background(0)` in `draw()`.
- **Add grain.** A subtle noise overlay (`set(x, y, color(random(20)))` on 1% of pixels) makes generative work look less plastic.
- **Constrain the canvas.** Square (1080x1080) and 16:9 (1920x1080) are most useful. Avoid weird aspect ratios unless the work demands them.

## Verification

`art_render.py` captures a single canvas frame. Sparse sketches that rely on accumulation across frames (`point()` called 200x with `background()` each frame) appear almost black in the still. For static renders: aim for full-canvas coverage in one frame, use `strokeWeight >= 1.5`, prefer `line/rect/ellipse` over `point()` at large resolutions, and consider `noLoop()` so the screenshot deterministically captures the seeded composition. **Always view the PNG before claiming it works -- file size alone is not proof.**

## Composition with the presentations skill

Generative pieces work as section backgrounds or interstitials in scrolling treatments. Two patterns:

**A. Embed the HTML directly** (lets the sketch animate live in the treatment):

```html
<section class="generative">
  <iframe src="generative/sketch.html" loading="lazy"></iframe>
</section>
```

**B. Render to PNG** and use it as a section background (lighter, no JS in the treatment):

```bash
$S/art_render.py sketch.html -o assets/bg-section-04.png --width 1920 --height 1080
```

Then reference in the treatment HTML/CSS. PNG is the safer default for delivered treatments unless interactivity is the point.

## Sending interactive artifacts

A sketch HTML file is self-contained (only depends on the p5 CDN). Ship it directly:

```bash
python utils/send_to_telegram.py --user USER_ID --document /tmp/sketch.html
```

The user opens it in any browser. If you also want to ship custom JS/CSS the sketch loads from disk, zip the folder first.

## Notes

- The CDN dependency means offline playback fails. For permanent / offline-safe artifacts, inline the p5 source into the HTML instead of using the CDN.
- WebGL sketches (`createCanvas(w, h, WEBGL)`) work but the `art_render.py` screenshot path uses the canvas at its current state -- some WebGL features depend on multiple frames to converge. Increase `--settle-ms` to compensate.
- Do not generate art that looks like a chart, scatter plot, or technical diagram unless that's the explicit ask. The skill exists to make creative output, not pseudo-visualization.
