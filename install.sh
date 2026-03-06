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

# Detect OS
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
info "Detected OS: $OS"

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
            error "git is required. Install it with: xcode-select --install"
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
        info "Installing Python 3.12..."
        if [ "$OS" = "linux" ]; then
            sudo apt-get update -qq
            sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip 2>/dev/null || \
            sudo apt-get install -y -qq python3 python3-venv python3-pip
        else
            if ! command -v brew &>/dev/null; then
                info "Installing Homebrew..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
            fi
            brew install python@3.12
        fi
        # Re-find python
        for candidate in python3.12 python3.11 python3.10 python3; do
            if command -v "$candidate" &>/dev/null; then
                py="$candidate"
                break
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

# Launch wizard
info "Starting setup wizard..."
echo ""
python "$REPO_DIR/install/wizard.py" --repo-dir "$REPO_DIR" --os "$OS"
