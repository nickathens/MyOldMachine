# QR Codes

Generate QR codes using qrcode.

## Generate QR Code Image

```bash
# Simple URL
qr "https://example.com" > /tmp/qr.png

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

## QR Code in Terminal (ASCII)

```bash
# Print to terminal
qr "https://example.com"
```

## Common Use Cases

```python
import qrcode

# WiFi network
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
