# Attribution

- **Space Grotesk** (`templates/_fonts/SpaceGrotesk-Variable.ttf`): Copyright 2020 The Space Grotesk Project Authors (github.com/floriankarsten/space-grotesk), SIL Open Font License 1.1, full text in `templates/_fonts/SpaceGrotesk-OFL.txt`. Fetched from google/fonts. The file was renamed from `SpaceGrotesk[wght].ttf`; the font itself is unmodified.
- **Rive web runtimes** (`@rive-app/webgl2`, `@rive-app/canvas`, rive-app/rive-wasm, MIT): not included in this repository. `scripts/riveweb.py` downloads the pinned version from the npm registry, checks its integrity hash and keeps it in a user cache; built pages copy `rive.js` and `rive.wasm` next to the page, where the MIT licence travels with them.
- **Rive CLI documentation**: the CLI ships its documentation without a licence, so nothing is copied from it. The references paraphrase it, cite topics by name (`rive docs <topic>`), and add what was measured here.
- **uianimation/rive-skill** (MIT, Praneeth Kawya Thathsara): the practice of labelling claims by how they were established (measured here, read in Rive's docs, untested) follows that skill's evidence labels.
- **bryanpinheiro/rive-mesa-glsl-fix** (MIT): referenced, not included, as the workaround for blank Linux captures.
- The SVG arc conversion in `scripts/rive_svg.py` follows the SVG 1.1 implementation notes (F.6, endpoint to centre parameterisation).
