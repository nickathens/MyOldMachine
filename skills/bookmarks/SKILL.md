# Bookmarks

Manage bookmarks with buku.

Every command below was run against buku 5.1 on a throwaway database on 2026-09-27.

In the bot always pass `--nostdin`, as the first argument (otherwise buku
reads stdin and stops with "buku: waiting for input") and `--nc` (no colour codes); searches also
take `--np` (no interactive prompt after the results).

## Add

```bash
buku --nostdin --nc -a https://example.com tag1,tag2 --title "Example Site" --comment "Description here"
buku --nostdin --nc -a https://example.com tag1,tag2 --offline     # do not fetch the page title
```

The title needs `--title`: words after the URL are tags, so
`buku -a URL "Example Site" tag1,tag2` (what this file used to show) saves
"example site tag1" as a tag and fetches the title from the page instead.

## Search

```bash
buku --nostdin --np --nc -s keyword1 keyword2     # ANY of the words
buku --nostdin --np --nc -S keyword1 keyword2     # ALL of the words
buku --nostdin --np --nc --stag tag1              # by tag
buku --nostdin --np --nc --stag                   # list all tags with counts
```

A bare `buku keyword1 keyword2` also matches ANY word, not all (the old
text said AND). In the results, `1. Example Org [2]`, the number in
brackets is the bookmark's index; the leading number is only the position
in the results.

## List

```bash
buku --nostdin --nc -p          # all
buku --nostdin --nc -p -10      # the last 10
buku --nostdin --nc -p 1        # index 1
```

(`-p -n 10` prints the first 10, not the last.)

## Delete

```bash
buku --nostdin --np --nc -s example.com           # find it; note the [index]
buku --nostdin --tacit --nc -d 5                  # delete index 5
```

**Never run `-d` without an index.** `buku -d --url https://example.com`,
the old "delete by URL" example, ignores the URL and asks "Remove ALL
bookmarks? (y/n)": one "y" empties the library. Confirm the index with the
user before deleting, and never delete more than they asked for.

## Export / Import

```bash
buku --nostdin --nc -e bookmarks.html     # or .md, .org, .xbel
buku --nostdin --tacit --nc -i bookmarks.html
```

## Database

`~/.local/share/buku/bookmarks.db`, created by the first add.
