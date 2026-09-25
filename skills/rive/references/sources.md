# Sources, measurements and the landscape

Researched and measured 25 Sep 2026 for the first version of this skill. Findings were logged as they came in with the research skill (`research.py note`, topics `rive/*`), with a premortem of the plan and a dialectic on the two render engines stored beside them (`premortem.py` #1, `dialectic.py` #1).

## Primary sources

- The Rive CLI's own documentation, `rive docs` (42 topics, about 610 KB of Markdown written for agents), `rive schema`, and the bundled samples (`rive samples`), all for CLI 1.1.1. Read in full. The CLI ships them without a licence, so this skill paraphrases and points to `rive docs <topic>` instead of copying.
- Rive's web documentation through its index for agents, `https://rive.app/docs/llms.txt` (about 440 pages; every page has a `.md` copy): the CLI pages, editor exporting, SVG/PSD/fonts/audio import, the AI agent, MCP, feature support, runtime sizes, the web runtime (getting started, parameters, low-level API, data binding, Canvas vs WebGL2), best practices, pricing.
- `https://rive.app/pricing`, rendered in a real browser (the docs pricing page disagrees on Voyager).
- Rive's release manifest, `https://releases.rive.app/cli/latest/manifest.json` (per-platform artifacts with SHA-256; `v<version>/manifest.json` for a given one), and the Homebrew tap rive-app/homebrew-tap.
- The web runtime packages themselves (`@rive-app/webgl2` and `@rive-app/canvas` 2.43.1: `rive.d.ts`, `rive_advanced.mjs.d.ts`, `rive.js`), read for the low-level API, the default-state-machine behaviour and the event and asset names.
- The CLI binary's own strings, for its environment variables (`RIVE_NO_AUDIO_DEVICE`, `RIVE_HOME`, `RIVE_DOCS_DIR`...), and an `fs_usage` trace for where the login lives.
- Rive's blog and X posts: the CLI technical preview (11 Sep 2026) and "free during the preview" posts, Games Done Quick's omnibar, Territory Studio's film UI talk, the film and TV use-case page.
- rive-app/rive-runtime#92 (Linux Mesa blank renders), #87 (MCP and agent quality).

## Measured here

Everything numeric in `SKILL.md` and `rendering.md`: capture cost and parallel throughput, memory, determinism, gesture costs, the frame-0 rest pose, per-frame data, the alpha solve and its recomposite error, web against CLI differences, ffmpeg tagging and decoding, font defaults, the SVG converter against Chromium, the login path. The six templates were each built, gated with `rive_check.py`, rendered and looked at; four real mistakes were caught that way (bars bound to width, a ring under its track, a label in a Solo ignoring its position, a layout box inside a Node missing its clicks), plus the previous demo's one-way play button.

## Agent skills and tools on GitHub (licence decides what may be reused)

| Repository | Licence | What it is | Used |
|---|---|---|---|
| uianimation/rive-skill | MIT | editor- and MCP-centred Rive skill: runtime contract tables, evidence labels, a validation ladder | the idea of labelling evidence as verified, inspected or unverified (`ATTRIBUTION.md`) |
| stevysmith/rive-skills | MIT | Jan 2026 skills for Luau, React, web, and a TypeScript `.riv` writer | read; the CLI supersedes the writer |
| sanqiushili/Rive_skills | CC BY 4.0 | Luau script builder skill with quality gates | read |
| BowTiedSwan/rive-skills | none stated | editor and runtime reference skill | read only |
| linnnn89/Rive-Skill | none stated | Chinese-language skill; headless Puppeteer frame QA | read only |
| owenlord/rive-claude-skills | GPL-3.0 | web and React embedding skill | read only |
| hesham88/rive-agentic-cinema-studio | MIT | sentence to Rive asset through research, generation, tracing and the editor's MCP | read; its SVG-to-Rive path idea matches `rive_svg.py`, which is written from the SVG spec |
| breakawaydata/rive-render | Apache-2.0 | native headless renderer on rive-runtime (Metal/Vulkan) to PNG/GIF/MP4 | read; builds from source, pins an older runtime, not needed next to the CLI |
| George-RD/tools | not stated | vendored toolchain for sandboxes incl. the Rive CLI under QEMU and Mesa | read; the headless Mesa environment is cited in `rendering.md` |
| bryanpinheiro/rive-mesa-glsl-fix | MIT | `LD_PRELOAD` fix for blank Linux renders | referenced for the Linux side |
| Seranggapatah/Clihelp | not stated | a CLI project drawing a GLB television with WGSL and a CRT pass | read; its web limits (one GPU canvas, JPEG decode) are cited |
| FUNGnix/rive-mcp | MIT | MCP server writing `.riv` binaries | read |
| ODU33104/rive-mcp | source-available, forbids modification and AI analysis | editor-less MCP | rejected |
| aloisdeniel/rive_obs_source, medcelerate/TDRive, medcelerate/RiveRenderStream | MIT / not stated | Rive in OBS, TouchDesigner, disguise | cited as broadcast routes |
| maidopi-usagi/RiveGD | MIT | Rive in Godot 4 | cited for game work |
| rive-app/rive-wasm, rive-runtime, rive-code-generator-wip, awesome-rive | MIT / list | official runtimes, a `.riv` to typed-wrapper generator (last release 2024), a resource list | the web runtime is used, fetched not vendored |

Community pulse (the last30days skill, 25 Sep 2026): thin; a Reddit thread questioning Rive's real-world uses answered with Duolingo, and GitHub activity around CLI-based projects. Learning resources worth pointing a person at: Rive's own docs and YouTube, Rive Masterclass (paid course incl. scripting), LERP (free Luau course for Rive), Rive Playground and Rive Analyzer (open-source `.riv` inspectors).

## Versions

| Thing | Version | Where it is pinned or checked |
|---|---|---|
| Rive CLI | 1.1.1 (21 Sep 2026) | `rivelib.TESTED_CLI_VERSIONS`; `rive_doctor.py` re-checks flags and timing |
| Rive editor (desktop) | 0.8.5940 | self-updating |
| `@rive-app/webgl2`, `@rive-app/canvas` | 2.43.1 | `scripts/web_runtime.json` with integrity hashes |
| ffmpeg | 9.0.2 | the tagging and decoding notes in `rendering.md` were measured on it |
| Space Grotesk (templates) | variable, wght 300-700 | `templates/_fonts`, SIL OFL 1.1 |
