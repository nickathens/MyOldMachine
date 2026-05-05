#!/usr/bin/env python3
"""CLI binary compatibility checks.

Refuses to install Claude Code CLI / OpenAI Codex CLI when the host OS is
known to be too old for the binary to load. Without this gate, installing
on (for example) macOS 10.15 succeeds at the npm step but every later
invocation crashes with a cryptic dyld "Symbol not found" error.

Compat floors are intentionally conservative — when in doubt, refuse and
let the user pick a different provider via the wizard's existing fallback.
"""

# macOS minimum versions for each CLI binary.
# Both binaries embed native code linked against macOS 13 frameworks; older
# macOS versions hit "Symbol not found: _ubrk_clone" or similar dyld errors.
MIN_MACOS_FOR_CLAUDE_CLI = (13, 0)  # Ventura
MIN_MACOS_FOR_CODEX_CLI = (13, 0)   # Conservative — same floor as Claude

# Linux glibc minimums.
# claude-code's optional native deps (and codex's Rust binaries shipped in
# the npm package) target glibc 2.31+, matching Ubuntu 20.04 / Debian 11 /
# RHEL 9. Below that, dynamic linking fails with "GLIBC_X.Y not found".
MIN_GLIBC_FOR_CLAUDE_CLI = (2, 31)
MIN_GLIBC_FOR_CODEX_CLI = (2, 31)


def _label_for(provider: str) -> str:
    return "Claude CLI" if provider == "claude" else "Codex CLI"


def cli_compat(provider: str, os_info) -> tuple[bool, str]:
    """Check whether a CLI provider's npm-distributed binary will load here.

    Args:
        provider: Provider key from the wizard. Only "claude" and "codex"
            are gated; everything else returns (True, "").
        os_info: An ``install.os_detect.OSInfo`` instance describing the host.

    Returns:
        ``(compatible, reason)`` — ``reason`` is empty when compatible and
        contains a human-readable explanation when not. The caller is
        expected to surface ``reason`` to the user via the install wizard.
    """
    if provider not in ("claude", "codex"):
        return True, ""

    label = _label_for(provider)

    if os_info.os_type == "macos":
        floor = (
            MIN_MACOS_FOR_CLAUDE_CLI
            if provider == "claude"
            else MIN_MACOS_FOR_CODEX_CLI
        )
        current = (os_info.version_major, os_info.version_minor)
        if current < floor:
            version_label = os_info.version or "unknown"
            name_suffix = f" ({os_info.version_name})" if os_info.version_name else ""
            return False, (
                f"{label} requires macOS {floor[0]}.{floor[1]}+ "
                f"but this Mac is on macOS {version_label}{name_suffix}. "
                f"The npm-distributed binary links against newer macOS "
                f"frameworks, so loading it crashes immediately with "
                f"'Symbol not found' / dyld errors. "
                f"Pick a different provider (claude-api, openrouter, "
                f"ollama, gemini, etc.)."
            )
        return True, ""

    if os_info.os_type == "linux":
        from install.os_detect import glibc_version

        gv = glibc_version()
        floor = (
            MIN_GLIBC_FOR_CLAUDE_CLI
            if provider == "claude"
            else MIN_GLIBC_FOR_CODEX_CLI
        )
        if gv is None:
            return False, (
                f"{label} requires a glibc-based Linux. This system "
                f"appears to use musl libc (Alpine and similar) or "
                f"ldd is unavailable. "
                f"Pick a different provider (claude-api, openrouter, "
                f"ollama, gemini, etc.)."
            )
        if gv < floor:
            return False, (
                f"{label} requires glibc {floor[0]}.{floor[1]}+ but "
                f"this system has glibc {gv[0]}.{gv[1]}. "
                f"Loading the binary fails with 'GLIBC_X.Y not found'. "
                f"Pick a different provider (claude-api, openrouter, "
                f"ollama, gemini, etc.)."
            )
        return True, ""

    return True, ""
