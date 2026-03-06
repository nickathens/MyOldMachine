# Downloads

Powerful file downloading with aria2: parallel connections, resume support, torrents.

## Usage

```bash
# Simple download
aria2c "https://example.com/file.zip"

# Download with custom filename
aria2c -o myfile.zip "https://example.com/file.zip"

# Download to specific directory
aria2c -d ~/Downloads "https://example.com/file.zip"

# Parallel download (16 connections)
aria2c -x 16 "https://example.com/largefile.iso"

# Resume interrupted download
aria2c -c "https://example.com/file.zip"

# Download multiple URLs from file
aria2c -i urls.txt

# Quiet mode (less output)
aria2c -q "https://example.com/file.zip"
```

## Common Options

| Option | Description |
|--------|-------------|
| `-x N` | N connections per server (max: 16) |
| `-s N` | Split file into N parts |
| `-c` | Continue/resume partial download |
| `-d DIR` | Download directory |
| `-o NAME` | Output filename |
| `-q` | Quiet mode |

## Notes

- Supports HTTP, HTTPS, FTP, BitTorrent, Metalink
- Auto-resumes if connection drops
- Use aria2 for large files. Use curl for API work.
