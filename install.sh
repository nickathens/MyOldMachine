#!/bin/bash
# MyOldMachine — One-Command Setup
# Converts any machine into a dedicated AI assistant controlled via Telegram.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
#   -- or --
#   git clone https://github.com/nickathens/MyOldMachine.git && cd MyOldMachine && ./install.sh

set -eo pipefail
# Note: no -u (nounset) — BASH_SOURCE is empty when piped from curl

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# When piped from curl, stdin is the script itself — we need the real terminal
# for user input (password prompts, Xcode CLT confirmation, etc.)
if [ -t 0 ]; then
    # Running normally (not piped) — stdin is already the terminal
    TTY_INPUT="/dev/stdin"
else
    # Piped from curl — redirect user input from /dev/tty
    if [ -e /dev/tty ]; then
        TTY_INPUT="/dev/tty"
    else
        error "Cannot access terminal for user input. Run the installer directly instead:
  git clone https://github.com/nickathens/MyOldMachine.git && cd MyOldMachine && ./install.sh"
    fi
fi

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        MyOldMachine Installer        ║${NC}"
echo -e "${BOLD}║  Turn any machine into an AI helper  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""

# Detect OS — minimal check here, full detection happens in Python (os_detect.py)
detect_os() {
    case "$(uname -s)" in
        Linux*)
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                if [[ "$ID" == "ubuntu" || "$ID" == "debian" || "${ID_LIKE:-}" == *"debian"* ]]; then
                    echo "linux"
                else
                    error "Unsupported Linux distribution: $ID. Only Ubuntu/Debian are supported."
                fi
            else
                error "Cannot detect Linux distribution."
            fi
            ;;
        Darwin*)
            echo "macos"
            ;;
        *)
            error "Unsupported operating system: $(uname -s)"
            ;;
    esac
}

OS=$(detect_os)

# Show basic info (detailed version detection happens in Python)
if [ "$OS" = "macos" ]; then
    MACOS_VER=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
    ARCH=$(uname -m)
    info "macOS $MACOS_VER ($ARCH)"

    # Quick sanity check — block truly ancient macOS before we even try Python
    MACOS_MAJOR=$(echo "$MACOS_VER" | cut -d. -f1)
    MACOS_MINOR=$(echo "$MACOS_VER" | cut -d. -f2)
    if [ "$MACOS_MAJOR" -eq 10 ] 2>/dev/null && [ "${MACOS_MINOR:-0}" -lt 13 ] 2>/dev/null; then
        error "macOS $MACOS_VER is too old. Minimum supported version is 10.13 (High Sierra).
  Consider installing Linux on this machine instead."
    fi
else
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        info "$PRETTY_NAME ($(uname -m))"
    fi
fi

# ─────────────────────────────────────────────────────────
# Step 0: Get admin/sudo access BEFORE doing anything else
# ─────────────────────────────────────────────────────────

acquire_sudo() {
    info "This installer needs administrator access to set up your machine."
    echo ""

    if [ "$OS" = "macos" ]; then
        # Check if the current user is an admin (member of 'admin' group)
        CURRENT_USER=$(whoami)
        if ! dseditgroup -o checkmember -m "$CURRENT_USER" admin &>/dev/null; then
            echo -e "${RED}[ERROR]${NC} User '$CURRENT_USER' is not an administrator on this Mac."
            echo ""
            echo -e "  To fix this, you need someone with admin access to run:"
            echo -e "    ${BOLD}sudo dseditgroup -o edit -a $CURRENT_USER -t user admin${NC}"
            echo ""
            echo -e "  Or go to System Preferences > Users & Groups and make"
            echo -e "  '$CURRENT_USER' an administrator."
            echo ""
            echo -e "  After that, log out and log back in, then run this installer again."
            exit 1
        fi
    fi

    # Ask for password and cache sudo credentials for the session
    echo -e "  Enter your password once — it won't be asked again during install."
    echo ""

    # Try up to 3 times
    local attempts=0
    while [ $attempts -lt 3 ]; do
        if sudo -v < "$TTY_INPUT" 2>/dev/null; then
            ok "Administrator access granted"
            # Keep sudo alive in the background — refresh every 50 seconds
            # (sudo timeout is usually 5-15 minutes, this ensures it never expires)
            (while true; do sudo -n true 2>/dev/null; sleep 50; done) &
            SUDO_KEEPALIVE_PID=$!
            # Clean up the keepalive process when the script exits
            trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT
            return 0
        fi
        attempts=$((attempts + 1))
        if [ $attempts -lt 3 ]; then
            echo -e "  ${RED}Incorrect password. Try again (attempt $((attempts+1))/3).${NC}"
        fi
    done

    error "Could not get administrator access after 3 attempts."
}

acquire_sudo
echo ""

# ─────────────────────────────────────────────────────────
# Step 1: Ensure git is available
# ─────────────────────────────────────────────────────────

ensure_git() {
    if command -v git &>/dev/null; then
        return 0
    fi

    if [ "$OS" = "linux" ]; then
        info "Installing git..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq git
    else
        # macOS — need Xcode Command Line Tools for git
        info "Installing Xcode Command Line Tools (includes git)..."

        # Check if CLT is already installed but git isn't in PATH
        if xcode-select -p &>/dev/null; then
            # CLT installed but git not found — probably a PATH issue
            if [ -f /Library/Developer/CommandLineTools/usr/bin/git ]; then
                export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
                ok "Found git in Command Line Tools"
                return 0
            fi
        fi

        # Trigger the install
        xcode-select --install 2>/dev/null || true

        echo ""
        echo -e "${YELLOW}Xcode Command Line Tools are being installed.${NC}"
        echo -e "${YELLOW}This may take a few minutes — waiting for it to finish...${NC}"
        echo ""

        # Poll every 5 seconds until git becomes available or 10 minutes pass
        local elapsed=0
        local max_wait=600  # 10 minutes
        while [ $elapsed -lt $max_wait ]; do
            # Check if the install completed
            if command -v git &>/dev/null; then
                ok "Xcode Command Line Tools installed — git is available"
                return 0
            fi
            # Also check the direct path
            if [ -f /Library/Developer/CommandLineTools/usr/bin/git ]; then
                export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
                ok "Xcode Command Line Tools installed — git is available"
                return 0
            fi
            # Check if the installer finished (xcode-select -p succeeds)
            if xcode-select -p &>/dev/null 2>&1; then
                # CLT path is set — might just need PATH update
                local clt_path
                clt_path=$(xcode-select -p 2>/dev/null)
                if [ -f "$clt_path/usr/bin/git" ]; then
                    export PATH="$clt_path/usr/bin:$PATH"
                    ok "Xcode Command Line Tools installed — git is available"
                    return 0
                fi
            fi

            sleep 5
            elapsed=$((elapsed + 5))

            # Print a dot every 15 seconds so the user knows it's waiting
            if [ $((elapsed % 15)) -eq 0 ]; then
                echo -ne "."
            fi
        done

        echo ""
        # One final check
        if command -v git &>/dev/null; then
            ok "git is available"
            return 0
        fi

        error "Xcode Command Line Tools installation timed out after 10 minutes.
  Please install them manually:
    xcode-select --install
  Then re-run this installer."
    fi
}

ensure_git

# ─────────────────────────────────────────────────────────
# Step 2: Get the repository
# ─────────────────────────────────────────────────────────

# Detect if we're running from inside a cloned repo
# BASH_SOURCE may be empty when piped from curl — that's fine, we just clone
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || true
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bot.py" ]; then
    REPO_DIR="$SCRIPT_DIR"
    info "Running from cloned repository: $REPO_DIR"
else
    REPO_DIR="$HOME/MyOldMachine"
    if [ -d "$REPO_DIR/.git" ]; then
        info "Existing installation found. Pulling latest..."
        cd "$REPO_DIR" && git pull
    else
        info "Cloning MyOldMachine repository..."
        # Remove stale directory if it exists but isn't a git repo
        if [ -d "$REPO_DIR" ]; then
            rm -rf "$REPO_DIR"
        fi
        git clone https://github.com/nickathens/MyOldMachine.git "$REPO_DIR"
    fi
    cd "$REPO_DIR"
fi

# ─────────────────────────────────────────────────────────
# Step 3: Ensure Python 3.10+
# ─────────────────────────────────────────────────────────

find_python() {
    # Check common python binary names for 3.10+ in PATH
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" &>/dev/null; then
            local ver
            ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] 2>/dev/null && [ "$minor" -ge 10 ] 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done

    # Check Homebrew-specific locations directly (brew may not have linked into PATH)
    for brew_python in \
        /usr/local/opt/python@3.12/bin/python3.12 \
        /opt/homebrew/opt/python@3.12/bin/python3.12 \
        /usr/local/opt/python@3.11/bin/python3.11 \
        /opt/homebrew/opt/python@3.11/bin/python3.11 \
        /usr/local/Cellar/python@3.12/*/bin/python3.12 \
        /opt/homebrew/Cellar/python@3.12/*/bin/python3.12 \
        /usr/local/bin/python3.12 \
        /opt/homebrew/bin/python3.12; do
        if [ -x "$brew_python" ] 2>/dev/null; then
            echo "$brew_python"
            return 0
        fi
    done

    return 1
}

ensure_python() {
    local py
    py=$(find_python) && { echo "$py"; return 0; }

    info "Python 3.10+ not found. Installing..."
    if [ "$OS" = "linux" ]; then
        sudo apt-get update -qq
        # Try python3.12 first, fall back to default python3
        sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip 2>/dev/null || \
        sudo apt-get install -y -qq python3 python3-venv python3-pip
    else
        # macOS — need Homebrew for modern Python
        ensure_homebrew

        # brew install may return non-zero even on success (e.g. "post-install step did not complete"
        # on old macOS where bottles aren't available and it compiles from source).
        # We don't bail on failure — we check if python actually works afterward.
        brew install python@3.12 || warn "brew install returned an error (may still be OK — checking...)"

        # On old macOS, brew may not link python into PATH automatically.
        # Try to link it explicitly, ignoring errors if already linked.
        brew link --overwrite python@3.12 2>/dev/null || true

        # Ensure Homebrew's bin dirs are in PATH for this session
        # (Intel Mac: /usr/local/bin, Apple Silicon: /opt/homebrew/bin)
        for brew_bin in /usr/local/bin /opt/homebrew/bin /usr/local/opt/python@3.12/bin /opt/homebrew/opt/python@3.12/bin; do
            if [ -d "$brew_bin" ]; then
                case ":$PATH:" in
                    *":$brew_bin:"*) ;;
                    *) export PATH="$brew_bin:$PATH" ;;
                esac
            fi
        done

        # Also check Homebrew's Cellar for the python binary directly
        for cellar_python in /usr/local/Cellar/python@3.12/*/bin/python3.12 /opt/homebrew/Cellar/python@3.12/*/bin/python3.12; do
            if [ -x "$cellar_python" ] 2>/dev/null; then
                cellar_bin=$(dirname "$cellar_python")
                case ":$PATH:" in
                    *":$cellar_bin:"*) ;;
                    *) export PATH="$cellar_bin:$PATH" ;;
                esac
            fi
        done
    fi

    # Re-check after install
    py=$(find_python) && { echo "$py"; return 0; }

    # Last resort: search for any python3 binary in common locations
    for search_path in /usr/local/bin/python3* /opt/homebrew/bin/python3* /usr/local/Cellar/python*/*/bin/python3*; do
        if [ -x "$search_path" ] 2>/dev/null; then
            local ver
            ver=$("$search_path" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] 2>/dev/null && [ "$minor" -ge 10 ] 2>/dev/null; then
                echo "$search_path"
                return 0
            fi
        fi
    done

    error "Could not find or install Python 3.10+.
  Homebrew may have installed Python but it's not in PATH.
  Try running: brew link --overwrite python@3.12
  Then re-run this installer."
}

ensure_homebrew() {
    if command -v brew &>/dev/null; then
        return 0
    fi
    # Check known paths (brew might exist but not be in PATH)
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
        return 0
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
        return 0
    fi

    info "Installing Homebrew (package manager for macOS)..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add brew to PATH
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi

    if ! command -v brew &>/dev/null; then
        error "Homebrew installation failed. Install it manually:
  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"
  Then re-run this installer."
    fi

    ok "Homebrew installed"
}

PYTHON=$(ensure_python)
ok "Python: $($PYTHON --version)"

# ─────────────────────────────────────────────────────────
# Step 4: Virtual environment and dependencies
# ─────────────────────────────────────────────────────────

if [ ! -d "$REPO_DIR/.venv" ]; then
    info "Creating virtual environment..."
    $PYTHON -m venv "$REPO_DIR/.venv"
fi

# Activate venv
source "$REPO_DIR/.venv/bin/activate"
ok "Virtual environment active"

# Install Python dependencies
info "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$REPO_DIR/requirements.txt"
ok "Python dependencies installed"

# ─────────────────────────────────────────────────────────
# Step 5: Launch the setup wizard
# ─────────────────────────────────────────────────────────

info "Starting setup wizard..."
echo ""

# Pass TTY_INPUT so the wizard knows where to read user input from
# (relevant when piped from curl)
exec python "$REPO_DIR/install/wizard.py" --repo-dir "$REPO_DIR" --os "$OS" < "$TTY_INPUT"
