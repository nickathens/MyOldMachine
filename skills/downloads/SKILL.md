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

# Parallel download (16 connections: -x alone stays at the default split of 5)
aria2c -x 16 -s 16 "https://example.com/largefile.iso"

# Resume interrupted download
aria2c -c "https://example.com/file.zip"

# Download multiple URLs from file
aria2c -i urls.txt

# Download with speed limit (1MB/s)
aria2c --max-download-limit=1M "https://example.com/file.zip"

# Download torrent (without --seed-time=0 aria2c seeds until it has uploaded as much as it
# downloaded, so the command can run for hours after the file is complete)
aria2c --seed-time=0 "magnet:?xt=urn:btih:..."
aria2c --seed-time=0 file.torrent

# Linux: a long download that must outlive the turn (see Notes)
systemd-run --user --unit=dl-iso --collect aria2c -x 16 -s 16 -d ~/Downloads "https://example.com/largefile.iso"
journalctl --user -u dl-iso -n 5 --no-pager   # progress (never -f in a turn: it does not return)

# Quiet mode (less output)
aria2c -q "https://example.com/file.zip"
```

## Common Options

| Option | Description |
|--------|-------------|
| `-x N` | Up to N connections per server (default: 1, max: 16) |
| `-s N` | Download with N connections (default: 5); pair it with `-x` |
| `-k SIZE` | Smallest piece to split off (default 20M: a file under 40 MB is not split) |
| `-c` | Continue/resume partial download |
| `-d DIR` | Download directory |
| `-o NAME` | Output filename |
| `-q` | Quiet mode |

## Notes

- Supports HTTP, HTTPS, FTP, BitTorrent, Metalink
- Retries a dropped connection within the run; `-c` resumes a partial file in a new run
- The skill's stop hook kills the aria2c processes a turn started when that turn ends.
  Run a long download outside the turn (on Linux, `systemd-run --user` as above)
- Can download from multiple mirrors simultaneously
- Checksum verification available
