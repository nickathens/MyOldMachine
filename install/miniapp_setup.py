"""MyOldMachine Mini App installer.

Installs the optional Telegram Mini App as a background service bound to
127.0.0.1:8090 (systemd on Linux, a launchd LaunchAgent on macOS), then
guides the user through tunnel setup (Tailscale Funnel by default,
Cloudflare Tunnel as the advanced option, or LAN-only) and registers the
chat menu button via the Bot API for every user in users.json.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent

MINIAPP_PORT_DEFAULT = 8090

# macOS LaunchAgent identity (mirrors com.myoldmachine.bot on the bot service).
LAUNCH_AGENT_LABEL = "com.myoldmachine.miniapp"

# Output formatting — mirrors install/service.py / wizard.py palette.
BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def info(msg: str) -> None:
    print(f"{BLUE}[MA]{NC} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")


# ─── State helpers ───────────────────────────────────────────────────


def _launch_agent_plist_path() -> Path:
    """Path to the macOS LaunchAgent plist for the Mini App."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def is_miniapp_configured(repo_dir: Path = REPO_DIR) -> bool:
    """True if the Mini App service has been installed for this platform."""
    system = platform.system()
    if system == "Linux":
        return Path("/etc/systemd/system/myoldmachine-miniapp.service").exists()
    if system == "Darwin":
        return _launch_agent_plist_path().exists()
    return False


def _miniapp_port() -> int:
    """Port the Mini App listens on. Override with MINIAPP_PORT in .env."""
    raw = os.environ.get("MINIAPP_PORT", "").strip()
    if raw.isdigit():
        port = int(raw)
        if 1024 <= port <= 65535:
            return port
    return MINIAPP_PORT_DEFAULT


# ─── Step 1: Python deps ─────────────────────────────────────────────


def _install_python_deps(repo_dir: Path) -> bool:
    venv_pip = repo_dir / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        warn(f"Virtual environment not found at {venv_pip}. Skipping pip install.")
        return False
    info("Installing fastapi + uvicorn into venv...")
    try:
        result = subprocess.run(
            [str(venv_pip), "install", "fastapi>=0.110", "uvicorn[standard]>=0.27", "python-multipart>=0.0.6"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        warn("pip install timed out. Run manually:")
        warn(f"  {venv_pip} install fastapi 'uvicorn[standard]'")
        return False
    if result.returncode != 0:
        warn(f"pip install failed:\n{result.stderr.strip()}")
        return False
    ok("fastapi + uvicorn installed.")
    return True


# ─── Step 2: systemd service ─────────────────────────────────────────


def _install_systemd_service(repo_dir: Path, port: int, sudo_password: str | None) -> bool:
    """Render the miniapp service template and install it via sudo."""
    from install.service import (
        _discover_cli_bin_paths,
        sudo_run,
    )

    template = repo_dir / "install" / "templates" / "myoldmachine-miniapp.service"
    if not template.exists():
        error(f"Service template not found: {template}")
        return False

    username = getpass.getuser()
    venv_python = repo_dir / ".venv" / "bin" / "python"
    log_dir = repo_dir / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    install_user_home = Path(f"/home/{username}")
    if not install_user_home.is_dir():
        install_user_home = Path.home()

    content = template.read_text(encoding="utf-8")
    content = content.replace("{{USER}}", username)
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{LOG_DIR}}", str(log_dir))
    content = content.replace("{{MINIAPP_PORT}}", str(port))
    content = content.replace("{{BOT_SERVICE}}", "myoldmachine")
    content = content.replace("{{CLI_BIN_PATHS}}", _discover_cli_bin_paths(install_user_home))

    service_path = "/etc/systemd/system/myoldmachine-miniapp.service"
    fd, tmp_name = tempfile.mkstemp(suffix=".service", prefix="myoldmachine_miniapp_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        result = sudo_run(f"cp {shlex.quote(tmp_name)} {service_path}", sudo_password)
        if result.returncode != 0:
            error(f"Failed to install service: {result.stderr.strip()}")
            return False
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    info("Enabling systemd service...")
    sudo_run("systemctl daemon-reload", sudo_password)
    sudo_run("systemctl enable myoldmachine-miniapp", sudo_password)
    result = sudo_run("systemctl start myoldmachine-miniapp", sudo_password)
    if result.returncode != 0:
        warn(f"Service may not have started: {result.stderr.strip()[:200]}")
    else:
        check = sudo_run("systemctl is-active myoldmachine-miniapp", sudo_password)
        if "active" in check.stdout:
            ok(f"Mini App service running on 127.0.0.1:{port}")
        else:
            warn("Service registered but may not be active. Check:")
            warn("  sudo systemctl status myoldmachine-miniapp")
    return True


# ─── Step 2b: launchd LaunchAgent (macOS) ────────────────────────────


def _render_miniapp_plist(template_text: str, repo_dir: Path, port: int, home: Path) -> str:
    """Fill the LaunchAgent plist template. Pure (no I/O), so it is unit-testable."""
    from install.service import _discover_cli_bin_paths

    venv_python = repo_dir / ".venv" / "bin" / "python"
    content = template_text
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    content = content.replace("{{ENV_FILE}}", str(repo_dir / ".env"))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))
    content = content.replace("{{VENV_BIN}}", str(repo_dir / ".venv" / "bin"))
    content = content.replace("{{MINIAPP_PORT}}", str(port))
    content = content.replace("{{HOME}}", str(home))
    content = content.replace("{{CLI_BIN_PATHS}}", _discover_cli_bin_paths(home))
    return content


def _install_launchd_service(repo_dir: Path, port: int) -> bool:
    """Render the Mini App LaunchAgent and load it via launchctl. Returns True
    on success.

    Writes to ~/Library/LaunchAgents/com.myoldmachine.miniapp.plist and loads
    it in the per-user GUI domain, mirroring how the bot registers its own
    LaunchAgent in install/service.py. No sudo: the plist lives in the user's
    own Library, unlike the Linux systemd unit under /etc.
    """
    from install.os_detect import detect as detect_os
    from install.service import _launchctl_load

    template = repo_dir / "install" / "templates" / "com.myoldmachine.miniapp.plist"
    if not template.exists():
        error(f"Plist template not found: {template}")
        return False

    venv_python = repo_dir / ".venv" / "bin" / "python"
    if not venv_python.exists():
        error(f"Virtual environment not found at {venv_python}")
        warn("Run the installer again to create it.")
        return False

    home = Path.home()
    content = _render_miniapp_plist(
        template.read_text(encoding="utf-8"), repo_dir, port, home
    )

    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    plist_path = _launch_agent_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        plist_path.write_text(content, encoding="utf-8")
    except OSError as e:
        error(f"Failed to write LaunchAgent plist: {e}")
        return False

    _launchctl_load(plist_path, detect_os(), system_wide=False)
    ok(f"Mini App LaunchAgent installed at {plist_path}")
    info(f"It binds 127.0.0.1:{port}, starts on login, and restarts on crash.")
    return True


# ─── Step 3: Tunnel guidance ─────────────────────────────────────────


def _pick_tunnel(ask) -> str:
    """Return 'tailscale', 'cloudflare', 'lan', or 'skip'."""
    print()
    print(f"  {BOLD}Tunnel options{NC}")
    print("    1) Tailscale Funnel  — free, persistent URL, no domain needed (recommended)")
    print("    2) Cloudflare Tunnel — requires a domain on Cloudflare DNS")
    print("    3) LAN only          — bind locally, reach via http://<host>:port from same network")
    print("    4) Skip              — install service only; you'll set up the tunnel later")
    while True:
        answer = ask("Choose [1]", default="1").strip()
        if answer in ("1", "tailscale", "t", ""):
            return "tailscale"
        if answer in ("2", "cloudflare", "cf", "c"):
            return "cloudflare"
        if answer in ("3", "lan", "l"):
            return "lan"
        if answer in ("4", "skip", "s"):
            return "skip"
        print("Pick 1, 2, 3, or 4.")


def _tailscale_present() -> bool:
    return shutil.which("tailscale") is not None


def _tailscale_funnel_status(port: int) -> str | None:
    """Best-effort: probe whether tailscale funnel is already serving our port.
    Returns the public URL on hit, None otherwise."""
    if not _tailscale_present():
        return None
    try:
        result = subprocess.run(
            ["tailscale", "funnel", "status"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    # Output usually contains a line like "https://hostname.ts.net (Funnel on)"
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("https://") and ".ts.net" in line:
            return line.split()[0]
    return None


def _setup_tailscale(port: int) -> str | None:
    """Guide the user through tailscale + funnel setup. Manual where it must be.

    Returns the public Funnel URL if we could detect one, otherwise None
    (the user gets clear next-step instructions either way).
    """
    print()
    print(f"  {BOLD}Tailscale Funnel setup{NC}")
    if not _tailscale_present():
        print("  Tailscale is not installed on this machine.")
        print()
        if platform.system() == "Darwin":
            print("  Install (macOS):")
            print(f"    {BOLD}brew install tailscale{NC}    # then: sudo tailscaled install-system-daemon")
            print("    or install the Tailscale app from the Mac App Store")
        else:
            print("  Install (Linux):")
            print(f"    {BOLD}curl -fsSL https://tailscale.com/install.sh | sh{NC}")
        print()
        print("  Then run, in order:")
        print(f"    {BOLD}sudo tailscale up{NC}              # follow the login URL in your browser")
        print(f"    {BOLD}sudo tailscale funnel {port}{NC}   # expose the Mini App publicly")
        print()
        print("  After that, `tailscale funnel status` will show your URL.")
        print("  Re-run the install wizard once funnel is up — the wizard will")
        print("  detect the URL and register the chat menu button automatically.")
        return None

    ok("Tailscale binary found.")
    existing = _tailscale_funnel_status(port)
    if existing:
        ok(f"Funnel already serving: {existing}")
        return existing

    print()
    print(f"  Tailscale is installed but not yet serving Funnel for port {port}.")
    print()
    print("  Run:")
    print(f"    {BOLD}sudo tailscale up{NC}              # if you haven't already")
    print(f"    {BOLD}sudo tailscale funnel {port}{NC}   # expose the Mini App publicly")
    print()
    print("  Then `tailscale funnel status` will print your URL.")
    return None


def _cloudflared_present() -> bool:
    return shutil.which("cloudflared") is not None


def _setup_cloudflare(port: int) -> str | None:
    """Guide the user through cloudflared named-tunnel setup."""
    print()
    print(f"  {BOLD}Cloudflare Tunnel setup{NC}")
    if not _cloudflared_present():
        print("  cloudflared is not installed.")
        print()
        print("  Install:")
        if platform.system() == "Darwin":
            print(f"    {BOLD}brew install cloudflared{NC}")
        else:
            print(f"    {BOLD}curl -fsSL https://pkg.cloudflare.com/install.sh | sh{NC}")
            print("    # then: sudo apt install cloudflared    (or your distro's equivalent)")
        print()
    print("  Steps (you only do these once):")
    print(f"    1) {BOLD}cloudflared tunnel login{NC}")
    print("       authorizes cloudflared with your Cloudflare account in a browser")
    print(f"    2) {BOLD}cloudflared tunnel create mom-miniapp{NC}")
    print("       creates the named tunnel (writes a credentials JSON to ~/.cloudflared/)")
    print(f"    3) Create {BOLD}~/.cloudflared/config.yml{NC}:")
    print("       ----------------------------------------")
    print("       tunnel: mom-miniapp")
    print("       credentials-file: /home/<you>/.cloudflared/<tunnel-id>.json")
    print("       ingress:")
    print("         - hostname: <chosen-subdomain>.yourdomain.com")
    print(f"           service: http://127.0.0.1:{port}")
    print("         - service: http_status:404")
    print("       ----------------------------------------")
    print(f"    4) {BOLD}cloudflared tunnel route dns mom-miniapp <chosen-subdomain>.yourdomain.com{NC}")
    print(f"    5) {BOLD}sudo cloudflared service install{NC}    # run as systemd service")
    print()
    print("  Your Mini App will be reachable at https://<chosen-subdomain>.yourdomain.com")
    print("  After it's live, re-run the wizard and the menu button will be set automatically.")
    return None


def _detect_lan_ip() -> str | None:
    """Best-effort primary LAN IPv4, cross-platform (Linux + macOS).

    Opens a UDP socket toward a reserved, never-routed address to learn which
    local interface the OS would route through, then reads that interface's IP.
    No packets are sent (a UDP connect only sets the route), so it works offline
    as long as a default route exists. Returns None if no usable IP is found.
    """
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 192.0.2.0/24 is TEST-NET-1 (RFC 5737): reserved, never actually reached.
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None
    if not ip or ip.startswith("127."):
        return None
    return ip


def _setup_lan(port: int) -> str | None:
    """Print LAN-only guidance and return a guessed URL."""
    print()
    print(f"  {BOLD}LAN-only setup{NC}")
    info("Detecting LAN IP...")
    ip = _detect_lan_ip()

    if ip:
        url = f"http://{ip}:{port}"
        ok(f"Mini App reachable on your LAN at: {url}")
    else:
        url = None
        warn("Could not auto-detect a LAN IP.")
        print("  Find yours with `ip addr` (Linux) or `ipconfig getifaddr en0` (macOS).")
        print(f"  Then the URL is: http://<your-ip>:{port}")

    print()
    print(f"  {YELLOW}Heads-up:{NC} Telegram's WebView requires HTTPS for some clients.")
    print("  Mobile clients may refuse to load plain http:// URLs. If you hit that,")
    print("  switch to Tailscale Funnel (option 1) for a free HTTPS endpoint.")
    return url


# ─── Step 4: chat menu button registration ───────────────────────────


def _registered_users(repo_dir: Path) -> list[str]:
    users_json = repo_dir / "data" / "users.json"
    if not users_json.exists():
        return []
    try:
        data = json.loads(users_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    return [tid for tid in data.keys() if tid.isdigit()]


def _set_menu_button(token: str, telegram_id: str, web_url: str) -> bool:
    """POST setChatMenuButton via the public Bot API. Returns True on ok=True."""
    api_base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    endpoint = f"{api_base}/bot{token}/setChatMenuButton"
    payload = {
        "chat_id": int(telegram_id),
        "menu_button": {
            "type": "web_app",
            "text": "App",
            "web_app": {"url": web_url},
        },
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError):
        return False


def _register_menu_buttons(repo_dir: Path, web_url: str) -> None:
    """Best-effort: set the menu button for every registered user. Logs results."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        warn("TELEGRAM_BOT_TOKEN not set in env. Skipping menu button registration.")
        return
    users = _registered_users(repo_dir)
    if not users:
        warn("No registered users in data/users.json. Skipping menu button registration.")
        return
    info(f"Registering chat menu button for {len(users)} user(s)...")
    successes = 0
    for tid in users:
        if _set_menu_button(token, tid, web_url):
            successes += 1
        else:
            warn(f"  Failed for user {tid}")
    if successes:
        ok(f"Menu button set for {successes}/{len(users)} user(s).")
    else:
        warn("No menu buttons set. Verify TELEGRAM_BOT_TOKEN and that the bot has chats with each user.")


# ─── Orchestration ───────────────────────────────────────────────────


def run_miniapp_setup_step(config: dict, ask=None) -> None:
    """Interactive Mini App install. Designed to plug into wizard.OPTIONAL_FEATURES."""
    system = platform.system()
    if system not in ("Linux", "Darwin"):
        warn("Mini App requires Linux (systemd) or macOS (launchd). Skipping.")
        return

    if ask is None:
        from install.wizard import ask as _ask
        ask = _ask

    print()
    print(f"  {BOLD}Telegram Mini App{NC}")
    print("  Visual dashboard for the bot: provider, model, schedule (calendar),")
    print("  projects, and quick-launch skills. Adds a button next to the chat")
    print("  input in Telegram (mobile, desktop, web).")
    print()
    print(f"  {YELLOW}Installs:{NC}")
    print("    1. fastapi + uvicorn into the venv")
    print("    2. A background service on 127.0.0.1:8090 that starts automatically")
    print("    3. Tunnel of your choice (Tailscale / Cloudflare / LAN)")
    print("    4. Telegram chat menu button for each registered user")
    print()

    answer = ask("Install the Mini App now?", default="n").strip().lower()
    if answer not in ("y", "yes"):
        ok("Skipping Mini App. The bot runs without it.")
        return

    if not _install_python_deps(REPO_DIR):
        warn("Could not install fastapi/uvicorn. Aborting Mini App install.")
        return

    port = _miniapp_port()
    if system == "Linux":
        # Sudo password is cached by install.sudo; this re-uses it.
        from install.service import get_sudo_password
        sudo_password = get_sudo_password()
        if not _install_systemd_service(REPO_DIR, port, sudo_password):
            warn("Service install failed. The Python code is in place. You can")
            warn("install the systemd unit manually from install/templates/.")
            return
    else:  # Darwin
        if not _install_launchd_service(REPO_DIR, port):
            warn("Service install failed. The Python code is in place. You can")
            warn("install the LaunchAgent manually from install/templates/.")
            return

    config["miniapp_enabled"] = True
    config["miniapp_port"] = port

    tunnel = _pick_tunnel(ask)
    config["miniapp_tunnel"] = tunnel

    public_url: str | None = None
    if tunnel == "tailscale":
        public_url = _setup_tailscale(port)
    elif tunnel == "cloudflare":
        public_url = _setup_cloudflare(port)
    elif tunnel == "lan":
        public_url = _setup_lan(port)
    else:
        info("Skipping tunnel setup. You'll need to expose 127.0.0.1:%d yourself." % port)

    if public_url:
        _register_menu_buttons(REPO_DIR, public_url)
        config["miniapp_url"] = public_url
    else:
        print()
        print(f"  {YELLOW}When your tunnel is up:{NC}")
        print("    Set the menu button with this curl:")
        print()
        print("      curl -sX POST \\\\")
        print("        \"$TELEGRAM_API/bot$TELEGRAM_BOT_TOKEN/setChatMenuButton\" \\\\")
        print("        -H 'Content-Type: application/json' \\\\")
        print("        -d '{\"chat_id\":<your-tg-id>,\"menu_button\":{\"type\":\"web_app\",\"text\":\"App\",\"web_app\":{\"url\":\"<public-url>/\"}}}'")
        print()
        print("    Or simply re-run the install wizard once the tunnel is reachable.")
