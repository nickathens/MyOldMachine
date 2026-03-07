# Icon Generator Skill

Generate favicons, app icons, and icon sets.

## Capabilities

- **Favicon**: Generate all favicon sizes (16, 32, 48, 64, 128, 256)
- **App Icons**: iOS, Android, PWA icons
- **ICO files**: Multi-resolution .ico files
- **SVG to PNG**: Convert vector to raster at any size

## Script Location

`scripts/icongen.py` - Icon generation script

## Commands

```bash
# Generate favicon set from image
python icongen.py favicon input.png ./icons/

# Generate app icons (iOS + Android)
python icongen.py appicons input.png ./icons/

# Single icon at specific size
python icongen.py resize input.png output.png 512

# Create ICO file
python icongen.py ico input.png favicon.ico
```

## Output Sizes

### Favicon
- 16x16, 32x32, 48x48 (standard)
- 64x64, 128x128, 256x256 (high-res)
- favicon.ico (multi-res)

### iOS
- 180x180 (iPhone)
- 167x167 (iPad Pro)
- 152x152 (iPad)

### Android
- 192x192, 512x512 (PWA)
- 48, 72, 96, 144, 192 (launcher)

## Examples

"Generate favicons from this logo"
"Create iOS and Android app icons"
"Make a 512x512 icon from this image"
