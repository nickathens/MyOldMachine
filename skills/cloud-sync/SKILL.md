# Cloud Sync

Sync files to/from cloud storage (Google Drive, Dropbox, S3, etc.) using rclone.

## Setup Required

Every example below needs a configured remote first. `rclone config` is an
interactive wizard that needs a terminal, and Google Drive or Dropbox also
need a browser sign-in: that is the user's step at the machine, not something
a bot turn can do. `rclone listremotes` shows what exists.

## Usage

```bash
# List configured remotes
rclone listremotes

# List files in remote
rclone ls gdrive:
rclone ls gdrive:/folder/path

# Copy file to cloud
rclone copy local_file.pdf gdrive:/backups/

# Copy folder to cloud
rclone copy ./project gdrive:/projects/myproject

# Sync folder: makes the remote IDENTICAL to local, DELETING remote files local lacks.
# Always dry-run first and show the user the list; prefer copy, which never deletes.
rclone sync ./folder gdrive:/folder --dry-run
rclone sync ./folder gdrive:/folder

# Download from cloud
rclone copy gdrive:/path/to/file.pdf ./local/

# Mount cloud as local folder (FUSE); unmount: fusermount -u ~/gdrive (Linux), umount ~/gdrive (macOS)
rclone mount gdrive: ~/gdrive --daemon

# Get info about a remote file
rclone lsl gdrive:/path/to/file
```

## Common Remotes

| Remote | Description |
|--------|-------------|
| `gdrive:` | Google Drive |
| `dropbox:` | Dropbox |
| `s3:` | Amazon S3 |
| `b2:` | Backblaze B2 |
| `onedrive:` | Microsoft OneDrive |

## Examples

User: "upload this to Google Drive" + file
User: "sync my project folder to the cloud"
User: "download my backup from Dropbox"
User: "list my files on Google Drive"

## Notes

- Requires one-time setup via `rclone config` (see above)
- Supports 40+ cloud storage providers
- `sync` and `copy` go one way, source to destination; `rclone bisync` goes both
  ways (its first run needs `--resync`)
- `--progress` redraws a live display: fine in a terminal, noise in a turn's output
- Encrypted remotes available for sensitive data
