#!/usr/bin/env python3
"""
Ollama Auto-Install and Hardware Benchmark for MyOldMachine.

Handles:
1. Hardware detection (RAM, CPU cores, disk space, GPU)
2. Model recommendation based on specs
3. Ollama installation (Linux + macOS)
4. Model pull
5. Service verification

Usage:
    python install/ollama_setup.py                    # Interactive
    python install/ollama_setup.py --auto             # Auto-detect and install best model
    python install/ollama_setup.py --benchmark-only   # Just show specs and recommendation
    python install/ollama_setup.py --model qwen2.5:3b # Install specific model
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Colors
BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def info(msg):
    print(f"{BLUE}[INFO]{NC} {msg}")


def ok(msg):
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")


# --- Hardware Detection ---

def get_ram_gb() -> float:
    """Get total RAM in GB."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            return int(result.stdout.strip()) / (1024 ** 3)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
    except Exception:
        pass
    return 0.0


def get_cpu_cores() -> int:
    """Get number of CPU cores."""
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def get_cpu_name() -> str:
    """Get CPU model name."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()
        else:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
    except Exception:
        pass
    return "Unknown"


def get_disk_free_gb() -> float:
    """Get free disk space in GB (on the home partition)."""
    try:
        st = os.statvfs(str(Path.home()))
        return (st.f_bavail * st.f_frsize) / (1024 ** 3)
    except Exception:
        return 0.0


def get_gpu_info() -> dict:
    """Detect GPU. Returns dict with 'name', 'vram_gb', 'type' (nvidia/amd/apple/none)."""
    gpu_info = {"name": None, "vram_gb": 0, "type": "none"}

    # Check for NVIDIA GPU
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            gpu_info["name"] = parts[0].strip()
            gpu_info["vram_gb"] = round(int(parts[1].strip()) / 1024, 1) if len(parts) > 1 else 0
            gpu_info["type"] = "nvidia"
            return gpu_info
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check for Apple Silicon (unified memory — shares with RAM)
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            chip = result.stdout.strip()
            if "Apple" in chip:
                gpu_info["name"] = chip
                gpu_info["type"] = "apple"
                # Apple Silicon uses unified memory — GPU gets a share of total RAM
                gpu_info["vram_gb"] = round(get_ram_gb() * 0.75, 1)
                return gpu_info
        except Exception:
            pass

    # Check for AMD GPU (Linux)
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "VGA" in line or "3D" in line:
                if "AMD" in line or "ATI" in line:
                    gpu_info["name"] = line.split(": ", 1)[-1].strip()
                    gpu_info["type"] = "amd"
                    return gpu_info
                elif "NVIDIA" in line:
                    # nvidia-smi not found but GPU is present
                    gpu_info["name"] = line.split(": ", 1)[-1].strip()
                    gpu_info["type"] = "nvidia"
                    return gpu_info
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # macOS Intel — check for discrete GPU
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "Chipset Model" in line:
                    gpu_name = line.split(":")[1].strip()
                    gpu_info["name"] = gpu_name
                    if "Intel" in gpu_name:
                        gpu_info["type"] = "intel"
                    elif "AMD" in gpu_name or "ATI" in gpu_name:
                        gpu_info["type"] = "amd"
                    elif "NVIDIA" in gpu_name:
                        gpu_info["type"] = "nvidia"
                    return gpu_info
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return gpu_info


def run_benchmark() -> dict:
    """Run hardware benchmark and return specs."""
    specs = {
        "ram_gb": round(get_ram_gb(), 1),
        "cpu_cores": get_cpu_cores(),
        "cpu_name": get_cpu_name(),
        "disk_free_gb": round(get_disk_free_gb(), 1),
        "gpu": get_gpu_info(),
        "os": platform.system().lower(),
        "arch": platform.machine(),
    }
    return specs


# --- Model Recommendation ---

# Model catalog: name, size on disk (GB), RAM needed (GB), quality tier, tool-use reliability
MODEL_CATALOG = [
    # (model_tag, disk_gb, ram_gb, quality, tool_use_reliable, description)
    ("qwen2.5:0.5b",    0.4,  1.0,  1, False, "Qwen 2.5 0.5B — barely functional, extremely fast"),
    ("qwen2.5:1.5b",    1.0,  2.0,  2, False, "Qwen 2.5 1.5B — basic tasks, fast"),
    ("gemma2:2b",        1.6,  3.0,  3, False, "Gemma 2 2B — light, decent for chat"),
    ("qwen2.5:3b",       2.0,  3.5,  3, False, "Qwen 2.5 3B — good chat, weak tool-use"),
    ("llama3.2:3b",      2.0,  3.5,  3, False, "Llama 3.2 3B — good instruction following"),
    ("phi3:3.8b",        2.3,  4.0,  4, False, "Phi-3 3.8B — Microsoft, strong for size"),
    ("mistral:7b",       4.1,  5.5,  5, True,  "Mistral 7B — reliable all-rounder"),
    ("llama3.1:8b",      4.7,  6.0,  6, True,  "Llama 3.1 8B — strong tool-use, recommended minimum"),
    ("gemma2:9b",        5.4,  7.0,  6, True,  "Gemma 2 9B — Google, strong reasoning"),
    ("qwen2.5:14b",      9.0,  10.0, 7, True,  "Qwen 2.5 14B — excellent quality"),
    ("llama3.1:70b-q4",  40.0, 48.0, 9, True,  "Llama 3.1 70B Q4 — near-frontier quality"),
]


def recommend_model(specs: dict) -> tuple[str, str]:
    """Recommend the best model for the hardware.

    Returns (model_tag, explanation).
    """
    ram = specs["ram_gb"]
    disk_free = specs["disk_free_gb"]
    gpu = specs.get("gpu", {})
    gpu_type = gpu.get("type", "none")

    # Find the best model that fits in RAM and disk
    # Leave at least 2GB RAM for the OS + bot
    available_ram = ram - 2.0

    # For Apple Silicon, models run on GPU (unified memory) — more efficient
    if gpu_type == "apple":
        available_ram = ram - 1.5  # Metal is more efficient

    best = None
    for model_tag, disk_gb, ram_needed, quality, tool_reliable, desc in MODEL_CATALOG:
        if ram_needed <= available_ram and disk_gb <= disk_free - 2.0:
            best = (model_tag, disk_gb, ram_needed, quality, tool_reliable, desc)

    if not best:
        return (None, f"Not enough resources. Need at least 3GB RAM free (have {ram:.1f}GB total).")

    model_tag, disk_gb, ram_needed, quality, tool_reliable, desc = best

    explanation = f"Recommended: {desc}\n"
    explanation += f"  Disk: {disk_gb:.1f}GB (you have {disk_free:.0f}GB free)\n"
    explanation += f"  RAM: needs ~{ram_needed:.0f}GB (you have {ram:.0f}GB total)\n"

    if not tool_reliable:
        explanation += (
            f"\n  {YELLOW}Warning: This model is below the recommended minimum for tool-use.{NC}\n"
            f"  It may fail to format tool calls correctly. For reliable machine control,\n"
            f"  you need at least 8GB RAM for Llama 3.1 8B or Mistral 7B.\n"
            f"  Consider using OpenRouter (free) for better tool-use quality."
        )

    if gpu_type == "apple":
        explanation += f"\n  Apple Silicon detected — model will run on GPU (Metal), fast inference."
    elif gpu_type == "nvidia" and gpu.get("vram_gb", 0) >= ram_needed:
        explanation += f"\n  NVIDIA GPU detected ({gpu['name']}) — model will use GPU acceleration."
    elif gpu_type == "nvidia":
        explanation += f"\n  NVIDIA GPU detected but {gpu.get('vram_gb', 0):.0f}GB VRAM may not fit model. Will use CPU+GPU split."

    return (model_tag, explanation)


# --- Ollama Installation ---

def is_ollama_installed() -> bool:
    """Check if Ollama is already installed."""
    return shutil.which("ollama") is not None


def install_ollama() -> bool:
    """Install Ollama on Linux or macOS. Returns True on success."""
    if is_ollama_installed():
        ok("Ollama is already installed")
        return True

    system = platform.system()

    if system == "Linux":
        info("Installing Ollama for Linux...")
        try:
            # Ollama's official install script
            result = subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                timeout=300,
            )
            if result.returncode != 0:
                error("Ollama install script failed")
                return False
            ok("Ollama installed")
            return True
        except subprocess.TimeoutExpired:
            error("Ollama installation timed out")
            return False
        except Exception as e:
            error(f"Ollama installation failed: {e}")
            return False

    elif system == "Darwin":
        # macOS — Homebrew is the primary method
        if shutil.which("brew"):
            info("Installing Ollama via Homebrew...")
            try:
                result = subprocess.run(
                    ["brew", "install", "ollama"],
                    timeout=600,
                )
                if result.returncode == 0:
                    ok("Ollama installed via Homebrew")
                    return True
                warn("Homebrew install failed")
            except (subprocess.TimeoutExpired, Exception) as e:
                warn(f"Homebrew install failed: {e}")

        # Fallback: official curl installer (works on some macOS versions)
        info("Trying official Ollama installer...")
        try:
            result = subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                timeout=300,
            )
            if result.returncode == 0:
                ok("Ollama installed")
                return True
        except Exception:
            pass

        # Final fallback: direct download instructions
        error("Automatic installation failed.")
        print(f"  Install Ollama manually:")
        print(f"    Option 1: brew install ollama")
        print(f"    Option 2: Download from https://ollama.com/download/mac")
        print(f"  Then re-run the installer.")
        return False

    else:
        error(f"Unsupported OS: {system}")
        return False


def ensure_ollama_running() -> bool:
    """Make sure Ollama service is running. Returns True if running."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try starting it
    system = platform.system()
    info("Starting Ollama service...")

    if system == "Linux":
        # Try systemd first
        try:
            subprocess.run(
                ["sudo", "systemctl", "start", "ollama"],
                capture_output=True, timeout=15
            )
            time.sleep(2)
        except Exception:
            pass

        # If systemd didn't work, start manually in background
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        # Start serve in background
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(3)
        except Exception:
            pass

    elif system == "Darwin":
        # On macOS, Homebrew installs as a launchd service
        try:
            subprocess.run(
                ["brew", "services", "start", "ollama"],
                capture_output=True, timeout=15
            )
            time.sleep(3)
        except Exception:
            pass

        # Fallback: start manually
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(3)
        except Exception:
            pass

    # Verify it's running
    for _ in range(5):
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                ok("Ollama is running")
                return True
        except Exception:
            pass
        time.sleep(2)

    error("Could not start Ollama service")
    return False


def pull_model(model_tag: str) -> bool:
    """Pull an Ollama model. Returns True on success."""
    info(f"Pulling model: {model_tag} (this may take a while)...")

    try:
        result = subprocess.run(
            ["ollama", "pull", model_tag],
            timeout=1800,  # 30 min timeout for large models
        )
        if result.returncode == 0:
            ok(f"Model {model_tag} ready")
            return True
        error(f"Failed to pull model: {model_tag}")
        return False
    except subprocess.TimeoutExpired:
        error(f"Model pull timed out (>30 min). Try manually: ollama pull {model_tag}")
        return False
    except Exception as e:
        error(f"Model pull failed: {e}")
        return False


def verify_model(model_tag: str) -> bool:
    """Verify a model responds correctly."""
    info("Verifying model responds...")
    try:
        result = subprocess.run(
            ["ollama", "run", model_tag, "Say hello in exactly 3 words."],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            ok(f"Model verified: {result.stdout.strip()[:80]}")
            return True
        warn("Model responded but with unexpected output")
        return True  # Still usable
    except subprocess.TimeoutExpired:
        warn("Model verification timed out (slow hardware). Model is installed though.")
        return True
    except Exception as e:
        error(f"Model verification failed: {e}")
        return False


# --- Main ---

def print_specs(specs: dict):
    """Print hardware specs in a nice format."""
    print(f"\n{BOLD}Hardware Benchmark{NC}")
    print(f"  CPU:  {specs['cpu_name']} ({specs['cpu_cores']} cores)")
    print(f"  RAM:  {specs['ram_gb']:.1f} GB")
    print(f"  Disk: {specs['disk_free_gb']:.0f} GB free")
    gpu = specs.get("gpu", {})
    if gpu.get("name"):
        vram = f" ({gpu['vram_gb']}GB VRAM)" if gpu.get("vram_gb") else ""
        print(f"  GPU:  {gpu['name']}{vram} [{gpu['type']}]")
    else:
        print(f"  GPU:  None detected (CPU inference only)")
    print(f"  OS:   {specs['os']} {specs['arch']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Ollama auto-install with hardware benchmark")
    parser.add_argument("--auto", action="store_true", help="Auto-detect and install best model")
    parser.add_argument("--benchmark-only", action="store_true", help="Show specs and recommendation only")
    parser.add_argument("--model", type=str, help="Install a specific model")
    parser.add_argument("--json", action="store_true", help="Output recommendation as JSON (for wizard)")
    args = parser.parse_args()

    # Always run benchmark
    specs = run_benchmark()

    if args.json:
        # Machine-readable output for wizard integration
        model_tag, explanation = recommend_model(specs)
        # Strip ANSI color codes for JSON output
        import re as _re
        clean_explanation = _re.sub(r'\033\[[0-9;]*m', '', explanation)
        output = {
            "specs": specs,
            "recommended_model": model_tag,
            "explanation": clean_explanation,
        }
        print(json.dumps(output))
        return

    print_specs(specs)

    # Get recommendation
    model_tag, explanation = recommend_model(specs)
    print(f"{BOLD}Model Recommendation{NC}")
    print(f"  {explanation}")
    print()

    if args.benchmark_only:
        return

    if model_tag is None:
        error("Hardware doesn't meet minimum requirements for local models.")
        print(f"  Consider using OpenRouter (free models available) instead.")
        sys.exit(1)

    # Determine which model to install
    target_model = args.model or model_tag

    if not args.auto and not args.model:
        # Interactive mode
        print(f"  Enter a model name or press Enter to accept recommendation.")
        print(f"  See all models at: https://ollama.com/library")
        print()
        try:
            choice = input(f"  Model [{target_model}]: ").strip()
        except EOFError:
            choice = ""
        if choice:
            target_model = choice

    # Install Ollama if needed
    if not is_ollama_installed():
        if not install_ollama():
            error("Failed to install Ollama")
            sys.exit(1)

    # Ensure it's running
    if not ensure_ollama_running():
        error("Could not start Ollama. Try manually: ollama serve")
        sys.exit(1)

    # Pull the model
    if not pull_model(target_model):
        sys.exit(1)

    # Verify
    verify_model(target_model)

    print()
    ok(f"Ollama setup complete. Model: {target_model}")
    print(f"  The bot will use this model for AI responses with tool-use capability.")

    # Output the final model for the wizard to capture
    print(f"\nOLLAMA_MODEL={target_model}")


if __name__ == "__main__":
    main()
