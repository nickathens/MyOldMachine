# Notes & Bookmarks

Manage notes, bookmarks, and knowledge using `nb`.

Every command below ran without a terminal on 2026-09-27, on a throwaway
notebook directory.

## First, once per machine

```bash
nb init          # safe to repeat
```

On a directory nb has not set up, the first `nb add` only initialises it
and the note is dropped without a word ("0 items" afterwards), checked
with nb 7.25.5 on 2026-09-27.

## Quick Commands

```bash
nb add "This is a note"
nb add "Meeting notes" --tags meeting,work
nb bookmark https://example.com          # fetches the page title
nb search "keyword"
nb search --tag meeting
nb list --no-color
nb show 1 --print --no-color
nb edit 1 --content "new text" --overwrite    # or --prepend; plain `nb edit` opens an editor and hangs the turn
nb delete 1 --force                            # only after the user confirmed which note
```

Without `--force`, `nb delete` asks for a y/N answer; with no terminal it
prints "Deleting: ..." and exits 0 having deleted nothing.

## Notebooks

```bash
nb notebooks add work
nb use work
nb notebooks
```

## Export

```bash
nb show 1 --print > /tmp/note.md
```

## Location

Notes stored in: `~/.nb/` (the default notebook is `home`).
