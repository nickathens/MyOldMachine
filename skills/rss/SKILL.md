# RSS Feeds

Parse and read RSS/Atom feeds using feedparser.

## Read a Feed

```python
import feedparser

feed = feedparser.parse("https://example.com/feed.xml")

print(f"Feed: {feed.feed.title}")
print(f"Entries: {len(feed.entries)}")

for entry in feed.entries[:5]:
    print(f"- {entry.title}")
    print(f"  {entry.link}")
    print(f"  {entry.get('published', 'No date')}")
    print()
```

## One-liner

```bash
python3 -c "
import feedparser
feed = feedparser.parse('https://news.ycombinator.com/rss')
for e in feed.entries[:5]:
    print(f'* {e.title}')
    print(f'  {e.link}\n')
"
```

## Get Full Content

```python
import feedparser

feed = feedparser.parse("https://example.com/feed.xml")

for entry in feed.entries[:3]:
    print(f"Title: {entry.title}")
    print(f"Link: {entry.link}")
    print(f"Published: {entry.get('published', 'N/A')}")

    if 'summary' in entry:
        print(f"Summary: {entry.summary[:200]}...")
    print("---")
```

## Common Feeds

- Hacker News: `https://news.ycombinator.com/rss`
- Reddit (any sub): `https://www.reddit.com/r/SUBREDDIT/.rss`
- GitHub releases: `https://github.com/USER/REPO/releases.atom`
