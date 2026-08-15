#!/usr/bin/env python3
"""
MemPalace setup: install the shared mempalace venv (once) from an upstream
source checkout, then provision a per-user palace and mine the user's
existing message log into it.

Run with the bot's Python:
    <BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py \\
        --user-dir <user_dir>

Provisioning is split:
  - The upstream repo is cloned to <BOT_DIR>/data/mempalace/src/ and pinned
    to PINNED_REF. The venv at <BOT_DIR>/data/mempalace/venv/ installs it
    editable, so the running library IS that checkout. One install per
    machine. Moving or deleting src/ breaks the install.
  - The actual palace, conversation exports, and sync state live entirely
    inside <user_dir>/mempalace/, one tree per Telegram user.

Pass --shared-only to install just the venv (e.g. during one-time bootstrap).
Pass --upgrade --ref vX.Y.Z to move the checkout to a newer release.
Pass --from-pypi for the old behaviour (wheel install, no checkout).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
SHARED_MEMPALACE_DIR = BOT_DIR / "data" / "mempalace"
SHARED_VENV_DIR = SHARED_MEMPALACE_DIR / "venv"
SHARED_SRC_DIR = SHARED_MEMPALACE_DIR / "src"
SHARED_VENV_PYTHON = SHARED_VENV_DIR / "bin" / "python"
SHARED_VENV_PIP = SHARED_VENV_DIR / "bin" / "pip"

UPSTREAM_REPO = "https://github.com/MemPalace/mempalace.git"
# Pinned so an install is reproducible and an upgrade is a reviewed decision,
# not whatever main happened to be that morning. Bump with --ref, then move
# this constant once the upgrade is verified on a real palace.
PINNED_REF = "v3.7.1"

# Extras declared by mempalace v3.7.1. Every one of these must be either
# installed by extras_for() or refused by skipped_extras() with a reason --
# tests/test_mempalace_source_install.py enforces that partition, so a new
# upstream extra cannot be silently ignored on the next bump.
UPSTREAM_EXTRAS = (
    "dev", "spellcheck", "milvus", "pgvector", "gpu", "dml", "coreml",
    "multilingual", "extract",
)

# Installed everywhere, and why.
COMMON_EXTRAS = {
    "extract": "binary-format mining: pdf, docx, pptx, xlsx, rtf",
    "spellcheck": "typo tolerance on search queries",
    "dev": "upstream's own test suite, so an upgrade can be gated before it goes live",
}

# Hardware acceleration for the ONNX embedding model. Upstream pyproject:
# "Install exactly one". mempalace's own default device is "auto", which
# picks the first provider actually compiled into the installed onnxruntime,
# so the right extra is all that is needed -- no env var to set.
ACCELERATORS = {
    "coreml": "Apple Neural Engine (macOS)",
    "gpu": "NVIDIA CUDA",
    "dml": "DirectML (Windows AMD/Intel/NVIDIA)",
}
_ACCELERATOR_BY_PLATFORM = {"darwin": "coreml", "win32": "dml"}

# Reasons for the extras we do not install, independent of platform.
_ALWAYS_SKIPPED = {
    "milvus": "alternative vector backend; this bot uses the default Chroma one",
    "pgvector": "alternative vector backend; needs a running PostgreSQL server "
                "with the vector extension",
    "multilingual": "no-op alias upstream (declares no packages); its dependencies "
                    "ship in core",
}


def accelerator_for(platform: str) -> str | None:
    """The one accelerator extra that can work on this platform, if any.

    Linux gets none by default: onnxruntime CPU already arrives with
    chromadb, and CUDA is an opt-in choice (--accel gpu), not an assumption
    about someone's hardware.
    """
    return _ACCELERATOR_BY_PLATFORM.get(platform)


def extras_for(platform: str, accel: str = "auto") -> list[str]:
    """Extras to install on this machine. At most one accelerator, ever."""
    extras = sorted(COMMON_EXTRAS)
    chosen = accelerator_for(platform) if accel == "auto" else (
        None if accel == "none" else accel
    )
    if chosen:
        extras.append(chosen)
    return extras


def skipped_extras(platform: str, accel: str = "auto") -> dict[str, str]:
    """Every upstream extra we leave out, mapped to the reason why."""
    installed = set(extras_for(platform, accel))
    skipped = {k: v for k, v in _ALWAYS_SKIPPED.items() if k not in installed}
    for name, hardware in ACCELERATORS.items():
        if name in installed:
            continue
        skipped[name] = (
            f"accelerator for {hardware}; upstream says install exactly one, "
            f"and this is not the one for platform '{platform}'"
        )
    return skipped


def stable_base_interpreter(version: tuple[int, int] | None = None,
                            executable: str | None = None,
                            exists=None) -> str:
    """The interpreter to build the venv from, preferring a stable path.

    A venv records the interpreter it was built from. Homebrew's nightly
    python bump deletes the old versioned Cellar directory, so any venv built
    from .../Cellar/python@3.12/3.12.13/... dies with exit 127 the moment
    3.12.14 lands -- which is exactly how the nightly mempalace sync jobs
    died on 2026-08-15. The /opt/homebrew/opt/python@3.12 symlink survives
    the bump, so build from that when it is present.
    """
    version = version or sys.version_info[:2]
    executable = executable or sys.executable
    exists = exists or os.path.exists
    major, minor = version
    for prefix in ("/opt/homebrew/opt", "/usr/local/opt"):
        candidate = f"{prefix}/python@{major}.{minor}/bin/python{major}.{minor}"
        if exists(candidate):
            return candidate
    return executable


def _git(args: list[str], cwd: Path | None = None, timeout: int = 600):
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=timeout,
    )


def checkout_is_dirty(src_dir: Path) -> bool:
    """True if someone has local edits in the upstream checkout."""
    result = _git(["status", "--porcelain"], cwd=src_dir, timeout=60)
    return result.returncode == 0 and bool(result.stdout.strip())


def ensure_source_checkout(ref: str = PINNED_REF) -> bool:
    """Clone or update the upstream repo at data/mempalace/src, pinned to ref."""
    SHARED_MEMPALACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)

    if (SHARED_SRC_DIR / ".git").is_dir():
        if checkout_is_dirty(SHARED_SRC_DIR):
            print(f"ERROR: {SHARED_SRC_DIR} has local edits. Commit, stash or "
                  "remove them before changing the pinned ref.", flush=True)
            return False
        print(f"Fetching upstream into {SHARED_SRC_DIR}...", flush=True)
        result = _git(["fetch", "--tags", "--prune", "origin"], cwd=SHARED_SRC_DIR)
        if result.returncode != 0:
            print(f"ERROR: git fetch failed:\n{result.stderr[-500:]}", flush=True)
            return False
    else:
        if SHARED_SRC_DIR.exists() and any(SHARED_SRC_DIR.iterdir()):
            print(f"ERROR: {SHARED_SRC_DIR} exists but is not a git checkout. "
                  "Move it aside and re-run.", flush=True)
            return False
        print(f"Cloning {UPSTREAM_REPO} into {SHARED_SRC_DIR}...", flush=True)
        result = _git(["clone", UPSTREAM_REPO, str(SHARED_SRC_DIR)], timeout=1800)
        if result.returncode != 0:
            print(f"ERROR: git clone failed:\n{result.stderr[-500:]}", flush=True)
            return False

    result = _git(["checkout", "--quiet", ref], cwd=SHARED_SRC_DIR)
    if result.returncode != 0:
        print(f"ERROR: cannot check out '{ref}':\n{result.stderr[-500:]}", flush=True)
        return False

    described = _git(["describe", "--tags", "--always"], cwd=SHARED_SRC_DIR, timeout=60)
    print(f"Source checkout at {described.stdout.strip() or ref}", flush=True)
    return True


def installed_version() -> str | None:
    """mempalace version importable from the shared venv, or None."""
    if not SHARED_VENV_PYTHON.exists():
        return None
    try:
        result = subprocess.run(
            [str(SHARED_VENV_PYTHON), "-c",
             "import mempalace; print(mempalace.__version__, mempalace.__file__)"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    version, _, location = result.stdout.strip().partition(" ")
    # Resolve both sides: /tmp vs /private/tmp and symlinked data dirs would
    # otherwise make an editable install look like a wheel.
    editable = SHARED_SRC_DIR.resolve() in Path(location).resolve().parents
    print(f"MemPalace {version} installed at {SHARED_VENV_DIR} "
          f"({'editable from src/' if editable else 'wheel'})", flush=True)
    return version


def create_venv() -> bool:
    """Create the shared venv from a version-stable interpreter."""
    SHARED_MEMPALACE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    base = stable_base_interpreter()
    print(f"Creating shared venv at {SHARED_VENV_DIR} (from {base})...", flush=True)
    try:
        result = subprocess.run(
            [base, "-m", "venv", str(SHARED_VENV_DIR)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: venv creation timed out.", flush=True)
        return False
    if result.returncode != 0:
        print(f"ERROR: Failed to create venv: {result.stderr}", flush=True)
        if "ensurepip" in result.stderr.lower():
            print("TIP: Install python3-venv (e.g., sudo apt install python3-venv)", flush=True)
        return False

    print("Upgrading pip...", flush=True)
    subprocess.run(
        [str(SHARED_VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True, text=True, timeout=300,
    )
    return True


def pip_install(pip_args: list[str], label: str) -> bool:
    print(f"Installing {label}...", flush=True)
    try:
        result = subprocess.run(
            [str(SHARED_VENV_PIP), "install", *pip_args],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: pip install timed out (>30 min). Check network.", flush=True)
        return False
    if result.returncode != 0:
        print(f"ERROR: pip install failed:\n{result.stderr[-800:]}", flush=True)
        return False
    return True


def install_from_source(accel: str = "auto") -> bool:
    """Editable-install the pinned checkout with the extras this machine can use."""
    extras = extras_for(sys.platform, accel)
    for name, reason in sorted(skipped_extras(sys.platform, accel).items()):
        print(f"  skipping [{name}]: {reason}", flush=True)
    spec = f"{SHARED_SRC_DIR}[{','.join(extras)}]" if extras else str(SHARED_SRC_DIR)
    return pip_install(["-e", spec],
                       f"mempalace (editable) with extras: {', '.join(extras) or 'none'}")


def provision_user(user_dir: Path) -> bool:
    """Create the per-user palace dir tree. Returns True on success."""
    if not user_dir.exists():
        print(f"ERROR: --user-dir does not exist: {user_dir}", flush=True)
        return False

    mempalace_dir = user_dir / "mempalace"
    palace_dir = mempalace_dir / "palace"
    convos_dir = mempalace_dir / "convos"

    mempalace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    palace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    convos_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"Provisioned palace dirs under {mempalace_dir}", flush=True)
    return True


def initial_mine(user_dir: Path) -> bool:
    """Mine the user's existing message log into their freshly provisioned palace."""
    msg_log = user_dir / "message_log.db"
    if not msg_log.exists():
        print(f"  No message_log.db in {user_dir}. Palace stays empty.", flush=True)
        return True

    sync_script = BOT_DIR / "skills" / "mempalace" / "scripts" / "mempalace_sync.py"
    print("\nMining existing history into palace...", flush=True)
    try:
        result = subprocess.run(
            [str(SHARED_VENV_PYTHON), str(sync_script),
             "--user-dir", str(user_dir), "--force-today"],
            text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        print("\nWARNING: Mining timed out after 15 minutes.", flush=True)
        print("Run sync manually to complete:", flush=True)
        print(f"  {SHARED_VENV_PYTHON} {sync_script} --user-dir {user_dir} --force-today",
              flush=True)
        return False
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="MemPalace setup (shared venv + per-user palace)")
    parser.add_argument(
        "--user-dir",
        help="The user's data directory. Required unless --shared-only is set.",
    )
    parser.add_argument(
        "--shared-only", action="store_true",
        help="Install just the shared mempalace venv. Skip per-user provisioning.",
    )
    parser.add_argument(
        "--reinstall", action="store_true",
        help="Rebuild the shared venv from scratch even if already present.",
    )
    parser.add_argument(
        "--upgrade", action="store_true",
        help="Move the checkout to --ref and reinstall it, keeping the venv.",
    )
    parser.add_argument(
        "--ref", default=PINNED_REF,
        help=f"Upstream tag, branch or commit to pin the checkout to (default: {PINNED_REF}).",
    )
    parser.add_argument(
        "--accel", default="auto", choices=["auto", *ACCELERATORS, "none"],
        help="Which embedding accelerator extra to install (default: auto, by platform).",
    )
    parser.add_argument(
        "--from-pypi", action="store_true",
        help="Install the published wheel instead of the source checkout.",
    )
    args = parser.parse_args()

    if not args.shared_only and not args.user_dir:
        parser.error("--user-dir is required (or pass --shared-only to install just the venv).")

    print("=== MemPalace Setup ===\n", flush=True)

    needs_install = args.reinstall or args.upgrade or installed_version() is None
    if needs_install:
        if args.reinstall and SHARED_VENV_DIR.exists():
            print(f"Removing existing venv at {SHARED_VENV_DIR}...", flush=True)
            shutil.rmtree(SHARED_VENV_DIR)
        if not SHARED_VENV_PYTHON.exists() and not create_venv():
            print("\nSetup failed: shared venv could not be created.", flush=True)
            sys.exit(1)

        if args.from_pypi:
            ok = pip_install(["mempalace"], "mempalace (from PyPI)")
        else:
            ok = ensure_source_checkout(args.ref) and install_from_source(args.accel)
        if not ok or installed_version() is None:
            print("\nSetup failed: mempalace could not be installed.", flush=True)
            sys.exit(1)
    else:
        print("\nShared venv ready. Skipping install.", flush=True)

    if args.shared_only:
        print(f"\nDone. Shared venv Python: {SHARED_VENV_PYTHON}", flush=True)
        return

    user_dir = Path(args.user_dir).expanduser().resolve()
    if not provision_user(user_dir):
        sys.exit(1)

    if not initial_mine(user_dir):
        print("\nSetup completed with warnings. Check output above.", flush=True)
    else:
        print("\nSetup complete. MemPalace is ready for this user.", flush=True)

    palace = user_dir / "mempalace" / "palace"
    print(f"\nPalace:      {palace}", flush=True)
    print(f"Venv Python: {SHARED_VENV_PYTHON}", flush=True)
    search = BOT_DIR / "skills" / "mempalace" / "scripts" / "mempalace_search.py"
    print(f'\nTest search: {SHARED_VENV_PYTHON} {search} "test query" --user-dir {user_dir}',
          flush=True)


if __name__ == "__main__":
    main()
