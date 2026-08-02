# Third party material in this folder

## better-accessibility

Source: https://github.com/jakubkrehel/skills
Commit: `a673333` (2026-07-29)
Licence: **MIT**

MIT requires the copyright notice and the permission notice to travel with these files. Do not strip this notice. The full text is at the bottom of this file.

### What was taken

| Taken | Landed at | State |
|---|---|---|
| `focus-and-keyboard.md`, `semantics-and-aria.md`, `screen-readers.md`, `hit-areas.md`, `motion-and-zoom.md` | this folder | verbatim, byte for byte |
| `forms.md` | this folder | one line changed, see below |
| `SKILL.md` (14 principles, common mistakes, review output format) | `../accessibility.md` | reworded header and cross-references, see below |

### What changed, and why

Upstream is a family of seven sibling skills that reference each other by skill name. Impeccable is one skill with a `reference/` folder, so every cross-skill pointer had to become a file path or be dropped.

- `forms.md`, iOS input zoom line: `better-typography` becomes a path to `../typography.md`. That is the only edit to the six deep-dive files; everything else is byte identical to upstream and will diff cleanly on a re-pull.
- `../accessibility.md`, principle 9: `better-colors` becomes a path to `../color-and-contrast.md`.
- `../accessibility.md`, common mistakes table: `better-typography` becomes a path to `../typography.md`.
- `../accessibility.md`, review format: `better-interface` orchestration becomes impeccable's own `audit` command.
- `../accessibility.md`, opening: upstream pointed RTL layout at `better-layout`. Impeccable has no RTL or bidirectional coverage in any reference doc, checked by grep across all seven, so the pointer was replaced with a statement that it is unwritten rather than silently dropped.
- YAML frontmatter removed from the entry document. The other reference docs carry none, and a `---` block at the top of a skill file has previously leaked verbatim into the skill listing.
- A section titled "Where this sits against the other reference docs" was added to `../accessibility.md`. It is not upstream material. It records three places where impeccable's older `../interaction-design.md` gives looser guidance than this material (focus rings, validation timing, hit-area thresholds) and states which one wins.

### What was deliberately left behind

- **The other six skills in the repo** (`better-colors`, `better-typography`, `better-layout`, `better-ui`, `better-interface`, `better-writing`). Each duplicates an impeccable reference doc that already exists. Accessibility was the only genuine gap.
- **`agents/openai.yaml`.** An OpenAI agent manifest. Nothing here reads it.

### Style

The vendored files keep their original punctuation. Counted, not assumed: multiplication signs in `hit-areas.md` (6) and in `../accessibility.md` (4), arrows in `motion-and-zoom.md` (3), `screen-readers.md` (1) and `semantics-and-aria.md` (1), ellipsis characters in `focus-and-keyboard.md` (2) and `screen-readers.md` (5). Rewriting any of it to house style would make every future diff against upstream noisy for no gain.

There are no em or en dashes anywhere in this fold, upstream's text or ours, so there was nothing to strip.

### Licence

```
MIT License

Copyright (c) 2026 Jakub Krehel

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
