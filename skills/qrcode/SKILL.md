# QR Codes

Generate QR codes using qrcode.

## Generate QR Code Image

```bash
# Simple URL (the `qr` command of the qrcode package, 8.x)
qr --output /tmp/qr.png "https://example.com"

# With specific output file
python3 -c "
import qrcode
img = qrcode.make('https://example.com')
img.save('/tmp/qr.png')
print('Saved to /tmp/qr.png')
"
```

## Customized QR Code

```python
import qrcode

qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_L,
    box_size=10,
    border=4,
)
qr.add_data('https://example.com')
qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white")
img.save('/tmp/qr.png')
```

## QR Code as text

```bash
# The bot's stdout is a pipe, where plain `qr` writes PNG bytes; --ascii forces text
qr --ascii "https://example.com"
```

## Common Use Cases

```python
import qrcode

# WiFi network. In the name and password, escape \ ; , : and " with a backslash
wifi = "WIFI:T:WPA;S:NetworkName;P:Password;;"
img = qrcode.make(wifi)
img.save('/tmp/wifi_qr.png')

# Contact (vCard)
vcard = """BEGIN:VCARD
VERSION:3.0
N:Last;First
TEL:+1234567890
EMAIL:user@example.com
END:VCARD"""
img = qrcode.make(vcard)
img.save('/tmp/contact_qr.png')

# Plain text
img = qrcode.make("Hello World!")
img.save('/tmp/text_qr.png')
```

Greek text encodes as UTF-8 and reads back intact (checked 2026-09-27 by
decoding with OpenCV).

## Send to User

```bash
python3 -c "import qrcode; qrcode.make('https://example.com').save('/tmp/qr.png')"
python utils/send_to_telegram.py --user USER_ID --photo /tmp/qr.png --caption "QR Code"
```
