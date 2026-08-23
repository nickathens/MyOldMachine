# Notes: last30days

Learnings discovered while adopting and running this skill. Newest first.

[2026-06-14] Free sources verified live via `--diagnose`: reddit, youtube, hackernews, polymarket, github. X is `bird_installed: true` but `bird_authenticated: false` (needs browser cookies or XAI_API_KEY). TikTok/Instagram/Threads need SCRAPECREATORS_API_KEY. Always re-check with `--diagnose` instead of assuming a source is on.

[2026-06-14] `--mock` uses an internal `providers.mock_runtime`, not the repo `fixtures/` dir, so the offline pipeline test works from the vendored copy even though fixtures/ was not vendored. Use it as a fast, no-network smoke test.

[2026-06-14] Engine is pure stdlib (`pyproject.toml` has `dependencies = []`) and self-locating (`Path(__file__).parent` + `sys.path.insert`), so it runs from any path as long as `scripts/lib/` stays a sibling of `last30days.py`. Requires Python 3.12 or newer; it needs no venv of its own.

[2026-06-14] When no `--plan` is passed the engine prints multi-line `[Planner]` banners to stderr explaining the model-as-planner contract. Filter them with `grep -vE '^\[Planner\]'` when surfacing output, or just pass `--plan` to silence them.

[2026-06-14] Source speed: HackerNews is fast (Algolia). Reddit/X fan-out can be slow or rate-limited. For quick checks use `--search reddit,youtube,hackernews --quick`. A live single-source HN pull took ~25s.

[2026-06-14] The upstream `SKILL.md` (134 KB) is full of host-specific marketing and rigid "voice/badge contracts" (Hermes, OpenClaw, clawhub) and a Claude-Code-plugin stale-clone self-check. None of that was vendored; the lean top-level SKILL.md drives the engine instead. The full upstream contract is preserved at references/UPSTREAM_SKILL.md for deep features (competitor mode, comparison rendering, the full query-plan schema).
