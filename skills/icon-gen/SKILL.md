# Icon Generator Skill

Generate favicons, app icons, and icon sets.

## Capabilities

- **Favicon**: 16, 32, 48, 64, 128, 256 px PNGs plus a multi-resolution favicon.ico
- **App icons**: iOS (opaque, as the App Store requires), Android, PWA
- **Input**: PNG, JPEG (EXIF rotation applied), or SVG (rendered with Inkscape at 1024 px first)
- Art that is not square is centred on a transparent square, not stretched

## Commands

```bash
G=skills/icon-gen/scripts/icongen.py
python $G favicon logo.png ./icons/            # favicon PNGs + favicon.ico
python $G ios logo.png ./icons/ios/ --background "#0b0b0b"   # opaque fill (default white)
python $G android logo.png ./icons/android/
python $G pwa logo.png ./icons/pwa/
python $G all logo.svg ./icons/                # favicon, ios, android, pwa subfolders
python $G resize logo.png icon-512.png 512
python $G ico logo.png favicon.ico
```

There is no `appicons` command (the old doc named one); `all` or the
per-platform commands do that. Fixed 2026-09-27: wide logos were squashed
into the square, iOS icons kept an alpha channel, SVG input failed.

## Output Sizes

### Favicon
- 16x16, 32x32, 48x48 (standard)
- 64x64, 128x128, 256x256 (high-res)
- favicon.ico (multi-res)

### iOS (opaque)
- 180, 167, 152, 120, 87, 80, 76, 60, 58, 40, 29, 20

### Android
- 512, 192, 144, 96, 72, 48, 36

### PWA
- 512, 384, 256, 192, 144, 128, 96, 72, 48

## Examples

"Generate favicons from this logo"
"Create iOS and Android app icons"
"Make a 512x512 icon from this image"
