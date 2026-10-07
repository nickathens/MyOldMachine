#!/usr/bin/env python3
"""
Price monitoring and tracking system
"""
import json
import re
import sys
import argparse
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

DATA_DIR = Path.home() / '.local' / 'share' / 'price-monitor'
DB_FILE = DATA_DIR / 'products.json'

# Common price selectors for popular sites
SITE_SELECTORS = {
    'amazon': ['.a-price-whole', '#priceblock_ourprice', '.a-offscreen'],
    'ebay': ['.x-price-primary span', '.notranslate'],
    'skroutz': ['.price', '.product-price'],
    'default': ['.price', '.product-price', '[class*="price"]', '[itemprop="price"]']
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

def init_db():
    """Initialize database directory and file"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        DB_FILE.write_text('{}', encoding="utf-8")

def load_db():
    """Load products database"""
    init_db()
    return json.loads(DB_FILE.read_text(encoding="utf-8"))

def save_db(data):
    """Save products database"""
    DB_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

# A price token: digits with ',' or '.' marks, or thousands grouped by spaces (12 999,50)
NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?|\d[\d.,]*\d|\d")
CURRENCY = re.compile(r"[€$£]|EUR|USD|GBP|ευρώ", re.IGNORECASE)


def _to_number(token):
    """'1.299,00' 1299.0, '1,299.00' 1299.0, '49,90' 49.9, '1.299' 1299.0, '1,299' 1299.0.

    With both marks the last one is the decimal mark. With one kind, a mark that
    repeats or has exactly three digits after it groups thousands, which is how
    prices are written (a price has two decimals, never three).
    """
    t = re.sub(r"[ \u00a0\u202f]", "", token)
    if "." in t and "," in t:
        decimal = "." if t.rfind(".") > t.rfind(",") else ","
        t = t.replace("," if decimal == "." else ".", "").replace(decimal, ".")
    elif "." in t or "," in t:
        mark = "." if "." in t else ","
        parts = t.split(mark)
        t = "".join(parts) if len(parts) > 2 or len(parts[-1]) == 3 else t.replace(mark, ".")
    try:
        return float(t)
    except ValueError:
        return None


def extract_price(text):
    """The price in a text: the number beside a currency mark if there is one, else the first.

    Linux bot review 2026-09-27: it took the first run of digits and read any single
    '.' or ',' as a decimal point, so "1.299 €" and "Από 1.049€" came out as
    1.299 and 1.049 (a 99.9 percent "drop" and a false threshold alert), and
    "Τιμή 2 τεμάχια: 49,90 €" came out as 2.
    """
    if not text:
        return None
    tokens = list(NUMBER.finditer(text))
    if not tokens:
        return None

    def beside_currency(m):
        return bool(CURRENCY.search(text[max(0, m.start() - 4):m.start()])
                    or CURRENCY.search(text[m.end():m.end() + 4]))

    chosen = next((m for m in tokens if beside_currency(m)), tokens[0])
    return _to_number(chosen.group())


def _price_of(elem):
    """An element's price: its machine readable attribute (itemprop content) first, then its text."""
    for attr in ("content", "data-price", "value"):
        if elem.has_attr(attr):
            price = extract_price(str(elem[attr]))
            if price is not None:
                return price
    return extract_price(elem.get_text(" "))


def get_selectors_for_url(url):
    """Get appropriate CSS selectors for URL"""
    url_lower = url.lower()
    for site, selectors in SITE_SELECTORS.items():
        if site in url_lower:
            return selectors
    return SITE_SELECTORS['default']

def fetch_price(url, custom_selector=None):
    """Fetch current price from URL"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # The custom selector first, then the site's; every match of a selector
        # is tried in page order (the first match is often a label with no number)
        selectors = ([custom_selector] if custom_selector else []) + get_selectors_for_url(url)
        for selector in selectors:
            for elem in soup.select(selector):
                price = _price_of(elem)
                if price is not None:
                    return price

        return None
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None

def add_product(name, url, selector=None, threshold=None):
    """Add product to tracking list"""
    db = load_db()

    # Fetch initial price
    current_price = fetch_price(url, selector)

    db[name] = {
        'url': url,
        'selector': selector,
        'threshold': threshold,
        'added': datetime.now().isoformat(),
        'history': []
    }

    if current_price:
        db[name]['history'].append({
            'price': current_price,
            'date': datetime.now().isoformat()
        })
        db[name]['last_price'] = current_price
        print(f"Added '{name}' - Current price: {current_price}")
    else:
        print(f"Added '{name}' - Could not fetch initial price")

    save_db(db)
    return db[name]

def remove_product(name):
    """Remove product from tracking"""
    db = load_db()
    if name in db:
        del db[name]
        save_db(db)
        print(f"Removed '{name}'")
        return True
    print(f"Product '{name}' not found")
    return False

def check_prices():
    """Check all tracked products and return updates"""
    db = load_db()
    updates = []

    for name, data in db.items():
        current = fetch_price(data['url'], data.get('selector'))

        if current is None:
            updates.append({
                'name': name,
                'status': 'error',
                'message': 'Could not fetch price'
            })
            continue

        last_price = data.get('last_price')
        threshold = data.get('threshold')

        # Record history
        data['history'].append({
            'price': current,
            'date': datetime.now().isoformat()
        })
        data['last_price'] = current

        # Keep only last 100 entries
        if len(data['history']) > 100:
            data['history'] = data['history'][-100:]

        update = {
            'name': name,
            'current': current,
            'previous': last_price,
            'url': data['url'],
            'status': 'ok'
        }

        if last_price:
            change = current - last_price
            change_pct = (change / last_price) * 100
            update['change'] = change
            update['change_pct'] = change_pct

            if change < 0:
                update['status'] = 'dropped'
            elif change > 0:
                update['status'] = 'increased'

        if threshold and current <= threshold:
            update['status'] = 'alert'
            update['message'] = f'Price below threshold ({threshold})'

        updates.append(update)

    save_db(db)
    return updates

def list_products():
    """List all tracked products"""
    db = load_db()
    products = []

    for name, data in db.items():
        products.append({
            'name': name,
            'url': data['url'],
            'last_price': data.get('last_price'),
            'threshold': data.get('threshold'),
            'history_count': len(data.get('history', [])),
            'added': data.get('added')
        })

    return products

def get_history(name):
    """Get price history for a product"""
    db = load_db()
    if name not in db:
        return None
    return db[name].get('history', [])

def format_report(updates):
    """Format price check results for display"""
    lines = ["Price Check Report", "=" * 40]

    for u in updates:
        if u['status'] == 'error':
            lines.append(f"❌ {u['name']}: {u.get('message', 'Error')}")
        elif u['status'] == 'alert':
            lines.append(f"🚨 {u['name']}: {u['current']:.2f} - ALERT! {u.get('message')}")
        elif u['status'] == 'dropped':
            lines.append(f"📉 {u['name']}: {u['current']:.2f} ({u['change']:.2f}, {u['change_pct']:.1f}%)")
        elif u['status'] == 'increased':
            lines.append(f"📈 {u['name']}: {u['current']:.2f} (+{u['change']:.2f}, +{u['change_pct']:.1f}%)")
        else:
            lines.append(f"✓ {u['name']}: {u['current']:.2f}")

    return '\n'.join(lines)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Price tracking system')
    parser.add_argument('action', choices=['add', 'remove', 'check', 'list', 'history'])
    parser.add_argument('name', nargs='?', help='Product name')
    parser.add_argument('url', nargs='?', help='Product URL')
    parser.add_argument('--selector', '-s', help='CSS selector for price')
    parser.add_argument('--threshold', '-t', type=float, help='Alert threshold')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    if args.action == 'add':
        if not args.name or not args.url:
            print("Error: name and url required for add")
            sys.exit(1)
        result = add_product(args.name, args.url, args.selector, args.threshold)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == 'remove':
        if not args.name:
            print("Error: name required for remove")
            sys.exit(1)
        remove_product(args.name)

    elif args.action == 'check':
        updates = check_prices()
        if args.json:
            print(json.dumps(updates, indent=2, ensure_ascii=False))
        else:
            print(format_report(updates))

    elif args.action == 'list':
        products = list_products()
        if args.json:
            print(json.dumps(products, indent=2, ensure_ascii=False))
        else:
            if not products:
                print("No products tracked")
            else:
                for p in products:
                    price = f"{p['last_price']:.2f}" if p['last_price'] else 'N/A'
                    threshold = f" (alert: {p['threshold']})" if p['threshold'] else ''
                    print(f"• {p['name']}: {price}{threshold}")

    elif args.action == 'history':
        if not args.name:
            print("Error: name required for history")
            sys.exit(1)
        history = get_history(args.name)
        if history is None:
            print(f"Product '{args.name}' not found")
        elif args.json:
            print(json.dumps(history, indent=2, ensure_ascii=False))
        else:
            for entry in history[-10:]:  # Last 10 entries
                print(f"{entry['date']}: {entry['price']:.2f}")
