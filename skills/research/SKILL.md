# Research Skill

Data collection, aggregation, and research automation.

## Capabilities

- **Web scraping**: Extract data from websites
- **RSS feeds**: Monitor and aggregate news/blogs
- **API queries**: Fetch data from public APIs
- **Data storage**: SQLite database for collected data
- **Export**: JSON, CSV, markdown reports
- **Dialectical analysis**: Structured bull/bear evaluation framework

## Script Location

`scripts/research.py` - Research automation engine
`scripts/dialectic.py` - Dialectical analysis framework (bull/bear evaluation)

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

## Dialectical Analysis

Structured bull/bear evaluation framework. Forces steel-manning of both sides before synthesis. FTS5-indexed and searchable. Exports to `~/research/<topic>/` for long-term reference.

### JSON Input Format

```json
{
  "proposition": "We should use X for Y",
  "context": "Brief context for the evaluation",
  "bull_case": "Strongest arguments FOR (markdown)",
  "bear_case": "Strongest arguments AGAINST (markdown)",
  "cross_examination": "Each side responds to the other's strongest point",
  "synthesis": "Final recommendation with reasoning",
  "recommendation": "FOR | AGAINST | CONDITIONAL",
  "confidence": 85,
  "flip_conditions": "What would change the recommendation",
  "tags": "comma,separated,tags"
}
```

Required fields: `proposition`, `bull_case`, `bear_case`, `cross_examination`, `synthesis`, `recommendation`, `confidence`

### Dialectic Commands

```bash
# Store analysis (JSON via stdin or file)
echo '{"proposition": "...", ...}' | python skills/research/scripts/dialectic.py store
python skills/research/scripts/dialectic.py store --input analysis.json

# Search past analyses (FTS5 — supports boolean, phrase, prefix queries)
python skills/research/scripts/dialectic.py search "video export"
python skills/research/scripts/dialectic.py search "rust AND audio"
python skills/research/scripts/dialectic.py search "present*"

# List recent analyses
python skills/research/scripts/dialectic.py list
python skills/research/scripts/dialectic.py list --limit 20 --tag tooling

# View full analysis
python skills/research/scripts/dialectic.py view <id>

# Export to ~/research/<topic>/
python skills/research/scripts/dialectic.py export <id>                    # topic = first tag or "general"
python skills/research/scripts/dialectic.py export <id> --topic audio      # explicit topic subfolder
python skills/research/scripts/dialectic.py export <id> --dir /other/path  # custom base directory

# Delete
python skills/research/scripts/dialectic.py delete <id>

# Rebuild FTS5 index
python skills/research/scripts/dialectic.py rebuild
```

## Data Storage

`~/.local/share/research/` - Research database and exports

## Ethics

- Respect robots.txt
- Don't overload servers
- Use for personal research only
- Check terms of service
