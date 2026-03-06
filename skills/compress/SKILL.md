# Compress

File compression and archive operations.

## Capabilities

- **ZIP**: Create, extract, list
- **TAR**: tar, tar.gz, tar.bz2, tar.xz
- **7z**: High compression (if installed)

## Commands

```bash
# ZIP
zip -r archive.zip folder/
unzip archive.zip -d output/
unzip -l archive.zip  # List contents

# TAR
tar -cvf archive.tar folder/
tar -czvf archive.tar.gz folder/
tar -xzvf archive.tar.gz -C output/
tar -tzvf archive.tar.gz  # List contents
```

## Notes

- zip and tar are available on all platforms by default
- 7z provides better compression but needs to be installed separately
