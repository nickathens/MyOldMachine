# Native behaviour oracle: run the ORIGINAL function's real machine code inside
# the Unicorn CPU emulator and compare it, input by input, against a rebuild.
# The original bytes are interpreted, never executed on the real CPU, so even a
# hostile binary only ever runs emulated; this image also carries gcc and the
# MinGW cross-compiler so a rebuild given as source is compiled here, in the
# sandbox, not on the host. Runs ONLY through sandbox.py (no network, read-only
# root, no capabilities, non-root, memory capped). Base pinned by digest; the
# Python wheels pinned by sha256. sandbox.py derives the image tag from this
# file's hash, so any edit rebuilds it on next use.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        gcc gcc-mingw-w64-x86-64 binutils libc6-dev; \
    rm -rf /var/lib/apt/lists/*; \
    python3 -m venv /opt/venv

# Pinned emulation + parsing libraries. --require-hashes makes the build refuse
# any wheel whose bytes do not match; on Python >= 3.9 none of these pull a dep.
RUN set -eu; \
    printf '%s\n' \
      "unicorn==2.1.4 --hash=sha256:9d6e6dea140560de4ebd8446661f7ef84a357d428c14a3ef09dacd306ec8c239" \
      "capstone==5.0.9 --hash=sha256:273fd8d747d2e35c88f91450be51a603ecfaafb00d96d9f315dcb8689c86193e" \
      "pefile==2024.8.26 --hash=sha256:76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f" \
      > /tmp/req.txt; \
    /opt/venv/bin/pip install --no-cache-dir --only-binary :all: --require-hashes -r /tmp/req.txt; \
    rm -f /tmp/req.txt; \
    /opt/venv/bin/python3 -c "import unicorn, capstone, pefile; print('unicorn', unicorn.__version__)"
ENV PATH=/opt/venv/bin:$PATH
