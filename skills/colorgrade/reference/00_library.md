# The reading library

Grading guides that are legitimately free to download, kept outside this repo
because they are large. Downloaded 2026-08-04 to
`~/projects/COLORGRADE_LIBRARY/`.

| file | size | what it is for |
|---|---|---|
| `DaVinci-Resolve-20-Colorist-Guide.pdf` | 24 MB | Blackmagic's own certified training book. The authority on workflow order, grouping, qualifiers, windows, tracking. Published free by Blackmagic. |
| `DaVinci_Resolve_20_Reference_Manual.pdf` | 209 MB | The full manual. Use it to check what a control actually does before claiming it. |
| `KODAK-VISION-Color-Print-Film-2383-3383-data-sheet.pdf` | 0.6 MB | The primary source for the kodak2383 look. Sensitometric curves, D max, dye behaviour. |

Re fetch them with:

```bash
mkdir -p ~/projects/COLORGRADE_LIBRARY && cd ~/projects/COLORGRADE_LIBRARY
curl -sSLO https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf
curl -sSLO https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_20_Reference_Manual.pdf
curl -sSLO https://www.kodak.com/content/products-brochures/Film/KODAK-VISION-Color-Print-Film-2383-3383-data-sheet.pdf
```

Blackmagic publish the whole training series free at
`blackmagicdesign.com/products/davinciresolve/training`, including the editing,
Fairlight and Fusion volumes if those ever become relevant.

Also on this machine, and the authority for anything about DCTL or scripting:
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/`,
which carries `DaVinciCTL/README.txt`, `Scripting/README.txt`, and thirteen
sample .dctl files that are the only safe guide to which constructs Resolve's
own translator will accept.

## Deliberately not downloaded

The standard grading books, Van Hurkman's Color Correction Handbook, Cullen
Kelly's material, the Mixing Light library, are copyrighted and not free. They
are worth buying rather than pirating. Nothing in this skill depends on them:
every claim in `reference/` traces to a primary document that is free to read,
or to a measurement on this machine.
