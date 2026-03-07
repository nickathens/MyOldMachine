# Web Scraper

Advanced web scraping with Playwright - handles JavaScript-rendered pages.

## Usage

```bash
# Take a screenshot
python ~/claude-telegram-bot/skills/scraper/scripts/scrape.py screenshot "https://example.com" output.png

# Get page content as text
python ~/claude-telegram-bot/skills/scraper/scripts/scrape.py content "https://example.com"

# Get specific elements by CSS selector
python ~/claude-telegram-bot/skills/scraper/scripts/scrape.py content "https://example.com" --selector "h1, h2"

# Save page as PDF
python ~/claude-telegram-bot/skills/scraper/scripts/scrape.py pdf "https://example.com" output.pdf

# Extract all links
python ~/claude-telegram-bot/skills/scraper/scripts/scrape.py links "https://example.com"

# Extract tables
python ~/claude-telegram-bot/skills/scraper/scripts/scrape.py tables "https://example.com"
```

## Examples

User: "screenshot this website" + URL
User: "extract all links from this page"
User: "scrape the prices from this product page"
User: "get all the tables from this Wikipedia article"
User: "save this article as PDF"

## Features

- **JavaScript support** - Renders dynamic content (React, Vue, etc.)
- **Screenshots** - Full page or viewport only
- **PDF export** - Print-quality output
- **Content extraction** - With CSS selectors
- **Link harvesting** - All or external only
- **Table extraction** - Structured data from HTML tables

## Notes

- Uses headless Chromium browser
- Handles SPAs and dynamic content
- 30-second timeout for page loads
- Respects robots.txt and site terms
