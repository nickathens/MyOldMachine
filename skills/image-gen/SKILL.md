# Image Generation

Generate images from text prompts using Pollinations.ai. Free, no API key required, works globally.

## Commands

```bash
# Basic generation (768x768)
python skills/image-gen/scripts/generate.py "a futuristic cityscape at sunset" -o /tmp/city.png

# Custom dimensions (max 768 per side)
python skills/image-gen/scripts/generate.py "a wide landscape" -o /tmp/wide.png -W 768 -H 432
python skills/image-gen/scripts/generate.py "a tall tower" -o /tmp/tower.png -W 432 -H 768

# With seed for reproducibility
python skills/image-gen/scripts/generate.py "a cat" -o /tmp/cat.png -s 42

# AI-enhanced prompt (Pollinations rewrites your prompt for better results)
python skills/image-gen/scripts/generate.py "cat on roof" -o /tmp/cat.png --enhance
```

## Options

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Output file path (default: /tmp/generated_image.png) |
| `-W`, `--width` | Image width, 64-768 (default: 768) |
| `-H`, `--height` | Image height, 64-768 (default: 768) |
| `-s`, `--seed` | Random seed for reproducible results |
| `--enhance` | Let AI enhance the prompt for better results |

## Notes

- Model: sana (Pollinations free tier)
- No API key or environment variables needed
- Resolution: 64x64 to 768x768 (clamped automatically)
- Output format: JPEG
- Rate limit: ~1 request per 15 seconds on free tier (auto-retries up to 3x)
- For best results, use detailed descriptive prompts
- The `--enhance` flag is useful for short/vague prompts
