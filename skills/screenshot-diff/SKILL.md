# Screenshot Diff

Visual regression testing: are two screenshots the same, and if not, where do
they differ.

# Create side-by-side composite
python skills/screenshot-diff/scripts/screenshot_diff.py composite image1.png image2.png --output comparison.png
```

```bash
D=skills/screenshot-diff/scripts/screenshot_diff.py

# Compare: exit 0 when they match, 1 when they differ (so a pipeline can gate on it)
python $D compare before.png after.png -o diff.png

# Stricter or looser: share of pixels allowed to change, and per pixel tolerance
python $D compare before.png after.png --threshold 0 --fuzz 0.01

# Side by side
python $D composite before.png after.png -o side.png
```

What it reports: the number and share of pixels whose colour moved by more
than `--fuzz` (2 percent of the range by default, which absorbs PNG and font
rendering noise), the bounding box of the change, and up to five changed
regions, largest first. The diff image is the second screenshot faded, with
every changed pixel in red. A size change is always a difference; the area
only one image has counts as changed.

The pair matches when the changed share is at most `--threshold` (0.05
percent by default, about a thousand pixels on a 1920x1080 frame).

Before 2026-09-27 it compared whole-image RMSE against 0.1: a button that
turned red measured 0.0318 and passed as a match, and ImageMagick searched a
smaller screenshot inside a taller one, so a page that grew 220 px measured
0.0000, also a match.

## Workflow

1. Capture a baseline screenshot (the media skill's screenshot.py)
2. Make changes
3. Capture the new screenshot at the same viewport size
4. Compare, then look at the diff image
5. Treat dynamic content (dates, carousels, ads) as expected differences, or crop it out first

## Notes

- Uses ImageMagick for pixel-level comparison
- Pillow for side-by-side composites
- Threshold controls sensitivity (0.0 = exact match required, 1.0 = ignore all differences)
