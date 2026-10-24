# Native reverse-engineering tools that parse untrusted binaries: radare2 and
# the binutils baseline. Runs ONLY through sandbox.py (no network, read-only
# root, no capabilities, non-root, memory capped). Base pinned by digest; the
# radare2 .deb is pinned by sha256. sandbox.py derives this image's tag from
# this file's hash, so any edit here rebuilds it on next use.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

ARG R2_DEB=radare2_6.2.4_amd64.deb
ARG R2_URL=https://github.com/radareorg/radare2/releases/download/6.2.4/radare2_6.2.4_amd64.deb
ARG R2_SHA256=7019eedc0e0e87d1f53d6b8f5fc62b898567679c5efdce7fbfc557c9e7655e90

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates wget file binutils xxd; \
    wget -q "$R2_URL" -O "/tmp/$R2_DEB"; \
    echo "$R2_SHA256  /tmp/$R2_DEB" | sha256sum -c -; \
    apt-get install -y --no-install-recommends "/tmp/$R2_DEB"; \
    rm -f "/tmp/$R2_DEB"; \
    rm -rf /var/lib/apt/lists/*; \
    r2 -v
