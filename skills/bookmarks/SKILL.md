# Bookmarks

Manage bookmarks using buku.

## Add Bookmark

```bash
# Add a URL
buku -a https://example.com

# Add with title and tags
buku -a https://example.com "Example Site" tag1,tag2

# Add with description
buku -a https://example.com "Title" tag1 --comment "Description here"
```

## Search Bookmarks

```bash
# Search by keyword
buku keyword

# Search by tag
buku --stag tag1

# Search multiple terms (AND)
buku keyword1 keyword2

# List all tags
buku --stag
```

## List Bookmarks

```bash
# List all
buku -p

# List last 10
buku -p -n 10

# List by index
buku -p 1
```

## Delete Bookmark

```bash
# Delete by index
buku -d 1

# Delete by URL
buku -d --url https://example.com
```

## Export/Import

```bash
# Export to HTML
buku -e bookmarks.html

# Export to Markdown
buku -e bookmarks.md

# Import from HTML
buku -i bookmarks.html
```

## Database

Bookmarks stored in: `~/.local/share/buku/bookmarks.db`
