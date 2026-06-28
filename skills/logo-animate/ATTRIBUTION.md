# Attribution

This skill is ported from **pixel2motion** by Nolan Lai, used under the MIT license. The full license and copyright notice are in `LICENSE`, retained verbatim as the MIT terms require.

Source: pixel2motion (Pixel, Vector, Motion), a logo vectorization and animation toolkit.

## What was ported

- `scripts/` (10 Python scripts): the raster vectorizer, the render and IoU overlay, the ribbon centerline fitter, the SVG path audit, the geometry QA strip, and the animation and motion QA tools.
- `references/` (5 markdown docs): the encoded craft (Disney's twelve principles applied to logos, motion personality, reveal patterns, the HTML delivery template, ribbon fitting). These are upstream content, kept verbatim.

## What was changed or left out

- The network only video exporter (`export_claude_videos.mjs`, which drives a browser over a local CDP port) was intentionally left out. Everything in this skill runs locally with no network.
- The upstream `agents/openai.yaml` and the upstream marketing `docs/` were left out as not relevant here.
- `scripts/render_overlay.py` got one minimal adaptation: a `TYPE_CHECKING` guard so the lazy Pillow import resolves for the linter. Runtime behavior is unchanged.

## Dependencies

All already present on this machine: Pillow, numpy, and Playwright (main venv), plus Chromium. The core vectorize and animate pipeline needs only Pillow and numpy. The two deterministic motion QA scripts use Playwright.
