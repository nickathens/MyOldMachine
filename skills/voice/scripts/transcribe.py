#!/usr/bin/env python3
"""Voice message transcription using Whisper (multilingual, CPU).

Memory isolation (load-bearing, do not remove):
Whisper loads its weights in fp32 on CPU, so even 'medium' peaks ~4.8GB
resident and 'large' needs ~8-10GB. MyOldMachine runs the bot as a long-lived
service and sets no memory cap on it, so a runaway transcription would consume
system RAM until the kernel OOM-killer fires -- which on the low-spec machines
this project targets can freeze the box or kill the bot itself. To prevent that,
the whisper run is re-exec'd inside a memory-capped *systemd user scope*
(``systemd-run --user --scope``) whose cgroup lives outside the service: if it
exceeds ``WHISPER_MEM_MAX`` only the transcription is killed, never the bot.

This complements (does not replace) the skill_hooks RAM gate (``min_ram_gb`` in
deps.json), which blocks heavy skills *before* launch but cannot bound a process
that balloons after it starts. Where ``systemd-run`` is unavailable (macOS, or a
Linux box with no user systemd manager) there is no scope to fall back on, so
models heavier than 'medium' are refused rather than gambling the machine.

Usage: python transcribe.py <audio_file> [--language LANG] [--model NAME]
"""
import os
import shutil
import subprocess
import sys

DEFAULT_MODEL = "medium"

# Scope memory ceiling. Whisper loads in fp32 on CPU, so 'medium' actually peaks
# ~4.8GB resident (NOT the ~1.5GB fp16/on-disk figure). 6G gives it headroom
# while still killing anything heavier (large* on CPU needs ~8-10GB) inside its
# own scope. The scope's cgroup is what bounds a runaway; on a machine with less
# RAM than the cap, MemorySwapMax=0 still confines the OOM to the scope so the
# bot survives. Override via WHISPER_MEM_MAX (e.g. "10G") for a bigger model.
MEM_MAX = os.environ.get("WHISPER_MEM_MAX", "6G")

# Models safe to run unisolated (small enough not to threaten a machine that
# passed the skill_hooks RAM gate). Anything outside this set is refused when
# scope isolation is unavailable, rather than risking a system-wide OOM.
SAFE_MODELS = {
    "tiny", "base", "small", "medium",
    "tiny.en", "base.en", "small.en", "medium.en",
}

USAGE = "Usage: python transcribe.py <audio_file> [--language LANG] [--model NAME]"

# Warm listening engine (data/stt/stt_daemon.py): Whisper large-v3-turbo held
# resident on the Apple GPU behind a local socket, ~1s per clip vs 5-30s for
# the cold CPU path below. hear.py is stdlib-only, auto-starts the daemon, and
# works under any python. Used whenever the caller does not force a --model;
# any failure falls through to the legacy CPU path unchanged.
HEAR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "..", "data", "stt", "hear.py")


def _try_warm_engine(audio_path, language):
    if not os.path.isfile(HEAR):
        return None
    cmd = [sys.executable, HEAR, os.path.abspath(audio_path)]
    if language:
        cmd += ["--language", language]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def _parse_args(argv):
    """Return (audio_path, language, model) from argv."""
    audio_path = argv[1]
    language = None
    model = DEFAULT_MODEL
    if "--language" in argv:
        i = argv.index("--language")
        if i + 1 < len(argv):
            language = argv[i + 1]
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 < len(argv):
            model = argv[i + 1]
    return audio_path, language, model


def _scope_prefix():
    """Return the ``systemd-run`` argv prefix that runs a command in a
    memory-capped user scope, or None if ``systemd-run`` is not installed
    (e.g. macOS, non-systemd Linux).

    The prefix ends in ``--`` so the payload command is appended directly.
    ``MemorySwapMax=0`` makes the scope OOM at the RSS cap instead of thrashing
    swap; ``--collect`` reaps the transient unit even on failure.
    """
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        return None
    return [
        systemd_run, "--user", "--scope", "--quiet", "--collect",
        "-p", f"MemoryMax={MEM_MAX}", "-p", "MemorySwapMax=0", "--",
    ]


def _isolation_works(prefix):
    """Probe whether a capped user scope can actually be created here (needs a
    reachable user systemd manager). Cheap relative to whisper's runtime."""
    try:
        probe = subprocess.run(
            prefix + ["true"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run_whisper(audio_path, language, model):
    """Load the model and print the transcription. Imported lazily so that
    importing this module (e.g. from tests) does not pull in torch/whisper."""
    import whisper

    wmodel = whisper.load_model(model, device="cpu")
    opts = {"language": language} if language else {}
    result = wmodel.transcribe(audio_path, **opts)
    print(result["text"].strip())


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2 or argv[1].startswith("-"):
        print(USAGE, file=sys.stderr)
        sys.exit(1)

    audio_path, language, model = _parse_args(argv)

    # Fast path: the warm GPU listening engine, unless the caller explicitly
    # asked for a specific legacy model.
    if "--model" not in argv:
        text = _try_warm_engine(audio_path, language)
        if text is not None:
            print(text)
            return

    # Re-exec inside the memory-capped scope unless we are already in it.
    if os.environ.get("WHISPER_ISOLATED") != "1":
        prefix = _scope_prefix()
        if prefix and _isolation_works(prefix):
            env = dict(os.environ, WHISPER_ISOLATED="1")
            proc = subprocess.run(
                prefix + [sys.executable, os.path.abspath(__file__)] + argv[1:],
                env=env,
            )
            # The scope's exit code IS the transcription's result (stdout/stderr
            # pass straight through). A non-zero code here means whisper failed
            # or hit the cap -- either way the bot is untouched.
            sys.exit(proc.returncode)

        # No scope isolation available on this host.
        if model not in SAFE_MODELS:
            print(
                f"[transcribe] REFUSING to run model {model!r} without memory "
                "isolation: it could exhaust system RAM and OOM the machine. "
                "Install systemd-run with a user manager, or use a model "
                "<= medium.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(
            f"[transcribe] WARNING: scope isolation unavailable; running {model!r} "
            "in-process (safe size, but unprotected).",
            file=sys.stderr,
        )

    _run_whisper(audio_path, language, model)


if __name__ == "__main__":
    main()
