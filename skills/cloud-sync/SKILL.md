# Cloud Sync

Sync files to/from cloud storage (Google Drive, Dropbox, S3, etc.) using rclone.

## Setup Required

Before using, configure a remote:
```bash
rclone config
```

This creates an interactive wizard to connect cloud services.

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

# Sync folder (mirror local to remote)
rclone sync ./folder gdrive:/folder --progress

# Download from cloud
rclone copy gdrive:/path/to/file.pdf ./local/

# Mount cloud as local folder (FUSE)
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

- Requires one-time setup via `rclone config`
- Supports 40+ cloud storage providers
- Sync is one-way (local → remote) - use `bisync` for two-way
- Progress shown with `--progress` flag
- Encrypted remotes available for sensitive data
