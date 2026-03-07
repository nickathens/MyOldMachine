#!/bin/bash
# MyOldMachine — One-Command Setup
# Converts any machine into a dedicated AI assistant controlled via Telegram.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
#   -- or --
#   git clone https://github.com/nickathens/MyOldMachine.git && cd MyOldMachine && ./install.sh

# NOTE: We use set -o pipefail but NOT set -e or set -u.
# -e (errexit) kills the script on ANY non-zero exit, including intentional ones
#   (e.g. brew returning 1 on a post-install warning). We handle errors manually.
# -u (nounset) kills the script when BASH_SOURCE is empty (curl|bash mode).
set -o pipefail

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
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ─────────────────────────────────────────────────────────
# Checkpoint system — resume from last successful step
# ─────────────────────────────────────────────────────────

CHECKPOINT_FILE="$HOME/.myoldmachine_install_checkpoints"

checkpoint_done() {
    grep -qxF "$1" "$CHECKPOINT_FILE" 2>/dev/null
}

checkpoint_set() {
    echo "$1" >> "$CHECKPOINT_FILE"
}

# ─────────────────────────────────────────────────────────
# Terminal input handling
# ─────────────────────────────────────────────────────────

if [ -t 0 ]; then
    TTY_INPUT="/dev/stdin"
else
    if [ -e /dev/tty ]; then
        TTY_INPUT="/dev/tty"
    else
        die "Cannot access terminal for user input. Run the installer directly instead:
  git clone https://github.com/nickathens/MyOldMachine.git && cd MyOldMachine && ./install.sh"
    fi
fi

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        MyOldMachine Installer        ║${NC}"
echo -e "${BOLD}║  Turn any machine into an AI helper  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""

# Handle existing checkpoints — ask fresh install or resume
if [ -f "$CHECKPOINT_FILE" ]; then
    completed=$(wc -l < "$CHECKPOINT_FILE" | tr -d ' ')
    echo -e "  ${YELLOW}Previous installation detected (${completed} step(s) completed).${NC}"
    echo ""
    echo "  1. Fresh install — start from scratch (recommended if changing provider)"
    echo "  2. Resume — continue where you left off"
    echo ""
    printf "  Choice [1]: "
    read -r resume_choice < "$TTY_INPUT"
    resume_choice="${resume_choice:-1}"
    if [ "$resume_choice" != "2" ]; then
        info "Starting fresh install..."
        rm -f "$CHECKPOINT_FILE"
        # Also remove stale .env so wizard doesn't skip
        # Check both possible repo locations
        for env_loc in "$HOME/MyOldMachine/.env" "${BASH_SOURCE[0]:+$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/.env}"; do
            [ -n "$env_loc" ] && [ -f "$env_loc" ] && rm -f "$env_loc"
        done
    else
        info "Resuming installation (${completed} step(s) already completed)"
    fi
    echo ""
fi

# ─────────────────────────────────────────────────────────
# Step 0: Detect OS
# ─────────────────────────────────────────────────────────

detect_os() {
    case "$(uname -s)" in
        Linux*)
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                if [[ "$ID" == "ubuntu" || "$ID" == "debian" || "${ID_LIKE:-}" == *"debian"* ]]; then
                    echo "linux"
                else
                    die "Unsupported Linux distribution: $ID. Only Ubuntu/Debian are supported."
                fi
            else
                die "Cannot detect Linux distribution."
            fi
            ;;
        Darwin*)
            echo "macos"
            ;;
        *)
            die "Unsupported operating system: $(uname -s)"
            ;;
    esac
}

OS=$(detect_os)

if [ "$OS" = "macos" ]; then
    MACOS_VER=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
    ARCH=$(uname -m)
    info "macOS $MACOS_VER ($ARCH)"

    MACOS_MAJOR=$(echo "$MACOS_VER" | cut -d. -f1)
    MACOS_MINOR=$(echo "$MACOS_VER" | cut -d. -f2)
    if [ "${MACOS_MAJOR:-0}" -eq 10 ] 2>/dev/null && [ "${MACOS_MINOR:-0}" -lt 13 ] 2>/dev/null; then
        die "macOS $MACOS_VER is too old. Minimum supported version is 10.13 (High Sierra).
  Consider installing Linux on this machine instead."
    fi
else
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        info "$PRETTY_NAME ($(uname -m))"
    fi
fi

# ─────────────────────────────────────────────────────────
# Step 1: Get admin/sudo access
# ─────────────────────────────────────────────────────────

if ! checkpoint_done "sudo"; then
    info "This installer needs administrator access to set up your machine."
    echo ""

    if [ "$OS" = "macos" ]; then
        CURRENT_USER=$(whoami)
        if ! dseditgroup -o checkmember -m "$CURRENT_USER" admin &>/dev/null; then
            err "User '$CURRENT_USER' is not an administrator on this Mac."
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

    echo -e "  Enter your password once — it won't be asked again during install."
    echo ""

    attempts=0
    sudo_ok=false
    while [ $attempts -lt 3 ]; do
        if sudo -v < "$TTY_INPUT" 2>/dev/null; then
            sudo_ok=true
            break
        fi
        attempts=$((attempts + 1))
        if [ $attempts -lt 3 ]; then
            echo -e "  ${RED}Incorrect password. Try again (attempt $((attempts+1))/3).${NC}"
        fi
    done

    if [ "$sudo_ok" = false ]; then
        die "Could not get administrator access after 3 attempts."
    fi

    ok "Administrator access granted"
    checkpoint_set "sudo"
fi

# Keep sudo alive in the background
(while true; do sudo -n true 2>/dev/null; sleep 50; done) &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT

echo ""

# ─────────────────────────────────────────────────────────
# Step 2: Ensure git is available
# ─────────────────────────────────────────────────────────

if ! checkpoint_done "git"; then
    if command -v git &>/dev/null; then
        ok "git is available"
    else
        if [ "$OS" = "linux" ]; then
            info "Installing git..."
            sudo apt-get update -qq 2>/dev/null || warn "apt-get update had warnings"
            sudo apt-get install -y -qq git || die "Failed to install git"
        else
            # macOS — Xcode Command Line Tools
            if xcode-select -p &>/dev/null; then
                if [ -f /Library/Developer/CommandLineTools/usr/bin/git ]; then
                    export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
                fi
            fi

            if ! command -v git &>/dev/null; then
                info "Installing Xcode Command Line Tools (includes git)..."
                xcode-select --install 2>/dev/null || true

                echo ""
                echo -e "${YELLOW}Waiting for Xcode Command Line Tools installation...${NC}"

                elapsed=0
                max_wait=600
                while [ $elapsed -lt $max_wait ]; do
                    if command -v git &>/dev/null; then break; fi
                    if [ -f /Library/Developer/CommandLineTools/usr/bin/git ]; then
                        export PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH"
                        break
                    fi
                    if xcode-select -p &>/dev/null 2>&1; then
                        clt_path=$(xcode-select -p 2>/dev/null)
                        if [ -f "$clt_path/usr/bin/git" ]; then
                            export PATH="$clt_path/usr/bin:$PATH"
                            break
                        fi
                    fi
                    sleep 5
                    elapsed=$((elapsed + 5))
                    if [ $((elapsed % 15)) -eq 0 ]; then echo -ne "."; fi
                done
                echo ""

                if ! command -v git &>/dev/null; then
                    die "Xcode Command Line Tools installation timed out.
  Install manually: xcode-select --install
  Then re-run this installer."
                fi
            fi
        fi
        ok "git is available"
    fi
    checkpoint_set "git"
fi

# ─────────────────────────────────────────────────────────
# Step 3: Get the repository
# ─────────────────────────────────────────────────────────

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
        cd "$REPO_DIR" && git pull || warn "git pull failed — continuing with existing code"
    else
        info "Cloning MyOldMachine repository..."
        if [ -d "$REPO_DIR" ]; then
            rm -rf "$REPO_DIR"
        fi
        git clone https://github.com/nickathens/MyOldMachine.git "$REPO_DIR" || die "Failed to clone repository"
    fi
    cd "$REPO_DIR"
fi

# ─────────────────────────────────────────────────────────
# Step 4: Ensure Python 3.10+
# ─────────────────────────────────────────────────────────

find_python() {
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" &>/dev/null; then
            local ver
            ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "${major:-0}" -ge 3 ] 2>/dev/null && [ "${minor:-0}" -ge 10 ] 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done

    # Homebrew paths (linked and Cellar)
    for brew_python in \
        /usr/local/opt/python@3.12/bin/python3.12 \
        /opt/homebrew/opt/python@3.12/bin/python3.12 \
        /usr/local/opt/python@3.11/bin/python3.11 \
        /opt/homebrew/opt/python@3.11/bin/python3.11 \
        /usr/local/opt/python@3.12/libexec/bin/python3 \
        /opt/homebrew/opt/python@3.12/libexec/bin/python3 \
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

ensure_homebrew() {
    if command -v brew &>/dev/null; then return 0; fi
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"; return 0
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"; return 0
    fi

    info "Installing Homebrew (package manager for macOS)..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/null

    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi

    if ! command -v brew &>/dev/null; then
        die "Homebrew installation failed. Install it manually:
  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"
  Then re-run this installer."
    fi
    ok "Homebrew installed"
}

if ! checkpoint_done "python"; then
    PYTHON=$(find_python) || true

    if [ -z "$PYTHON" ]; then
        info "Python 3.10+ not found. Installing..."

        if [ "$OS" = "linux" ]; then
            sudo apt-get update -qq 2>/dev/null || warn "apt-get update had warnings"
            sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip 2>/dev/null || \
            sudo apt-get install -y -qq python3 python3-venv python3-pip || \
                die "Failed to install Python"
        else
            ensure_homebrew

            info "Installing Python 3.12 via Homebrew..."
            info "(On older macOS, this compiles from source — may take 15-30 minutes)"

            # Stream output so user sees progress during long compilations
            brew install python@3.12 2>&1 | while IFS= read -r line; do
                echo "    $line"
            done
            # Note: PIPESTATUS doesn't work in all shells, so we verify below

            # Force-link python into PATH
            brew link --overwrite python@3.12 2>/dev/null || true

            # Add all possible Homebrew Python paths
            for brew_bin in /usr/local/bin /opt/homebrew/bin \
                /usr/local/opt/python@3.12/bin /opt/homebrew/opt/python@3.12/bin \
                /usr/local/opt/python@3.12/libexec/bin /opt/homebrew/opt/python@3.12/libexec/bin; do
                if [ -d "$brew_bin" ]; then
                    case ":$PATH:" in
                        *":$brew_bin:"*) ;;
                        *) export PATH="$brew_bin:$PATH" ;;
                    esac
                fi
            done

            for cellar_python in /usr/local/Cellar/python@3.12/*/bin/python3.12 \
                /opt/homebrew/Cellar/python@3.12/*/bin/python3.12; do
                if [ -x "$cellar_python" ] 2>/dev/null; then
                    cellar_bin=$(dirname "$cellar_python")
                    case ":$PATH:" in
                        *":$cellar_bin:"*) ;;
                        *) export PATH="$cellar_bin:$PATH" ;;
                    esac
                fi
            done

            # Clear bash's command hash table so it picks up the new PATH
            hash -r 2>/dev/null
        fi

        PYTHON=$(find_python) || true

        # Last resort search
        if [ -z "$PYTHON" ]; then
            for search_path in /usr/local/bin/python3* /opt/homebrew/bin/python3* \
                /usr/local/Cellar/python*/*/bin/python3* /opt/homebrew/Cellar/python*/*/bin/python3* \
                /usr/local/opt/python*/bin/python3* /opt/homebrew/opt/python*/bin/python3* \
                /usr/local/opt/python*/libexec/bin/python3*; do
                if [ -x "$search_path" ] 2>/dev/null; then
                    ver=$("$search_path" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0")
                    major=$(echo "$ver" | cut -d. -f1)
                    minor=$(echo "$ver" | cut -d. -f2)
                    if [ "${major:-0}" -ge 3 ] 2>/dev/null && [ "${minor:-0}" -ge 10 ] 2>/dev/null; then
                        PYTHON="$search_path"
                        break
                    fi
                fi
            done
        fi

        if [ -z "$PYTHON" ]; then
            die "Could not find or install Python 3.10+.
  If using macOS, try: brew link --overwrite python@3.12
  Then re-run this installer."
        fi
    fi

    ok "Python: $($PYTHON --version)"
    checkpoint_set "python"
else
    PYTHON=$(find_python) || die "Python was previously installed but can't be found. Remove $CHECKPOINT_FILE and re-run."
    ok "Python: $($PYTHON --version) (cached)"
fi

# ─────────────────────────────────────────────────────────
# Step 5: Virtual environment and dependencies
# ─────────────────────────────────────────────────────────

if ! checkpoint_done "venv"; then
    if [ ! -d "$REPO_DIR/.venv" ]; then
        info "Creating virtual environment..."
        $PYTHON -m venv "$REPO_DIR/.venv" || die "Failed to create virtual environment.
  On Ubuntu, try: sudo apt install python3-venv"
    fi

    source "$REPO_DIR/.venv/bin/activate" || die "Failed to activate virtual environment"
    ok "Virtual environment active"

    info "Installing Python dependencies..."
    pip install --quiet --upgrade pip 2>/dev/null || warn "pip upgrade had warnings"
    pip install --quiet -r "$REPO_DIR/requirements.txt" || die "Failed to install Python dependencies"
    ok "Python dependencies installed"

    checkpoint_set "venv"
else
    source "$REPO_DIR/.venv/bin/activate" || die "Failed to activate virtual environment"
    ok "Virtual environment ready (cached)"
fi

# ─────────────────────────────────────────────────────────
# Step 6: Launch the setup wizard
# ─────────────────────────────────────────────────────────

info "Starting setup wizard..."
echo ""

# Pass checkpoint file path so wizard and provisioner can use it
export MYOLDMACHINE_CHECKPOINT_FILE="$CHECKPOINT_FILE"
exec "$REPO_DIR/.venv/bin/python" "$REPO_DIR/install/wizard.py" --repo-dir "$REPO_DIR" --os "$OS" < "$TTY_INPUT"
