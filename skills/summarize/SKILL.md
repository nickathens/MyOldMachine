# URL Summarizer

Fetch and summarize web articles, blog posts, and other URLs.

## Usage

When the user sends a URL or asks you to summarize a link:

1. Run the fetch script to get the content:
```bash
python $SKILL_DIR/scripts/summarize_url.py "<url>"
```

2. Read the output and provide a concise summary with:
   - Main topic/thesis
   - Key points (3-5 bullet points)
   - A one-sentence takeaway

## Supported Content

- HTML pages (articles, blogs, news)
- Plain text
- JSON data

## Limitations

- Maximum content: 15,000 characters
- Some sites may block automated access
- Paywalled content won't be accessible
