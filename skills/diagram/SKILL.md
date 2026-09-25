# Diagram

Render Mermaid diagrams to PNG / SVG / PDF, or build interactive self contained HTML system maps with archify, for sending to the user.

Mermaid covers ~90% of common diagrams: flowcharts, sequence diagrams, class diagrams,
state machines, ER diagrams, Gantt charts, mindmaps, gitGraph, pie charts, journey,
quadrant, timeline, sankey, and more. Authoring is text-only -- no GUI, no manual layout.

## Quick reference

```bash
D="python $SKILL_DIR/scripts/diagram.py"

# From file
$D path/to/diagram.mmd -o /tmp/out.png

# From stdin (preferred when generating dynamically)
$D -o /tmp/out.png <<'EOF'
graph TD
    A[Start] --> B{Decision}
    B -->|Yes| C[Do thing]
    B -->|No| D[Skip]
EOF

# Theme + width
$D src.mmd -o /tmp/dark.png --theme dark --width 1800
$D src.mmd -o /tmp/light.png --theme default --background white
```

Defaults: `--theme dark`, `--background transparent`, `--width 1600`, format inferred from output extension.

`--width` is the page width: a wider diagram is fitted to it, a narrower one keeps its
natural size. It means the same on Mermaid CLI 11 and 12; the script reads the installed
version and passes the width the way that version takes it, because 12 removed `-w`.

The look is Mermaid 11's: flat boxes, labels at their natural width, the dagre layout.
`scripts/mermaid.json` keeps it on Mermaid 12, so a diagram re-rendered after the update
matches one rendered before it. A diagram's own front matter always wins, so one diagram
can still ask for Mermaid 12's ELK layout or its shaded `neo` look:

```
---
config:
  layout: elk
---
graph LR
    A --> B
```

## Common diagram types

### Flowchart (most common)

```
graph LR
    A[Input] --> B[Process]
    B --> C{Valid?}
    C -->|yes| D[Save]
    C -->|no| E[Reject]
```

Direction: `TD` top-down, `LR` left-right, `BT` bottom-top, `RL` right-left.

### Sequence diagram

```
sequenceDiagram
    User->>Bot: /command
    Bot->>Claude: prompt
    Claude-->>Bot: response
    Bot-->>User: reply
```

### State machine

```
stateDiagram-v2
    [*] --> Idle
    Idle --> Running: start
    Running --> Idle: stop
    Running --> Failed: error
    Failed --> [*]
```

### ER diagram

```
erDiagram
    USER ||--o{ SESSION : has
    SESSION ||--|{ MESSAGE : contains
    USER {
        int id
        string name
    }
```

### Gantt

```
gantt
    title Project Timeline
    dateFormat YYYY-MM-DD
    section Build
    Spec      :a1, 2026-05-01, 3d
    Implement :after a1, 7d
```

### Class diagram

```
classDiagram
    class Skill {
        +String name
        +load()
    }
    Skill <|-- BehavioralSkill
    Skill <|-- ScriptSkill
```

## Common syntax pitfalls

- **Reserved words in node labels:** wrap with quotes -- `A["End-of-life"]` not `A[End]`.
- **Special characters:** escape pipes inside labels with `&#124;` and quotes with `&quot;`.
- **Arrow direction matters:** `A --> B` is "A points to B" -- swap if reading the wrong way.
- **Subgraphs need names:** `subgraph "My Group"` not `subgraph My Group` if the name has spaces.
- **Don't mix diagram types in one source.** One `graph` / `sequenceDiagram` / etc per file.

## Send to user

After rendering:

```bash
$D src.mmd -o /tmp/out.png
python utils/send_to_telegram.py --user USER_ID --photo /tmp/out.png --caption "Architecture"
```

For text-heavy diagrams (long Gantts, big class diagrams) prefer SVG so it stays sharp on zoom:

```bash
$D src.mmd -o /tmp/out.svg
python utils/send_to_telegram.py --user USER_ID --document /tmp/out.svg
```

## Interactive HTML maps (archify)

Mermaid gives a picture. For a system map someone will explore, click through
or present, use archify: a small typed JSON spec compiles into ONE self
contained HTML file with search, focus, route tracing, guided chapters, dark
and light themes, and PNG, SVG and WebM export built into the page. Five
types: `architecture` (components and boundaries), `workflow` (steps, lanes,
gates), `sequence` (calls and returns), `dataflow` (pipelines, lineage) and
`lifecycle` (states, retries). A pasted Mermaid flowchart, sequenceDiagram or
stateDiagram can be read for its topology and re-authored as a spec.

The renderer is vendored at `archify/` (MIT, pinned in `archify/VENDOR.md`).
Always go through the wrapper, never `node archify/bin/archify.mjs` directly:
the wrapper turns the package's update check off, points the browser check at
the Chrome that Puppeteer keeps for mmdc, and adds the sandbox flag Linux
needs (macOS runs it sandboxed).

```bash
A="python $SKILL_DIR/scripts/archify.py"

# 1. Pick the type, then read ONLY the matching schema and one example:
#    archify/schemas/<type>.schema.json, archify/schemas/common.schema.json,
#    and one archify/examples/<name>.<type>.json. Write the spec fresh (new
#    ids, real names, your own layout); the example shows field shape, not
#    content. Full rules: archify/SKILL.md, then, only when a diagnostic
#    needs it, archify/references/authoring-contract.md.

# 2. Validate while iterating (0.3 s, no browser)
$A validate architecture /tmp/map.json

# 3. Deliver the HTML plus a PNG of the rendered page for Telegram
$A deliver architecture /tmp/map.json -o /tmp/map.html --preview /tmp/map.png
```

`deliver` writes nothing for a spec that fails a check; it prints the
diagnostics (code, subject, evidence, supportedFixes). Fix only what a
diagnostic names, then rerun. Exit 3 means the HTML is written but the browser
check did not pass: read the message, the PNG still shows the page when a
capture exists.

Send both. The HTML is the deliverable (it opens in the phone browser with no
network) and the PNG is what people see in the chat:

```bash
python utils/send_to_telegram.py --user USER_ID --photo /tmp/map.png --caption "Request path"
python utils/send_to_telegram.py --user USER_ID --document /tmp/map.html
```

Rules that matter:

- Keep `meta.quality_profile` at `"showcase"` and keep every check green: the
  receipt must read 9/9 checks, 0 errors, 0 warnings.
- Start with one main path, at most 12 primary nodes and automatic routes. Add
  `via`, `channelX`, `channelY` or `labelAt` only when a diagnostic asks for
  one, one control per repair.
- Omit `meta.visual_preset`, `meta.subtitle` and `meta.legend` unless asked.
  Motion (`meta.animation: "trace"`) is opt in; static is the default.
- Greek, or any language other than English and Simplified Chinese: write all
  authored text in that language and omit `meta.locale`. The viewer's own
  buttons stay English; say so when delivering.
- Never run the upstream `examples` command: it writes 4 MB of rendered pages
  into the vendored tree. For a sample page use `$A run demo /tmp/demo`.
- Skip the "Update awareness" section of `archify/SKILL.md`. The wrapper turns
  that check off on purpose, and the pinned version is updated by re-pull.
- A node whose `brand` is an object with a `url` is fetched from that site at
  build time and checked against its `sha256`: private addresses are refused,
  a mismatch fails the delivery. Leave `brand` out when the build must stay
  offline. The delivered page itself never fetches anything.
- The viewer is desktop first; on a phone the page scrolls. The PNG is the
  phone view.

## Notes

- Uses `@mermaid-js/mermaid-cli` (`mmdc`) under the hood with Puppeteer.
- Two Mermaid CLI 12 changes are not covered by `scripts/mermaid.json`: a PDF is sized
  to the diagram instead of a Letter page, and mindmap nodes draw slightly larger.
- The `--no-sandbox` flag is preconfigured in `scripts/puppeteer.json` because Chromium runs without a user-namespace sandbox.
- Do not cache renders by output hash: class and gitGraph PNGs differ byte for byte on every run of the same source, and PDFs carry a timestamp. Cache by source hash if needed.
- The install fetches a Puppeteer-managed Chrome and its headless shell into the bot user's own cache (`~/.cache/puppeteer`), and every later run uses that. `mmdc` draws with the headless shell of the exact build its Puppeteer pins, so a render that fails with "Could not find chrome-headless-shell (ver. ...)" means that build is missing for this user: a Linux install under sudo leaves it in root's cache, and a failed download still lets npm exit 0. Fetch it as this user with `python3 utils/puppeteer_browsers.py @mermaid-js/mermaid-cli`, then render again. The nightly app update tries a new mermaid-cli in a scratch folder and draws one diagram with it before it replaces the live one.
