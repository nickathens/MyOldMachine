#!/bin/bash
# MyOldMachine — One-Command Setup
# Converts any machine into a dedicated AI assistant controlled via Telegram.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
#   -- or --
#   git clone https://github.com/nickathens/MyOldMachine.git && cd MyOldMachine && ./install.sh

set -euo pipefail

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
                if [[ "$ID" == "ubuntu" || "$ID" == "debian" || "$ID_LIKE" == *"debian"* ]]; then
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

# Check if we're in a cloned repo or running standalone
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/bot.py" ]; then
    REPO_DIR="$SCRIPT_DIR"
    info "Running from cloned repository: $REPO_DIR"
else
    # Clone the repo
    info "Cloning MyOldMachine repository..."
    if ! command -v git &>/dev/null; then
        if [ "$OS" = "linux" ]; then
            sudo apt-get update -qq && sudo apt-get install -y -qq git
        else
            # On Mac, try xcode-select to get git
            info "Installing Xcode Command Line Tools (needed for git)..."
            xcode-select --install 2>/dev/null || true
            # Wait for the user to complete the Xcode CLT install
            echo ""
            echo -e "${YELLOW}If an Xcode install dialog appeared, complete it and press Enter.${NC}"
            echo -e "${YELLOW}If git is already installed, just press Enter.${NC}"
            read -r
            if ! command -v git &>/dev/null; then
                error "git is still not available. Install Xcode Command Line Tools first:
  xcode-select --install"
            fi
        fi
    fi
    REPO_DIR="$HOME/MyOldMachine"
    if [ -d "$REPO_DIR" ]; then
        warn "Directory $REPO_DIR already exists. Pulling latest..."
        cd "$REPO_DIR" && git pull
    else
        git clone https://github.com/nickathens/MyOldMachine.git "$REPO_DIR"
    fi
    cd "$REPO_DIR"
fi

# Ensure Python 3.10+
ensure_python() {
    local py=""
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" &>/dev/null; then
            local ver
            ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                py="$candidate"
                break
            fi
        fi
    done

    if [ -z "$py" ]; then
        info "Python 3.10+ not found. Installing..."
        if [ "$OS" = "linux" ]; then
            sudo apt-get update -qq
            # Try python3.12 first, fall back to default python3
            sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip 2>/dev/null || \
            sudo apt-get install -y -qq python3 python3-venv python3-pip
        else
            # macOS — need Homebrew for Python
            if ! command -v brew &>/dev/null; then
                info "Installing Homebrew (needed for Python)..."
                NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                # Add brew to PATH — check both Apple Silicon and Intel locations
                if [ -f /opt/homebrew/bin/brew ]; then
                    eval "$(/opt/homebrew/bin/brew shellenv)"
                elif [ -f /usr/local/bin/brew ]; then
                    eval "$(/usr/local/bin/brew shellenv)"
                fi
            fi
            if command -v brew &>/dev/null; then
                brew install python@3.12
            else
                error "Could not install Homebrew. Install Python 3.10+ manually and re-run."
            fi
        fi
        # Re-find python
        for candidate in python3.12 python3.11 python3.10 python3; do
            if command -v "$candidate" &>/dev/null; then
                local ver2
                ver2=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
                local major2 minor2
                major2=$(echo "$ver2" | cut -d. -f1)
                minor2=$(echo "$ver2" | cut -d. -f2)
                if [ "$major2" -ge 3 ] && [ "$minor2" -ge 10 ]; then
                    py="$candidate"
                    break
                fi
            fi
        done
    fi

    if [ -z "$py" ]; then
        error "Could not find or install Python 3.10+."
    fi

    echo "$py"
}

PYTHON=$(ensure_python)
ok "Python: $($PYTHON --version)"

# Create virtual environment
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

# Launch wizard — os_detect.py handles full version detection from here
info "Starting setup wizard..."
echo ""
python "$REPO_DIR/install/wizard.py" --repo-dir "$REPO_DIR" --os "$OS"
