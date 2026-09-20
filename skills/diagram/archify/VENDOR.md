# Vendored: archify

Upstream: https://github.com/tt-a1i/archify
Commit: 72c750bb070d95171dbb2244e5b62b1b7da69c12 (2026-09-20, version 2.17.0-dev.1)
Licence: MIT. The permission notice is `LICENSE` in this folder. Third party
brand marks and the JetBrains Mono font are covered by `THIRD_PARTY_NOTICES.md`
and `assets/JetBrainsMono-OFL.txt`, both kept as shipped.

## What was taken

The contents of the release package `archify.zip` at that commit, which is the
authors' own zero dependency skill bundle (79 files). Nothing is installed from
npm; the runtime is plain Node 18 or newer, and `node bin/archify.mjs doctor`
proves the bundle is whole.

## Left out on purpose

- The five rendered example pages `examples/*.html`, about 4 MB of build
  output. The JSON examples that authoring reads are all here, and
  `node bin/archify.mjs demo <dir>` regenerates a rendered page on demand.
  Do not run the upstream `examples` command: it writes those pages back
  into this folder.
- The repository's tests, docs, gallery, benchmarks, and the two generator
  scripts that need devDependencies (`generate-brand-marks.mjs`,
  `generate-validators.mjs`). The generated validators and brand catalogue
  they produce are shipped and committed here.

## Local changes to upstream files

None. Everything the bot adds lives outside this folder: the wrapper
`../scripts/archify.py` (environment, Chrome discovery, preview PNG for
Telegram) and the "Interactive HTML maps" section of the diagram skill's
`SKILL.md`. The nested `SKILL.md` here is upstream's authoring contract and is
read on demand; it is not a bot skill of its own, so its YAML frontmatter never
reaches the skill listing.

## Re-pull recipe

```bash
git clone --depth 1 https://github.com/tt-a1i/archify /tmp/archify-src
unzip -q /tmp/archify-src/archify.zip -d /tmp/archify-zip
rm /tmp/archify-zip/archify/examples/*.html
rsync -a --delete --exclude VENDOR.md /tmp/archify-zip/archify/ skills/diagram/archify/
```

Then update the commit and version above, and run
`python -m unittest tests.test_diagram_archify`. The wrapper depends on three
upstream facts that a re-pull can move: the `ARCHIFY_UPDATE_CHECK_DISABLED`
switch in `scripts/check-update.mjs`, the `ARCHIFY_CHROME` and
`ARCHIFY_CHROME_NO_SANDBOX` variables read by `bin/visual-check.mjs`, and the
`<name>.visual-check.1440x900.<theme>.png` sidecar names it writes. The tests
pin all three.
