# Price Monitor

Track prices and get alerts for changes.

## Usage

```bash
# Add product to track
python skills/price-monitor/scripts/price_tracker.py add "Product Name" "https://url" --selector ".price"

# Add with alert threshold
python skills/price-monitor/scripts/price_tracker.py add "Product Name" "https://url" --threshold 50.0

# Check all prices
python skills/price-monitor/scripts/price_tracker.py check

# Check prices (JSON output)
python skills/price-monitor/scripts/price_tracker.py check --json

# List tracked products
python skills/price-monitor/scripts/price_tracker.py list

# View price history
python skills/price-monitor/scripts/price_tracker.py history "Product Name"

# Remove product
python skills/price-monitor/scripts/price_tracker.py remove "Product Name"
```

## Data Storage

`~/.local/share/price-monitor/` - JSON database with price history

## Supported Sources

- Any website with accessible pricing (no heavy JS)
- Built-in selectors for Amazon, eBay, Skroutz
- Custom CSS selectors for specific sites

## Notes

- Some sites block scraping (use with respect)
- JavaScript-heavy sites may not work (use browser skill for those)
- Price history capped at 100 entries per product
