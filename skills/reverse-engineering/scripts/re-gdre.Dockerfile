# GDRE Tools (gdsdecomp, MIT): the games tier's Godot script and scene reader.
# It turns compiled GDScript (.gdc) back into source, binary scenes and
# resources into text, and rebuilds a whole project from a .pck or an exported
# game. Runs ONLY through sandbox.py (no network, read-only root, no
# capabilities, non-root, memory capped), so an untrusted game is only ever read
# in isolation. GDRE is itself an exported Godot program: headless it needs only
# the C library, and its bundled C# decompiler library sits beside it (found
# through its $ORIGIN runpath). Base pinned by digest, the release zip by
# sha256 (GitHub's published digest). sandbox.py derives the image tag from this
# file's hash, so any edit rebuilds it on next use.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

ARG GDRE_URL=https://github.com/GDRETools/gdsdecomp/releases/download/v2.7.4/GDRE_tools-v2.7.4-linux.zip
ARG GDRE_SHA256=3dc4f1244dda62e41e0c952ac00a69e60e07f3b0d5a211164806bf9d41a54758

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates wget unzip; \
    wget -q "$GDRE_URL" -O /tmp/gdre.zip; \
    echo "$GDRE_SHA256  /tmp/gdre.zip" | sha256sum -c -; \
    mkdir -p /opt/gdre; \
    unzip -q /tmp/gdre.zip -d /opt/gdre; \
    rm -f /tmp/gdre.zip; \
    apt-get purge -y wget unzip; apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*; \
    HOME=/tmp/gdre-home /opt/gdre/gdre_tools.x86_64 --headless --list-bytecode-versions | grep -q "4.5.0-stable"; \
    rm -rf /tmp/gdre-home
