# Vendoring record: last30days

This skill is a vendored, pinned copy of an external open-source project. The engine code is verbatim; only the MOM-facing `SKILL.md`, `deps.json`, this file, and `notes.md` were added on top.

## Source

- **Upstream:** https://github.com/mvanhorn/last30days-skill
- **Pinned commit:** `122158415ae421da83e739f2668032f6bc78d39c`
- **Version:** 3.3.2 (released 2026-06-06)
- **License:** MIT, Copyright (c) 2026 Matt Van Horn. Preserved verbatim at `LICENSE`.
- **Adopted:** 2026-06-14. Ported into MOM 2026-08-23.

## What was vendored

- `scripts/` the full engine: `last30days.py`, `briefing.py`, `watchlist.py`, `store.py`, `verify_v3.py`, helper shell scripts, and `scripts/lib/` (59 source adapters plus `lib/vendor/bird-search/`, the free X/Twitter client run via node).
- `references/` upstream `references/` plus the complete upstream `SKILL.md` (saved as `references/UPSTREAM_SKILL.md`), `CONFIGURATION.md`, `CONCEPTS.md`, `AGENTS.md`.

## What was deliberately excluded

- `assets/` 14 MB of README demo media (images, mp3). Not needed for function.
- `tests/`, `fixtures/`, `conftest.py` dev-only. Validation is run against a fresh clone during updates (see below), not carried in the bot tree.
- `hooks/` a Claude Code plugin `SessionStart` hook (`check-config.sh`, a cosmetic welcome/config banner). MOM has its own skill hook system and does not consume Claude Code plugin hooks, so wiring it would do nothing. Left out by design.
- `.claude-plugin/`, `gemini-extension.json`, `.agents/` manifests for other host runtimes.
- `CHANGELOG.md`, `uv.lock`, `pyproject.toml` build/release metadata. The engine has zero Python dependencies (`dependencies = []`), so no install step is required.

## Verified state at adoption

- Upstream `pytest` suite: all pass, 4 skipped (network-gated), exit 0.
- `--diagnose` from the vendored location: free sources live = `reddit, youtube, hackernews, polymarket, github`; `has_github: true`, `bird_installed: true` (X not authed). Re-run it after install: the YouTube and GitHub rows depend on `yt-dlp` and `gh` being present.
- `--mock` offline pipeline: exit 0.
- Live HackerNews pull ("claude code"): exit 0, fetched and ranked 4 real stories (~25s).

## No local modifications to engine code

The `scripts/` and `references/` trees are byte-for-byte upstream at the pinned commit. Keep it that way so updates stay a clean replace. Host-specific behavior lives only in the top-level `SKILL.md`.

## How to update

```bash
TMP=$(mktemp -d)
git clone https://github.com/mvanhorn/last30days-skill "$TMP/l30d"
cd "$TMP/l30d"
git checkout <new-tag-or-commit>

# integrity gate: must be green before adopting
python3 -m pytest -q

# replace engine + references (NOT the MOM-facing SKILL.md / VENDOR.md / deps.json / notes.md)
DST="$(git rev-parse --show-toplevel)/skills/last30days"
rm -rf "$DST/scripts" "$DST/references"
cp -r "$TMP/l30d/skills/last30days/scripts" "$DST/scripts"
mkdir -p "$DST/references"
cp -r "$TMP/l30d/skills/last30days/references/." "$DST/references/"
cp "$TMP/l30d/skills/last30days/SKILL.md" "$DST/references/UPSTREAM_SKILL.md"
cp "$TMP/l30d/CONFIGURATION.md" "$TMP/l30d/CONCEPTS.md" "$TMP/l30d/AGENTS.md" "$DST/references/"
cp "$TMP/l30d/LICENSE" "$DST/LICENSE"
find "$DST" -name __pycache__ -type d -exec rm -rf {} +

# verify, then bump the pinned commit recorded above
"$DST/scripts/last30days.py" --diagnose
git -C "$TMP/l30d" rev-parse HEAD
```

Then update the **Pinned commit** and **Version** lines at the top of this file, and re-read the upstream `SKILL.md` diff for any new flags worth surfacing in the MOM-facing `SKILL.md`.
