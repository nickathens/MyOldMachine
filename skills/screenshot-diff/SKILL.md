# Screenshot Diff

Visual regression testing for websites.

## Usage

```bash
# Compare two screenshots
python skills/screenshot-diff/scripts/screenshot_diff.py compare image1.png image2.png

# Compare with custom threshold
python skills/screenshot-diff/scripts/screenshot_diff.py compare image1.png image2.png --threshold 0.05

# Compare with diff output file
python skills/screenshot-diff/scripts/screenshot_diff.py compare image1.png image2.png --output diff.png

# Create side-by-side composite
python skills/screenshot-diff/scripts/screenshot_diff.py composite image1.png image2.png --output comparison.png
```

## ImageMagick CLI Alternative

```bash
# Get difference metric
compare -metric RMSE image1.png image2.png null: 2>&1

# Highlight differences
compare image1.png image2.png -compose src -highlight-color red diff.png
```

## Workflow

1. Capture baseline screenshot
2. Make changes
3. Capture new screenshot
4. Compare with diff tool
5. Review visual differences

## Notes

- Uses ImageMagick for pixel-level comparison
- Pillow for side-by-side composites
- Threshold controls sensitivity (0.0 = exact match required, 1.0 = ignore all differences)
