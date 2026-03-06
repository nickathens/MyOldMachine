#!/usr/bin/env python3
"""
MyOldMachine Setup Wizard — Interactive configuration.

Walks the user through setup: name, Telegram token, LLM provider,
takeover level, sudo password, timezone. Writes .env and user profile.
Then hands off to the provisioner for system-level changes.
"""

import argparse
import getpass
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add repo root to path
REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))


# --- Terminal UI helpers ---

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
    sys.exit(1)


def ask(prompt, default=None, required=True, secret=False):
    """Ask a question with optional default."""
    suffix = f" [{default}]" if default else ""
    while True:
        if secret:
            value = getpass.getpass(f"  {prompt}{suffix}: ")
        else:
            value = input(f"  {prompt}{suffix}: ").strip()
        if not value and default:
            return default
        if not value and required:
            print(f"  {RED}This field is required.{NC}")
            continue
        return value


def ask_choice(prompt, options, default=None):
    """Ask user to pick from numbered options."""
    print(f"  {prompt}")
    for i, (key, desc) in enumerate(options, 1):
        marker = " (default)" if key == default else ""
        print(f"    {i}. {key} — {desc}{marker}")
    while True:
        raw = input(f"  Choice [{default or ''}]: ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        # Try matching by name
        for key, _ in options:
            if raw.lower() == key.lower():
                return key
        print(f"  {RED}Invalid choice. Pick 1-{len(options)} or type the name.{NC}")


def detect_timezone():
    """Attempt to detect local timezone."""
    try:
        import tzlocal
        return str(tzlocal.get_localzone())
    except ImportError:
        pass
    # Fallback: read /etc/timezone or systemd timedatectl
    try:
        tz = Path("/etc/timezone").read_text().strip()
        if tz:
            return tz
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # macOS — read /etc/localtime symlink (works without sudo)
    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            target = str(localtime.resolve())
            # /var/db/timezone/zoneinfo/America/New_York or /usr/share/zoneinfo/...
            for marker in ["/zoneinfo/", "/zoneinfo/"]:
                if marker in target:
                    tz = target.split(marker, 1)[1]
                    if "/" in tz:  # e.g. "America/New_York"
                        return tz
    except Exception:
        pass
    # macOS fallback — systemsetup (may need sudo on newer macOS)
    try:
        result = subprocess.run(
            ["systemsetup", "-gettimezone"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Output: "Time Zone: America/New_York"
            match = re.search(r":\s*(.+)", result.stdout)
            if match:
                return match.group(1).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "UTC"


def detect_machine_specs():
    """Detect basic machine specs."""
    import platform
    specs = {
        "os": platform.system().lower(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "hostname": platform.node(),
    }

    # CPU
    try:
        if specs["os"] == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            specs["cpu"] = result.stdout.strip()
        else:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        specs["cpu"] = line.split(":")[1].strip()
                        break
    except Exception:
        specs["cpu"] = "Unknown"

    # RAM
    try:
        if specs["os"] == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            specs["ram_gb"] = round(int(result.stdout.strip()) / (1024**3))
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        specs["ram_gb"] = round(kb / (1024**2))
                        break
    except Exception:
        specs["ram_gb"] = 0

    # Disk
    try:
        st = os.statvfs("/")
        specs["disk_gb"] = round((st.f_blocks * st.f_frsize) / (1024**3))
        specs["disk_free_gb"] = round((st.f_bavail * st.f_frsize) / (1024**3))
    except Exception:
        specs["disk_gb"] = 0
        specs["disk_free_gb"] = 0

    # GPU (best effort)
    specs["gpu"] = None
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "VGA" in line or "3D" in line:
                specs["gpu"] = line.split(": ", 1)[-1].strip()
                break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if not specs["gpu"]:
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "Chipset Model" in line:
                    specs["gpu"] = line.split(":")[1].strip()
                    break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return specs


LLM_PROVIDERS = [
    ("claude", "Anthropic Claude — best reasoning, requires API key ($)"),
    ("openai", "OpenAI GPT — widely used, requires API key ($)"),
    ("gemini", "Google Gemini — fast, requires API key (free tier available)"),
    ("ollama", "Ollama — free, runs locally on your machine (no API key)"),
    ("openrouter", "OpenRouter — many models, one API key ($)"),
]

DEFAULT_MODELS = {
    "claude": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "ollama": "llama3.1:8b",
    "openrouter": "anthropic/claude-sonnet-4-20250514",
}


def write_env(repo_dir: Path, config: dict):
    """Write configuration to .env file."""
    lines = [
        f"TELEGRAM_BOT_TOKEN={config['telegram_token']}",
        f"LLM_PROVIDER={config['llm_provider']}",
        f"LLM_MODEL={config['llm_model']}",
        f"LLM_API_KEY={config.get('llm_api_key', '')}",
        f"ALLOWED_USERS={config['telegram_user_id']}",
        f"BOT_NAME={config['bot_name']}",
        f"TIMEZONE={config['timezone']}",
        f"WEBHOOK_PORT=0",
    ]
    if config["llm_provider"] == "ollama":
        lines.append(f"OLLAMA_BASE_URL={config.get('ollama_url', 'http://localhost:11434')}")

    env_file = repo_dir / ".env"
    env_file.write_text("\n".join(lines) + "\n")
    # Restrict permissions — .env contains API keys
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    ok(f"Configuration saved to {env_file}")


def write_user_profile(repo_dir: Path, config: dict, machine_specs: dict):
    """Write initial user profile and memories."""
    data_dir = repo_dir / "data"
    users_dir = data_dir / "users" / str(config["telegram_user_id"])
    users_dir.mkdir(parents=True, exist_ok=True)

    # users.json
    profiles = {
        str(config["telegram_user_id"]): {
            "name": config["user_name"],
            "display_name": config["user_name"],
            "role": "admin",
            "can_install": True,
            "can_restart": True,
            "blocked_skills": [],
        }
    }
    profiles_file = data_dir / "users.json"
    profiles_file.write_text(json.dumps(profiles, indent=2) + "\n")

    # Initial memories
    memories = [
        {
            "content": f"User's name is {config['user_name']}",
            "timestamp": datetime.now().isoformat(),
        },
        {
            "content": f"Machine: {machine_specs.get('hostname', 'unknown')} / "
                       f"{machine_specs.get('cpu', 'unknown')} / "
                       f"{machine_specs.get('ram_gb', '?')}GB RAM / "
                       f"{machine_specs.get('disk_gb', '?')}GB disk",
            "timestamp": datetime.now().isoformat(),
        },
    ]
    memories_file = users_dir / "memories.json"
    memories_file.write_text(json.dumps(memories, indent=2) + "\n")

    # Create memory directory structure
    memory_dir = data_dir / "memory"
    for subdir in ["projects", "topics", "decisions"]:
        (memory_dir / subdir).mkdir(parents=True, exist_ok=True)

    ok(f"User profile created for {config['user_name']}")


def store_sudo_password(password: str):
    """Store sudo password securely."""
    sudo_file = Path.home() / ".sudo_pass"
    sudo_file.write_text(password + "\n")
    sudo_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    ok("Sudo password stored")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=str, default=str(REPO_DIR))
    parser.add_argument("--os", type=str, choices=["linux", "macos"], default="linux")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)
    detected_os = args.os

    # Check if already configured
    if (repo_dir / ".env").exists():
        warn(".env already exists.")
        reconfigure = input("  Reconfigure? (y/N): ").strip().lower()
        if reconfigure != "y":
            info("Skipping wizard. Run the provisioner manually if needed.")
            sys.exit(0)

    config = {}

    # --- OS Detection (before anything else) ---
    from install.os_detect import detect as detect_os_info, print_detection_summary
    os_info = detect_os_info()
    print(f"\n{BOLD}Detected System{NC}")
    print_detection_summary(os_info)

    if os_info.blockers:
        print()
        for b in os_info.blockers:
            error(b)
        # error() calls sys.exit(1)

    if os_info.warnings:
        print()
        proceed = input("  Continue despite warnings? (Y/n): ").strip().lower()
        if proceed == "n":
            info("Aborted. Address the warnings above and try again.")
            sys.exit(0)

    print()

    # --- Step 1: User identity ---
    print(f"\n{BOLD}Step 1: About You{NC}")
    config["user_name"] = ask("What's your name?")

    # --- Step 2: Telegram ---
    print(f"\n{BOLD}Step 2: Telegram Bot{NC}")
    print("  You need a Telegram bot token. Here's how to get one:")
    print("    1. Open Telegram and search for @BotFather")
    print("    2. Send /newbot and follow the prompts")
    print("    3. Copy the token it gives you")
    print()
    config["telegram_token"] = ask("Paste your bot token")

    print()
    print("  Now you need your Telegram user ID:")
    print("    1. Search for @userinfobot on Telegram")
    print("    2. Send /start — it will reply with your ID")
    print()
    raw_id = ask("Your Telegram user ID")
    if not raw_id.isdigit():
        error("Telegram user ID must be a number.")
    config["telegram_user_id"] = raw_id

    # --- Step 3: LLM Provider ---
    print(f"\n{BOLD}Step 3: AI Provider{NC}")
    print("  Choose which AI model will power your assistant.")
    print()
    config["llm_provider"] = ask_choice(
        "Pick your provider:",
        LLM_PROVIDERS,
        default="claude",
    )

    default_model = DEFAULT_MODELS.get(config["llm_provider"], "")
    config["llm_model"] = ask(f"Model", default=default_model)

    if config["llm_provider"] != "ollama":
        config["llm_api_key"] = ask(f"API key for {config['llm_provider']}", secret=True)
    else:
        config["llm_api_key"] = ""
        config["ollama_url"] = ask("Ollama URL", default="http://localhost:11434", required=False)
        print(f"  {YELLOW}Make sure Ollama is running: ollama serve{NC}")

    # --- Step 4: Bot name ---
    print(f"\n{BOLD}Step 4: Personalization{NC}")
    config["bot_name"] = ask("What should your bot call itself?", default="MyOldMachine")

    # --- Step 5: Timezone ---
    detected_tz = detect_timezone()
    config["timezone"] = ask("Timezone", default=detected_tz)

    # --- Step 6: Takeover level ---
    print(f"\n{BOLD}Step 6: Takeover Level{NC}")
    if detected_os == "macos":
        config["takeover"] = ask_choice(
            "How much control should the bot have?",
            [
                ("full", "Full takeover — remove unused apps, disable sleep, headless mode"),
                ("soft", "Soft install — bot runs in background, your apps stay"),
            ],
            default="full",
        )
    else:
        print("  Linux: Full takeover (strip desktop environment, disable sleep, server mode)")
        config["takeover"] = "full"

    # --- Step 7: Sudo password ---
    print(f"\n{BOLD}Step 7: System Access{NC}")
    print("  The bot needs sudo access to install software and manage services.")
    print("  Your password is stored locally at ~/.sudo_pass (readable only by you).")
    sudo_pass = ask("Sudo/admin password", secret=True)

    # Verify sudo password works
    info("Verifying sudo access...")
    verify = subprocess.run(
        ["sudo", "-S", "echo", "ok"],
        input=sudo_pass + "\n",
        capture_output=True, text=True, timeout=10
    )
    if verify.returncode != 0:
        error("Sudo password verification failed. Check your password and try again.")
    ok("Sudo access verified")

    # --- Detect machine specs ---
    info("Detecting machine specs...")
    machine_specs = detect_machine_specs()
    print(f"  Hostname: {machine_specs.get('hostname', '?')}")
    print(f"  CPU: {machine_specs.get('cpu', '?')}")
    print(f"  RAM: {machine_specs.get('ram_gb', '?')} GB")
    print(f"  Disk: {machine_specs.get('disk_free_gb', '?')} GB free / {machine_specs.get('disk_gb', '?')} GB total")
    if machine_specs.get("gpu"):
        print(f"  GPU: {machine_specs['gpu']}")
    print()

    # --- Write everything ---
    info("Saving configuration...")
    write_env(repo_dir, config)
    write_user_profile(repo_dir, config, machine_specs)
    store_sudo_password(sudo_pass)

    # --- Run provisioner ---
    print(f"\n{BOLD}Step 7: System Provisioning{NC}")
    print("  The installer will now configure your machine.")
    if config["takeover"] == "full":
        print("  This will remove unnecessary software and install the bot's dependencies.")
    else:
        print("  This will install the bot's dependencies without removing existing software.")
    print()

    # Offer dry-run first
    dry_run_first = input("  Preview changes first (dry run)? (Y/n): ").strip().lower()
    if dry_run_first != "n":
        info("Running dry run — no changes will be made...")
        subprocess.run(
            [
                sys.executable, str(repo_dir / "install" / "provisioner.py"),
                "--repo-dir", str(repo_dir),
                "--takeover", config["takeover"],
                "--dry-run",
            ],
        )
        print()
        proceed = input("  Proceed with actual provisioning? (Y/n): ").strip().lower()
    else:
        proceed = input("  Continue? (Y/n): ").strip().lower()

    if proceed == "n":
        ok("Configuration saved. Run the provisioner later with:")
        print(f"  python {repo_dir}/install/provisioner.py --repo-dir {repo_dir} --takeover {config['takeover']}")
        sys.exit(0)

    # Launch provisioner (it does its own OS detection via os_detect.py)
    result = subprocess.run(
        [
            sys.executable, str(repo_dir / "install" / "provisioner.py"),
            "--repo-dir", str(repo_dir),
            "--takeover", config["takeover"],
        ],
    )
    if result.returncode != 0:
        error("Provisioning failed. Check the output above for errors.")

    # Launch service installer (it does its own OS detection)
    result = subprocess.run(
        [
            sys.executable, str(repo_dir / "install" / "service.py"),
            "--repo-dir", str(repo_dir),
        ],
    )
    if result.returncode != 0:
        error("Service setup failed. Check the output above for errors.")

    # --- Done ---
    print()
    print(f"{BOLD}╔══════════════════════════════════════╗{NC}")
    print(f"{BOLD}║         Setup Complete!              ║{NC}")
    print(f"{BOLD}╚══════════════════════════════════════╝{NC}")
    print()
    print(f"  Your bot ({config['bot_name']}) is now running.")
    print(f"  Open Telegram and send /start to your bot.")
    print()
    print(f"  Useful commands:")
    print(f"    /status  — Check bot status")
    print(f"    /health  — System health report")
    print(f"    /update  — Update to latest version")
    print(f"    /help    — See all commands")
    print()
    if detected_os == "linux":
        print(f"  Service management:")
        print(f"    sudo systemctl status myoldmachine")
        print(f"    sudo systemctl restart myoldmachine")
        print(f"    journalctl -u myoldmachine -f")
    else:
        print(f"  Service management:")
        print(f"    launchctl list | grep myoldmachine")
        print(f"    tail -f {repo_dir}/data/logs/bot.log")
    print()


if __name__ == "__main__":
    main()
