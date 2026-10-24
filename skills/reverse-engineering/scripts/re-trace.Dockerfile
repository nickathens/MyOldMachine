# Native runtime recorder: strace, to record what a Linux program DOES when it
# runs (files it opens, network it attempts, other programs it starts). The
# target is EXECUTED, so this runs ONLY through sandbox.py (no network,
# read-only root, no capabilities, non-root, memory/pids/time capped); strace
# traces its own child, which needs no extra capability. Base pinned by digest.
# sandbox.py derives the image tag from this file's hash.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends strace; \
    rm -rf /var/lib/apt/lists/*; \
    strace -V
