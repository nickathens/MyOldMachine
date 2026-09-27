# Price Monitor

Track product prices on web pages and keep a history.

## Capabilities

- **Track products**: read the price from a product page with CSS selectors
- **Threshold**: `check` marks a product ALERT when its price is at or under
  the threshold set with `add --threshold`
- **History**: the last 100 checks per product

There is no cross-shop comparison (the old doc listed one), and no alert
reaches Telegram on its own: see "Alerts" below.

## Usage

```bash
P=skills/price-monitor/scripts/price_tracker.py
python $P add "Sony WH-1000XM6" "https://www.skroutz.gr/s/..." --threshold 300
python $P add "Lens" "https://shop.example/lens" --selector ".product-price .amount"
python $P check            # fetch every product, report changes and alerts
python $P list
python $P history "Lens"   # last 10 prices (--json for all)
python $P remove "Lens"
```

- Check the first `add`: it prints "Could not fetch initial price" when no
  selector matched or the shop blocked the request. Amazon and eBay have
  built-in selectors, but both often answer scripts with a captcha page.
- Prices are read the way shops write them: "1.299,00 €", "1.299 €",
  "$1,299.00", "12 999,50 €" are all 1299 or 12999.50, and the number next to
  the currency sign wins over other numbers in the element. A machine
  readable `content` attribute (itemprop="price") is used first when present.
  Before 2026-09-27 "1.299 €" was read as 1.299 (a 99.9 percent "drop" and a
  false alert).
- JavaScript-rendered prices are not in the page's HTML and cannot be read.

## Alerts

Nothing runs `check` on its own, and no alert reaches Telegram by itself:
`check` prints the ALERT lines, so whatever runs it (a turn, a scheduled job)
has to pass them on.

## Data Storage

`~/.local/share/price-monitor/products.json`

## Data Storage

"Track this product and tell me its price history"
"Add this gear to my watchlist"
"What's the current price of my tracked items?"

## Notes

- Some sites block scraping (use with respect; do not check more than a few
  times a day)
- JavaScript-heavy sites may not work
