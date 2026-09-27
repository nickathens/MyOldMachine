# URL Summarizer

Fetch and summarize web articles, blog posts, and other URLs.

## Usage

When the user sends a URL or asks you to summarize a link:

1. Run the fetch script to get the content:
```bash
python $SKILL_DIR/scripts/summarize_url.py "<url>"
python $SKILL_DIR/scripts/summarize_url.py "<url>" --max-chars 0   # the whole text
```

2. Read the output and provide a concise summary with:
   - Main topic/thesis
   - Key points (3-5 bullet points)
   - A one-sentence takeaway

## Supported Content

- HTML pages (articles, blogs, news): of all the article-like containers on the page, the one with the most text is taken (the first one is often a "Related" teaser card), paragraphs kept on their own lines, the page's own charset honoured (older Greek sites declare windows-1253 only in `<meta>`)
- PDF links (text through markitdown)
- Plain text
- JSON data

## Limitations

- 15,000 characters by default; a longer text says `[truncated: N of M characters shown]` at the end, and `--max-chars 0` prints all of it. Summarise from the whole text when it is truncated
- Pages built by JavaScript (many social and app sites) come back nearly empty: use the browser instead
- Some sites may block automated access
- Paywalled content won't be accessible
