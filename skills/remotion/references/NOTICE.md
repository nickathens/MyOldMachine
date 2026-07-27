# Third party material in this skill

## video-shotcraft

Source: https://github.com/Vincentwei1021/video-shotcraft
Commit: `93fe427` (2026-07-27)
Licence: **Apache License 2.0**

Apache-2.0 requires the licence and attribution to travel with these files. Do not strip this notice. Full licence text: https://www.apache.org/licenses/LICENSE-2.0

### What was taken

| Taken | Landed at | State |
|---|---|---|
| 104 shot recipe cards, 10 categories | `references/shots/` | verbatim |
| Pipeline, aesthetic rules, beat sync, sound design, sequences, final review | `references/*.md`, `references/sequences/` | verbatim |
| `helpers/motion.ts`, `helpers/shake.ts`, `helpers/rand.ts` | `render-engine/src/helpers/` | verbatim, provenance header added |
| `Caption.tsx`, `DigitRoll.tsx`, `FlashCut.tsx`, `PageCam.tsx`, `VerticalTicker.tsx` | `render-engine/src/` | provenance header added, explicit React import added |

### What was deliberately left behind

- **`gallery/` and `demos/`, roughly 165 MB of motion preview videos.** They are a browsing aid for picking a shot. The recipe cards carry the same information as text and cost 0.6 MB. Nothing here depends on them.
- **`template/`**, a full runnable project. This engine already has its own render CLI and Root registry; a second project shape would compete with it.
- **`FlatPanel.tsx` and `helpers/camera.tsx`.** Both import `three` and `@react-three/fiber`, which this engine does not carry. To adopt them later, add `three`, `@react-three/fiber` and `@remotion/three` at matching versions and render through `ThreeCanvas`. That would also be the bridge to the `img2threejs` skill, which emits Three.js objects. It is a real dependency decision, not a copy, so it is not taken here.

### Language

Every recipe card is written in Chinese. They were not translated wholesale, because a machine pass over 104 craft documents would lose exactly the precision that makes them worth keeping. `shot-index.md` is an English index of all 104 with a one line gloss each: use it to find the right card, then read that card directly.

### Known upstream quirk

`Caption.tsx` carries an upstream comment conceding that its 22px type violates the project's own aesthetic rule Q11 (subtitles at 56px or more) and is an intentional exception for an information strip. It is fine as a corner caption. Do not use it as a narrative subtitle without raising the size.
