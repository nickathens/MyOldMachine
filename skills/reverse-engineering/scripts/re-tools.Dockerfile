# Command-line analysis tools that parse untrusted files, run only through
# sandbox.py (no network, read-only, no capabilities). Base pinned by digest;
# the tag sandbox.py uses is derived from this file's hash, so editing it
# rebuilds the image on next use.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
RUN apt-get update \
 && apt-get install -y --no-install-recommends wabt \
 && rm -rf /var/lib/apt/lists/* \
 && wasm-objdump --version
