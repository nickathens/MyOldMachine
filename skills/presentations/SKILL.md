# Presentations

Generate scroll-based GSAP treatment documents from structured JSON definitions. Cinematic, continuously scrolling HTML pages with ScrollTrigger animations, floating particles, parallax images, dual typography (sans-serif + serif), and section-by-section reveal. PDF export via Playwright. Video export via ffmpeg x11grab.

## Quick Start

```bash
# Generate treatment from JSON definition
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html

# With PDF export
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html \
    --pdf /tmp/treatment.pdf

# Custom theme CSS
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html \
    --theme /path/to/custom.css

# Record as video (auto-scroll + ffmpeg x11grab)
python skills/presentations/scripts/record_presentation.py \
    --html /tmp/treatment.html \
    --output /tmp/treatment.mp4

# Video with background music
python skills/presentations/scripts/record_presentation.py \
    --html /tmp/treatment.html \
    --output /tmp/treatment.mp4 \
    --audio /path/to/bg_music.mp3

# Custom resolution and cover hold time
python skills/presentations/scripts/record_presentation.py \
    --html /tmp/treatment.html \
    --output /tmp/treatment.mp4 \
    --width 1920 --height 1080 --fps 30 --delay 6
```

## JSON Structure

The document is defined by a top-level object with metadata, a cover, and an array of sections:

```json
{
    "title": "Document Title",
    "lang": "en",
    "particles": true,
    "cover": {
        "brand": "Brand Name",
        "brand_url": "https://brand.com",
        "logo": "/path/to/logo.png",
        "logo_filter": "brightness(2) invert(1)",
        "title": "Campaign Title",
        "type": "Director's Treatment",
        "meta": ["Director Name", "Production Co", "March 2026"],
        "background": "/path/to/cover-bg.jpg",
        "duration": 4.0
    },
    "scheme": {
        "bg": "#050505",
        "text": "#e8e4df",
        "text_dim": "rgba(255,255,255,0.45)",
        "text_mid": "rgba(255,255,255,0.7)",
        "accent": "#c9a84c",
        "accent_dim": "rgba(201,168,76,0.3)",
        "accent_light": "#e8d9a8",
        "brand_color": "#1b2d82",
        "brand_light": "#c5cff0",
        "cream": "#f5f0e8"
    },
    "fonts": {
        "heading": "Space Grotesk",
        "body": "Outfit",
        "serif": "Playfair Display"
    },
    "sections": [...]
}
```

### Top-Level Fields

- `title` — HTML page title
- `lang` — Language code (default: `en`)
- `particles` — Enable floating particle background (default: `true`)
- `mode` — Display mode: `dark` (default), `light`, `editorial`, `minimal`
- `animation` — Animation preset: `fade` (default), `slide`, `scale`, `blur`, `clip`
- `nav` — Navigation type: `none` (default), `sidebar`, `topbar`, `dots`, `progress`
- `cover` — Cover section (see below). Top-level, not inside `sections`.
- `scheme` — Color scheme overrides. Auto-derives `accent_dim`, `accent_light`, `brand_glow` from base colors if not set.
- `fonts` — Font overrides. Accepts any Google Font name (41 curated fonts with optimized specs, unknown fonts get default weights)

### Cover Object

The cover is full-viewport with animated entrance and scroll-fade-out.

- `brand` — Brand name text (top of cover)
- `brand_url` — Link for brand name
- `logo` — Logo image path (local = base64 embedded)
- `logo_filter` — CSS filter for logo (e.g. `"brightness(2) invert(1)"`)
- `title` — Subtitle below logo (serif italic)
- `type` — Document type label (e.g. "Director's Treatment")
- `meta` — Array of strings shown at bottom of cover
- `background` — Background image path
- `duration` — Seconds to hold on cover during video autoplay (default: `4.0`)

## Section-Level Timing (for Video Export)

Every section supports an optional `duration` field (seconds) that controls how long the section stays visible during video autoplay. If omitted, durations are auto-calculated:

| Section Type | Default Duration | Logic |
|---|---|---|
| `divider` | 2.5s | Fixed |
| `quote` | 4.0s | Fixed |
| `reveal` | 4.0s | Fixed |
| `full_bleed` | 3.5s | Fixed |
| `image` | 3.0s | Fixed |
| `image_grid` | 4.0s | Fixed |
| `stats` | 3.5s | Fixed |
| `packshot` | 5.0s | Fixed |
| `video` | 6.0s | Fixed |
| `closing` | 5.0s | Fixed |
| `hr` | 1.0s | Fixed |
| `note` | 3–15s | Word count at 180 wpm |
| `beats` | 3–15s | Word count at 180 wpm |
| `content` | 3–15s | Word count at 180 wpm |
| `concept` | 3–15s | Word count at 180 wpm |
| `two_col` | 3–15s | Word count at 180 wpm |
| `table` | 3–15s | 2 + 0.8s/row, capped |
| `cards` | 3–15s | Item count + text, capped |
| `why_list` | 3–15s | Word count |
| `specs` | 2 + 0.6s/item | Item count |

Override any default by setting `"duration": 8.0` on the section.

## Section Types

### Divider (section header)
```json
{"type": "divider", "number": "01", "title": "The Landscape"}
```
Numbered section header with accent line. Animates in on scroll.

### Note (director's note / long-form serif text)
```json
{
    "type": "note",
    "paragraphs": ["First paragraph...", "Second paragraph..."],
    "signature": "— Your Name"
}
```
Serif italic paragraphs, each animating in. Optional signature in accent color.

### Quote (dramatic centered text)
```json
{"type": "quote", "text": "The big quote in serif.", "sub": "Optional smaller text below"}
```
Large serif italic centered text with radial glow background.

### Table (competition / data table)
```json
{
    "type": "table",
    "caption": "Optional caption above table",
    "headers": ["Brand", "Share", "Positioning"],
    "rows": [
        ["Zagori", "22%", "Mountain purity"],
        ["**Arrena**", "**2%**", "**???**"]
    ]
}
```
Last row auto-highlights. Rows animate in with stagger.

### Cards (2-col or 4-col grid)
```json
{
    "type": "cards",
    "caption": "Optional header",
    "columns": 2,
    "items": [
        {"name": "Card Title", "desc": "Card description text"},
        {"name": "Card Title 2", "desc": "More text"}
    ]
}
```
- `columns`: 2 (gap-card style) or 4 (ref-card style, uses wide container)
- Also accepts `"description"` as alias for `"desc"`

### Reveal (dramatic text reveal)
```json
{
    "type": "reveal",
    "label": "THE GAP",
    "text": "Nobody owns authenticity.",
    "highlight": "authenticity",
    "sub": "This is the opportunity."
}
```
Centered dramatic text that scales in. `highlight` word gets gradient accent treatment.

### Full Bleed (full-width image with parallax)
```json
{"type": "full_bleed", "src": "/path/to/image.jpg", "alt": "Description"}
```
Full-width image with parallax scroll, edge-fade gradients, and scale-in animation.

### Concept (centered hero text)
```json
{
    "type": "concept",
    "heading": "The *Central* Idea",
    "desc": ["Paragraph one.", "Paragraph two."],
    "tagline": "The tagline"
}
```
Large serif heading with description paragraphs. Supports markdown in heading (`*italic*` renders in accent).

### Why List (numbered reasons)
```json
{
    "type": "why_list",
    "caption": "WHY THIS WORKS",
    "items": [
        {"title": "Reason One", "text": "Explanation"},
        {"title": "Reason Two", "text": "Explanation"}
    ]
}
```
Auto-numbered list with large ghost numbers.

### Beats (storyline / treatment scenes)
```json
{
    "type": "beats",
    "beats": [
        {
            "label": "Scene 1",
            "title": "The Opening",
            "text": "We see a mountain landscape...",
            "text2": "Optional second paragraph",
            "dialogue": "What the character says.",
            "action": "Stage direction."
        }
    ]
}
```
Multiple beats in one section. Dialogue renders in serif italic with accent left border. Also works as `"type": "beat"` with a single beat (no `beats` array wrapper needed). Optional `dialogue_style` on each beat sets custom inline CSS on the dialogue element.

### Two Column
```json
{
    "type": "two_col",
    "columns": [
        {"label": "Color Palette", "text": ["Warm naturals", "Golden hour light"]},
        {"label": "Atmosphere", "text": "Bright, authentic, real"}
    ]
}
```
`text` can be a string or array of strings. Optional `style` on the section sets custom inline CSS on the section element.

### Specs (key-value list)
```json
{
    "type": "specs",
    "items": [
        {"label": "Camera", "value": "ARRI Alexa Mini LF"},
        {"label": "Lenses", "value": "35-50mm Cooke Speed Panchros"}
    ]
}
```

### Stats (large numbers)
```json
{
    "type": "stats",
    "items": [
        {"number": "1.2B€", "label": "Market Size"},
        {"number": "22%", "label": "Leader Share"}
    ],
    "text": "Optional explanatory text below"
}
```

### Packshot (product hero shot)
```json
{
    "type": "packshot",
    "image": "/path/to/product.png",
    "taglines": ["Pure.", "Natural."],
    "logo": "/path/to/logo.png"
}
```
Full-viewport section with product image, serif taglines, and brand logo.

### Video (YouTube/Vimeo embed)
```json
{
    "type": "video",
    "label": "Reference Video",
    "url": "https://www.youtube.com/watch?v=xyz",
    "caption": "Optional caption"
}
```
YouTube and Vimeo URLs auto-convert to embed format.

### Closing (director info / credits)
```json
{
    "type": "closing",
    "role": "Director",
    "name": "Your Name",
    "links": "yoursite.com",
    "brand_logo": "/path/to/logo.png",
    "company": "Your Company",
    "company_url": "https://example.com"
}
```

### Content (generic text block)
```json
{
    "type": "content",
    "caption": "OPTIONAL LABEL",
    "heading": "Optional Heading",
    "texts": ["Paragraph one.", "Paragraph two."],
    "align": "center"
}
```
Also accepts `"text"` (string) instead of `"texts"` (array).

### Image (single centered image)
```json
{"type": "image", "src": "/path/to/image.png", "caption": "Optional caption"}
```

### Image Grid (mood board)
```json
{
    "type": "image_grid",
    "caption": "VISUAL REFERENCES",
    "columns": 4,
    "images": [
        {"src": "/path/to/img1.jpg", "caption": "Reference 1"},
        {"src": "/path/to/img2.jpg", "caption": "Reference 2"}
    ]
}
```
`columns`: 2, 3, or 4.

### Horizontal Rule
```json
{"type": "hr"}
```

## Inline Markdown

Text fields support: `**bold**`, `*italic*`, `` `code` ``, `[link](url)`, `\n` line breaks.

## Modes

Four display modes define the surface system (background, text colors, component styling). The mode is the first creative decision for any presentation.

| Mode | Background | Text | Default Accent | When to Use |
|---|---|---|---|---|
| `dark` | #050505 (near-black) | #e8e4df (warm white) | #c9a84c (gold) | Treatments, cinematic pitches, premium brands, moody content |
| `light` | #faf8f5 (warm cream) | #1a1a1a (near-black) | #8b6914 (dark gold) | Business proposals, strategy docs, hospitality, architecture |
| `editorial` | #f8f5f0 (paper cream) | #1c1917 (charcoal) | #c0392b (deep red) | Publications, literary content, cultural institutions, reviews |
| `minimal` | #ffffff (pure white) | #000000 (pure black) | #000000 (black) | Technical proposals, portfolios, modernist brands |

The mode defines the surface. The `scheme` override customizes specific colors (accent, brand) on top of the mode. Same client can have different modes for different document types.

## Animation Presets

One preset per presentation. Defines how elements enter the viewport on scroll.

| Preset | Feel | Entrance | Best For |
|---|---|---|---|
| `fade` | Gentle, default | opacity + translateY(20px) | Universal, safe default |
| `slide` | Directional, dynamic | Alternating left/right translateX | Pitch decks, energetic content |
| `scale` | Dramatic, bouncy | scale(0.85) with back.out easing | Hero-heavy, product launches |
| `blur` | Cinematic, focus-pull | filter:blur(12px) clearing | Film treatments, luxury brands |
| `clip` | Sharp, editorial | clipPath inset reveal | Technical, editorial, architectural |

## Navigation

Five navigation types. Choose based on content structure and audience.

| Type | Appearance | Best For |
|---|---|---|
| `none` | No navigation, pure scroll | Treatments, short docs, video export |
| `sidebar` | Fixed left panel with section links | Long documents, playbooks, multi-chapter content |
| `topbar` | Fixed top bar with section links + progress | Business presentations, proposals |
| `dots` | Vertical dots on right edge | Minimal interference, portfolios |
| `progress` | Thin accent bar at top | Any length, subtle orientation |

Navigation items are auto-extracted from `divider` sections (number + title).

## Creative Direction

Before generating any presentation, make three conscious choices:

**1. Mode** (surface system)
Match the mode to the content's emotional register, not to a default. A sponsorship deck for a sports club can be dark. A strategy proposal for a cultural institution should probably be editorial. A tech company's capabilities deck might be minimal.

**2. Typography** (personality)
Never use the same font pairing twice in a row for different clients. The pipeline accepts any Google Font. Some proven pairings by context:

- **Cinematic/premium:** DM Serif Display + Outfit + Playfair Display
- **Corporate/clean:** Inter + Inter + Lora
- **Editorial/literary:** Cormorant Garamond + Source Serif 4 + EB Garamond
- **Tech/modern:** Space Grotesk + DM Sans + Spectral
- **Bold/energetic:** Unbounded + Plus Jakarta Sans + Libre Baskerville
- **Luxury/fashion:** Montserrat + Urbanist + Noto Serif Display
- **Brutalist/stark:** Archivo + Archivo + Space Mono
- **Friendly/startup:** Figtree + Nunito Sans + Crimson Pro

**3. Animation** (movement vocabulary)
Match the animation to the content's rhythm. A fast-paced sports proposal should use `slide`. A luxury brand treatment should use `blur`. A technical proposal should use `clip` or `fade`.

**What NOT to do:**
- Use dark mode + Space Grotesk + Outfit + Playfair Display + fade for everything
- Use the same navigation type for every document
- Default to gold accent when the brand has its own color

## Theme

Default theme: dark background, triple typography system (Space Grotesk / Outfit / Playfair Display), accent color system with brand color integration.

Theme CSS at: `skills/presentations/scripts/theme.css`

All styling uses CSS custom properties. Override via `scheme` in JSON, `mode` for surface presets, or provide a custom `--theme` CSS file.

## Video Export

Record any presentation as a cinematic video. Uses ffmpeg x11grab with CPU-based H.264 encoding (libx264) to capture headed Chromium on display :0.

**How it works:**
1. The HTML includes dormant auto-scroll JS (activated by `?autoplay=1` URL parameter)
2. `record_presentation.py` opens the HTML in Chromium, triggers autoplay, captures with ffmpeg x11grab
3. GSAP ScrollToPlugin smoothly scrolls section-by-section with `power2.inOut` easing
4. ScrollTrigger animations fire naturally as sections scroll into view
5. Encoded as H.264 MP4 via libx264

**Pacing control:** Set `duration` on individual sections to control video timing. Dramatic reveals should hold longer, data tables can be faster. The auto-scroll respects these per-section durations.

**Background audio:** Use `--audio` to mux in music. The video is trimmed to the shorter of video/audio (`-shortest`).

**Recording parameters:**
- `--width` / `--height` — Resolution (default: 1920x1080)
- `--fps` — Frame rate (default: 30)
- `--delay` — Seconds to hold on cover before scrolling (default: 5.0)

## Spec-to-JSON Workflow

Instead of constructing the JSON manually, describe the presentation as a natural language spec. The spec captures intent, tone, and structure — the JSON is generated from it.

### How to write a spec

A spec is a plain-English creative brief. Include:

1. **Purpose** — What is this document? (treatment, pitch, portfolio, brief)
2. **Audience** — Who will see it? (client, jury, investors, internal team)
3. **Tone** — How should it feel? (cinematic, corporate, playful, stark)
4. **Visual direction** — Colors, mood, reference images, typography preferences
5. **Content outline** — The narrative arc:
   - What's the opening statement / hook?
   - What data or context needs to be shown?
   - What's the core insight or concept?
   - How does the story unfold (beats, scenes)?
   - What's the closing / call to action?
6. **Pacing notes** — Which moments should hold (dramatic pause) vs. flow quickly
7. **Assets** — Logo paths, images, brand colors (hex values)

### Spec example

```
Treatment for Arrena water brand TVC pitch.

Audience: Creative director at the agency.
Tone: Cinematic, confident, quietly rebellious. Dark aesthetic.
Colors: Deep navy brand (#1b2d82), gold accent (#c9a84c), near-black background.

Structure:
- Open with brand logo on dark. Hold.
- Market landscape: table showing all Greek water brands, market share, positioning.
  Arrena is last row — highlighted, question mark on positioning.
- The gap: nobody owns "authenticity" in this space. Dramatic reveal.
- Director's note: 2 paragraphs on why authenticity matters now, personal voice.
- Concept: "Real Water, Real People" — hero text with tagline.
- 4 beats: mountain source, village life, the pour, the tagline moment.
- Visual references: 4-image mood board (earthy, warm, golden hour).
- Technical specs: camera, lenses, aspect ratio, color grade approach.
- Closing: director name, links, brand logo.

Pacing: Hold longer on the gap reveal and concept. Table and specs can be faster.
```

This spec contains everything needed to generate the full JSON definition. The mapping is direct:
- Market landscape → `table` + `stats`
- The gap → `reveal`
- Director's note → `note`
- Concept → `concept`
- Beats → `beats`
- Visual references → `image_grid`
- Technical specs → `specs`
- Closing → `closing`

## Brand Ingestion (Firecrawl)

Seed a treatment's `scheme`, `fonts`, and `cover.logo` from a live brand URL by passing `--brand-url`. The skill calls Firecrawl's v2 `/scrape` endpoint with the `branding` format, which is the dedicated design-system extractor (logo, colors, typography).

```bash
# Standalone — emit a JSON partial
python scripts/ingest_brand.py --url https://example.com --out brand.json

# Integrated — seed a treatment inline
python scripts/create_presentation.py \
    --json treatment.json \
    --brand-url https://example.com \
    --output out.html
```

**Setup.** Set `FIRECRAWL_API_KEY` in your shell env or in the project `.env`:

```
FIRECRAWL_API_KEY=fc-...
```

Free tier is 500 lifetime credits. One brand scrape = 1 credit.

**What gets extracted:**

| Firecrawl field | Maps to |
|---|---|
| `colors.background` | `scheme.bg` |
| `colors.textPrimary` | `scheme.text` |
| `colors.textSecondary` | `scheme.text_dim` |
| `colors.accent` / `colors.primary` | `scheme.accent` |
| `colors.primary` | `scheme.brand_color` |
| `typography.fontFamilies.heading` | `fonts.heading` |
| `typography.fontFamilies.primary` | `fonts.body` |
| `logo` / `images.logo` | `cover.logo` (downloaded to `~/.cache/presentations/brand_logos/`) |

**Precedence** (highest wins): explicit treatment JSON → `--brand-url` → `--aesthetic`. Nothing in the treatment gets overwritten.

## Aesthetic References

A local library of cinematic references lives in `references/*.md`. Each file encodes a brand's visual vocabulary as YAML frontmatter (palette, fonts, mood, avoid) plus prose notes. Pass `--aesthetic <name>` to seed a treatment with that baseline.

```bash
# List available references
python scripts/references.py list

# Inspect one
python scripts/references.py show a24

# Apply while generating
python scripts/create_presentation.py \
    --json treatment.json \
    --aesthetic a24 \
    --output out.html
```

**Current references:**

| Name | Category | Mood |
|---|---|---|
| `a24` | film | literary, modern-gothic |
| `aesop` | retail | wabi-sabi, apothecary |
| `apple-film` | film | quiet, cinematic-minimal |
| `boiler-room` | music | raw, nocturnal documentary |
| `cahiers` | film | French editorial, scholarly |
| `criterion` | film | archival, off-white editorial |
| `dazed` | magazine | editorial rebellion, high-contrast |
| `ghibli-museum` | film | hand-made, mythic |
| `letterboxd` | film | friendly-nerd, diary |
| `mubi` | film | disciplined cinephile |
| `opus` | film print | single-serif, quiet |
| `rick-owens` | fashion | brutalist, monastic |

Add new references as single `.md` files in `references/`. Filename is the slug.

## Output

- **HTML**: Self-contained, GSAP from CDN, images base64-embedded. Open in any browser.
- **PDF**: Playwright/Chromium print mode. All animations disabled, elements forced visible.
- **Video**: ffmpeg x11grab + auto-scroll. 30fps H.264 MP4. Optional background audio. Linux only.
