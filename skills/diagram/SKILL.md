# Diagram

Render Mermaid diagrams to PNG / SVG / PDF for sending to the user.

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

## Notes

- Uses `@mermaid-js/mermaid-cli` (`mmdc`) under the hood with Puppeteer.
- The `--no-sandbox` flag is preconfigured in `scripts/puppeteer.json` because Chromium runs without a user-namespace sandbox.
- Renders are deterministic for the same source -- safe to cache by hash if a diagram is requested repeatedly.
- First-time install pulls a Puppeteer-managed Chromium (~150MB). Subsequent runs use the cached binary.
