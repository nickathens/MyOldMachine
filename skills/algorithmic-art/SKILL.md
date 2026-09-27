# Algorithmic Art

Generate visual art programmatically using p5.js. Output is a self-contained HTML page (single file, p5 loaded from CDN) that can be rendered to PNG, captured as video, embedded in a treatment, or shipped as an interactive artifact.

The skill is for **creative output** (generative posters, motion graphics, code-as-art), not for charts, diagrams, or UI. For diagrams use the `diagram` skill. For charts use `charts`. For data viz the user can interact with, write a custom HTML/JS file directly.

---

## Composition

Three steps: write the sketch, wrap it in HTML, render or send.

```bash
# 1. Write a draw() body to a JS file (or pipe it on stdin)
cat > /tmp/sketch.js <<'EOF'
// A draw() body: the canvas exists and the seed is set before it runs.
noLoop();                                   // one frame: a still
background(8);
noFill();
for (let i = 0; i < 70; i++) {
  const y0 = map(i, 0, 69, height * 0.15, height * 0.95);
  stroke(255, 60 + 140 * noise(i * 0.2));
  beginShape();
  for (let x = 0; x <= width; x += 3) {
    const ridge = noise(x * 0.006, i * 0.12) * noise(x * 0.002 + i * 0.01) ** 2;
    vertex(x, y0 - ridge * height * 0.35);
  }
  endShape();
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

Two ways to hand over the code:

- **A draw() body** (`--draw`, plus an optional `--setup` body). The page makes
  the canvas, seeds `random()` and `noise()`, runs the setup body (default
  `background(0);`), then the draw body every frame. A `let` in the setup body
  is not visible in draw; state that lives across frames needs a sketch.
- **A complete sketch** (`--sketch`) that defines its own `setup()` and
  `draw()` and any globals. The page seeds before your `setup()` runs, and
  makes the canvas at `--width`x`--height` unless your code calls
  `createCanvas` itself. A `--draw` file that defines `setup()` or `draw()` is
  treated as a complete sketch (before 2026-09-27 it was nested inside the
  page's draw(), never ran, and the still came out black).

`SEED` is defined for you in both; do not declare it again.

The renderer loads the page in headless Chromium and captures the canvas at
frame 120 (`--frames`), or at once when the sketch calls `noLoop()`. It exits
1 with the reason when p5 fails to load (it comes from a CDN, so offline
renders fail), when the sketch throws a JavaScript error, or when it times out
(`--timeout`, 120 s), and warns when the canvas is one flat colour. Pages
without p5 are captured `--settle-ms` after their canvas appears.

## Reproducibility

Always use a seed. The page sets `randomSeed(SEED)` and `noiseSeed(SEED)`, and
the renderer stops at an exact frame, so one seed, one size and one `--frames`
give the same still every time. Anything outside p5's seeded sources breaks
this: `Math.random`, `Date.now()`, `millis()`, `deltaTime`. Animate with
`frameCount`, draw randomness from `random()`, `randomGaussian()` and `noise()`.
(Before 2026-09-27 the renderer waited two seconds of wall time instead, and
two renders of one seed differed across the whole canvas.)

Different seeds explore the parameter space. To find good ones, render seeds
1..30 in batch and pick the best:

```bash
S="python $SKILL_DIR/scripts"
for s in $(seq 1 30); do
  $S/art_scaffold.py --draw /tmp/sketch.js --seed $s --width 600 --height 600 \
    -o /tmp/grid_$s.html
  $S/art_render.py /tmp/grid_$s.html -o /tmp/grid_$s.png \
    --width 600 --height 600
done
```

Positions scale with the canvas, so render the chosen seed again at the final
size and look at it before sending.

## Patterns that consistently produce good output

Each block below was rendered at 1080x1080 on 2026-09-27 and looked at, and
tests/test_algorithmic_art_fixes.py renders every one of them. Blocks with
`setup()`/`draw()` go in with `--sketch`, the rest with `--draw`.

### Flow field (a draw() body: strokes build up, no clear)

```javascript
// Stateless strokes along a noise field; no clear, so they build up.
const scale = 0.004;
stroke(255, 14);
for (let i = 0; i < 1500; i++) {
  const x = random(width);
  const y = random(height);
  const angle = noise(x * scale, y * scale) * TWO_PI * 2;
  line(x, y, x + cos(angle) * 14, y + sin(angle) * 14);
}
```

### Particle system (a complete sketch: particles carry state across frames)

```javascript
let particles = [];

function setup() {
  background(0);
  for (let i = 0; i < 1500; i++) particles.push(spawn());
}

function spawn() {
  return { x: random(width), y: random(height), vx: 0, vy: 0 };
}

function draw() {
  stroke(255, 16);
  for (let i = 0; i < particles.length; i++) {
    const p = particles[i];
    const a = noise(p.x * 0.002, p.y * 0.002, frameCount * 0.002) * TWO_PI * 2;
    p.vx = lerp(p.vx, cos(a) * 2, 0.1);
    p.vy = lerp(p.vy, sin(a) * 2, 0.1);
    line(p.x, p.y, p.x + p.vx, p.y + p.vy);
    p.x += p.vx;
    p.y += p.vy;
    if (p.x < 0 || p.x > width || p.y < 0 || p.y > height) particles[i] = spawn();
  }
}
```

### Recursive geometry (a complete sketch: subdivision, a palette, film grain)

```javascript
const PALETTE = ["#0e0e0e", "#262626", "#3a3a3a", "#e8e2d6", "#b3202a"];

function setup() {
  noLoop();
}

function draw() {
  background(PALETTE[0]);
  stroke(PALETTE[0]);
  strokeWeight(3);
  subdivide(24, 24, width - 48, height - 48, 7);
  grain(10);
}

function subdivide(x, y, w, h, depth) {
  if (depth <= 0 || w < 24 || h < 24 || random() < 0.12) {
    fill(random() < 0.06 ? PALETTE[4] : random(PALETTE.slice(0, 4)));
    rect(x, y, w, h);
    return;
  }
  if (w > h) {
    const sw = random(w * 0.3, w * 0.7);
    subdivide(x, y, sw, h, depth - 1);
    subdivide(x + sw, y, w - sw, h, depth - 1);
  } else {
    const sh = random(h * 0.3, h * 0.7);
    subdivide(x, y, w, sh, depth - 1);
    subdivide(x, y + sh, w, h - sh, depth - 1);
  }
}

// Film grain, once, as the last step of a still.
function grain(amount) {
  loadPixels();
  for (let i = 0; i < pixels.length; i += 4) {
    const g = random(-amount, amount);
    pixels[i] += g;
    pixels[i + 1] += g;
    pixels[i + 2] += g;
  }
  updatePixels();
}
```

### L-systems (a complete sketch: organic structures)

```javascript
const RULES = { F: "FF+[+F-F-F]-[-F+F+F]" };

function lsystem(axiom, rules, iterations) {
  let s = axiom;
  for (let i = 0; i < iterations; i++) s = [...s].map(c => rules[c] || c).join("");
  return s;
}

function setup() {
  noLoop();
}

function draw() {
  background(0);
  stroke(255, 110);
  translate(width / 2, height * 0.97);
  const step = height / 60;
  const turn = radians(24);
  for (const c of lsystem("F", RULES, 4)) {
    if (c === "F") {
      line(0, 0, 0, -step);
      translate(0, -step);
    } else if (c === "+") rotate(turn + random(-0.08, 0.08));
    else if (c === "-") rotate(-turn + random(-0.08, 0.08));
    else if (c === "[") push();
    else if (c === "]") pop();
  }
}
```

### Voronoi / Worley (a draw() body)

```javascript
// A draw() body. noLoop(): the sites are random, so every frame would differ.
noLoop();
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
- **Avoid raw `random()` colour**: use a palette. Pull palette colours from `color-palette` skill or hand-pick 3-5 hex values.
- **Slow trails over hard clears** for animated sketches. `background(0, 12)` instead of `background(0)` in `draw()`.
- **Add grain.** A subtle noise overlay makes generative work look less plastic: the `grain()` helper in the subdivision pattern, called once as the last step of a still. (The old tip, `set()` on 1% of pixels, changed nothing on screen: `set()` writes a pixel buffer that only `updatePixels()` shows.) Not on a canvas that accumulates across frames, where the grain would pile up.
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
S="python $SKILL_DIR/scripts"
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

- The CDN dependency means offline playback fails (and `art_render.py` says so). For permanent / offline-safe artifacts, inline the p5 source into the HTML instead of using the CDN.
- WebGL sketches (`createCanvas(w, h, WEBGL)`) work. The still is the canvas at the captured frame, so a sketch that needs many frames to converge needs a higher `--frames`.
- Do not generate art that looks like a chart, scatter plot, or technical diagram unless that's the explicit ask. The skill exists to make creative output, not pseudo-visualization.
