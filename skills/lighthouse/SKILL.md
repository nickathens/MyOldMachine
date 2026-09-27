# Lighthouse

Website performance, accessibility, best-practice and SEO audits with
Google Lighthouse.

## Commands

```bash
# Full audit (JSON output)
lighthouse https://example.com --output json --output-path report.json --chrome-flags="--headless"

# HTML report
lighthouse https://example.com --output html --output-path report.html --chrome-flags="--headless"

# Specific categories only
lighthouse https://example.com --only-categories=performance,accessibility --chrome-flags="--headless"

# Desktop mode (default is mobile)
lighthouse https://example.com --preset=desktop --chrome-flags="--headless"

```

## Categories

Lighthouse 13 has performance, accessibility, best-practices, seo and
agentic-browsing (how well AI agents can browse the page). There is no PWA
category any more; Lighthouse 12 removed it.

`--quiet` only silences the log; nothing is printed to stdout, so read the
scores from the JSON report. Do not use `--view`: it opens the report in a
browser on the desktop. Send the HTML report instead.

## Examples

"Run a Lighthouse audit on my website"
"Check the performance of this URL"
"Generate an accessibility report"
"What's the SEO score of this page?"

## Notes

- Requires Chrome/Chromium installed
- Use --headless for server environments
- Scores vary a little between runs (network, CPU load); compare runs made
  the same way, and run twice before calling a regression.
