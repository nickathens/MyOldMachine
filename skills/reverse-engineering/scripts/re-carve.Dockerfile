# Tier 3 carving: binwalk, for finding and (under the gate) extracting files
# embedded in a blob (firmware, asset packs). Runs ONLY through sandbox.py (no
# network, read-only root unless an output mount is given, no capabilities,
# non-root, memory capped). Base pinned by digest; binwalk from Debian trixie.
# sandbox.py derives the image tag from this file's hash.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
# binwalk only identifies; it shells out to these to actually extract an embedded
# archive or stream. Free extractors only (no unrar/non-free).
RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        binwalk unzip p7zip-full xz-utils gzip tar zstd lz4 cpio; \
    rm -rf /var/lib/apt/lists/*; \
    binwalk --help >/dev/null
