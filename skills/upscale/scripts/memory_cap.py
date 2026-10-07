"""Run an upscale in its own memory-capped user scope.

On Linux an upscale is a child of the bot's service, and when the kernel
OOM-kills a process in a unit, systemd's default OOMPolicy=stop takes the whole
unit down with it. Measured 2026-10-07 on the Linux bot's machine (CPU): the 4x
model peaked at 4.48 GB on a 600x600 picture with the default 512 px tile (1.73
GB at 2x), and the full-size arrays after it need about 0.1 GB per output
megapixel, 3.3 GB for a 1080p frame at 4x. So both scripts re-run themselves in
a capped scope, the voice skill's pattern, and on Linux where no scope can be
made only a job estimated to fit beside the bot runs. macOS has no scope and no
such policy, and its GPU path is the fast one; it runs as before.
"""
import os
import shutil
import subprocess
import sys

MEM_MAX = os.environ.get("UPSCALE_MEM_MAX", "6G")
FLAG = "UPSCALE_ISOLATED"
UNPROTECTED_MAX_GB = 3.0

# Fitted to three hybrid runs on a 600x600 picture: 4x peaked at 4.48 GB with
# a 512 px tile (544 with its padding) and at 2.12 GB with 256 px, 2x at 1.73
# GB with 512. The process holds about 1.2 GB whatever the tile (torch, the
# weights, the arrays); the rest grows with the padded tile's area.
BASE_GB = 1.2
TILE_GB = {2: 0.53, 4: 3.28}    # above the base, for one 544x544 padded tile


def scope_prefix():
    """systemd-run argv for a memory-capped user scope, or None when none can be made here."""
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        return None
    prefix = [systemd_run, "--user", "--scope", "--quiet", "--collect",
              "-p", f"MemoryMax={MEM_MAX}", "-p", "MemorySwapMax=0", "--"]
    try:
        probe = subprocess.run(prefix + ["true"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return prefix if probe.returncode == 0 else None


def rerun(script, argv):
    """The exit code of `script argv` run again inside the capped scope; None
    when already inside one, or when none can be made here."""
    if os.environ.get(FLAG) == "1":
        return None
    prefix = scope_prefix()
    if prefix is None:
        return None
    env = dict(os.environ, **{FLAG: "1"})
    proc = subprocess.run(prefix + [sys.executable, os.path.abspath(script), *argv], env=env)
    if proc.returncode < 0:
        print(f"Error: the upscale was killed by signal {-proc.returncode}, most likely by the "
              f"{MEM_MAX} memory cap: use --scale 2, --tile 256, or a smaller picture", file=sys.stderr)
        return 1
    return proc.returncode


def estimate_gb(width, height, scale, tile, neural=True):
    """Rough peak memory of an upscale: the larger of the model phase (tile
    work plus the output being filled) and the full-size arrays after it."""
    out_mp = width * height * scale * scale / 1e6
    if not neural:
        return 0.35 + 0.045 * out_mp    # 1080p at 4x, 8 or 16 bit: 1.73 GB measured
    side = tile if tile > 0 else max(width, height)
    area = (min(side, width) + 32) * (min(side, height) + 32)
    model = BASE_GB + TILE_GB[scale] * area / 544 ** 2 + 0.012 * out_mp
    return max(model, 0.5 + 0.12 * out_mp)


def unprotected_refusal(width, height, scale, tile, neural=True):
    """Why this job must not run without the cap, or None when it may."""
    if os.environ.get(FLAG) == "1" or not sys.platform.startswith("linux"):
        return None
    need = estimate_gb(width, height, scale, tile, neural)
    if need <= UNPROTECTED_MAX_GB:
        return None
    job = f"{width}x{height} at {scale}x" + (f" with --tile {tile}" if neural else " (lanczos)")
    advice = "--tile 256, --scale 2, or a smaller picture" if neural else "--scale 2 or a smaller picture"
    return (f"no memory-capped scope can be made here, and {job} needs about {need:.1f} GB, too much "
            f"to run beside the bot (limit {UNPROTECTED_MAX_GB:.0f} GB without the cap). Use {advice}.")
