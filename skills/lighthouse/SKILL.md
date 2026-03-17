# Lighthouse

Website performance and SEO auditing.

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

# Quiet mode (just scores)
lighthouse https://example.com --quiet --chrome-flags="--headless"
```

## Categories

- **Performance**: Loading speed, core web vitals
- **Accessibility**: A11y issues, WCAG compliance
- **Best Practices**: Security, modern web standards
- **SEO**: Search engine optimization
- **PWA**: Progressive Web App checks

## Examples

"Run a Lighthouse audit on my website"
"Check the performance of this URL"
"Generate an accessibility report"
"What's the SEO score of this page?"

## Notes

- Requires Chrome/Chromium installed
- Use --headless for server environments
- First run may take a moment to initialize
