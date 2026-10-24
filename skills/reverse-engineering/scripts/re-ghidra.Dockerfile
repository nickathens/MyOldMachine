# Ghidra headless decompiler + JDK 21, for Tier 2 native analysis. Runs ONLY
# through sandbox.py (no network, read-only root, no capabilities, non-root,
# memory capped), so a hostile binary is decompiled in isolation and a heavy
# analysis cannot threaten the bot. Base pinned by digest; the Ghidra release is
# pinned by sha256. sandbox.py derives the image tag from this file's hash.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip
ARG GHIDRA_SHA256=ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends openjdk-21-jdk-headless ca-certificates wget unzip; \
    wget -q "$GHIDRA_URL" -O /tmp/ghidra.zip; \
    echo "$GHIDRA_SHA256  /tmp/ghidra.zip" | sha256sum -c -; \
    unzip -q /tmp/ghidra.zip -d /opt; \
    mv /opt/ghidra_* /opt/ghidra; \
    rm -f /tmp/ghidra.zip; \
    apt-get purge -y wget unzip; apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
    test -x /opt/ghidra/support/analyzeHeadless; \
    java -version
ENV GHIDRA_HOME=/opt/ghidra
