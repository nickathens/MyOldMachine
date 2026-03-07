# Notes & Bookmarks

Manage notes, bookmarks, and knowledge using `nb`.

## Quick Commands

```bash
# Add a note
nb add "This is a note"

# Add a bookmark
nb bookmark https://example.com

# Search notes
nb search "keyword"

# List all notes
nb list

# Show a specific note
nb show 1

# Edit a note
nb edit 1

# Delete a note
nb delete 1
```

## Notebooks

```bash
# Create a new notebook
nb notebooks add work

# Switch to a notebook
nb use work

# List notebooks
nb notebooks
```

## Tags

```bash
# Add note with tags
nb add "Meeting notes" --tags meeting,work

# Search by tag
nb search --tag meeting
```

## Export

```bash
# Export a note
nb show 1 --print > /tmp/note.md
```

## Location

Notes stored in: `~/.nb/`
