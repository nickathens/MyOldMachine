# Research

Data collection, aggregation, and research automation.

## Usage

```bash
# Add RSS feed to monitor
python skills/research/scripts/research.py rss add "Feed Name" "https://feed.url/rss"

# Check all feeds for new articles
python skills/research/scripts/research.py rss check

# List monitored feeds
python skills/research/scripts/research.py rss list

# Scrape a page
python skills/research/scripts/research.py scrape "https://url" --selector ".item"

# View collected articles
python skills/research/scripts/research.py articles --limit 10

# Add research note
python skills/research/scripts/research.py note add --topic "AI" --content "Key finding..."

# Export data
python skills/research/scripts/research.py export --format csv --output data.csv
python skills/research/scripts/research.py export --format markdown --type notes
```

## Data Storage

`~/.local/share/research/` - SQLite database and exports

## Ethics

- Respect robots.txt
- Don't overload servers
- Use for personal research only
- Check terms of service
