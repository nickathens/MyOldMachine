#!/bin/bash
# MyOldMachine — One-Command Setup
# Converts any machine into a dedicated AI assistant controlled via Telegram.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/nickathens/MyOldMachine/main/install.sh | bash
#   -- or --
#   git clone https://github.com/nickathens/MyOldMachine.git && cd MyOldMachine && ./install.sh

# When invoked as `curl ... | bash`, the script body lives on stdin. Any
# command inside the script that reads stdin (brew install, pip install,
# auth prompts) can consume script bytes that bash has not yet parsed —
# bash then hits EOF and exits cleanly between steps with no error message.
# Defend by spooling the script to a real file and re-executing.
if [ -z "${BASH_SOURCE[0]:-}" ] && [ ! -t 0 ]; then
    _spool=$(mktemp -t myoldmachine_install.XXXXXXX 2>/dev/null) || \
        _spool=$(mktemp /tmp/myoldmachine_install.XXXXXX 2>/dev/null)
    if [ -z "$_spool" ]; then
        echo "[ERROR] Could not create temp file for installer" >&2
        exit 1
    fi
    cat > "$_spool"
    bash "$_spool" "$@"
    _rc=$?
    rm -f "$_spool"
    exit "$_rc"
fi

# NOTE: We use set -o pipefail but NOT set -e or set -u.
# -e (errexit) kills the script on ANY non-zero exit, including intentional ones
#   (e.g. brew returning 1 on a post-install warning). We handle errors manually.
# -u (nounset) kills the script when BASH_SOURCE is empty (curl|bash mode).
set -o pipefail

# ─────────────────────────────────────────────────────────
# CLI flags
# ─────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h)
            cat <<'EOF'
MyOldMachine installer

Usage:
  ./install.sh    Standard install (default)

EOF
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            echo "[ERROR] Unknown flag: $1" >&2
            echo "Run './install.sh --help' for usage." >&2
            exit 2
            ;;
    esac
done

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
        # Remove stale .env from ALL possible repo locations so wizard runs from scratch
        for env_loc in \
            "$HOME/MyOldMachine/.env" \
            "${BASH_SOURCE[0]:+$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/.env}" \
            "$(pwd)/.env"; do
            [ -n "$env_loc" ] && [ -f "$env_loc" ] && rm -f "$env_loc" && info "Removed stale $env_loc"
        done
        # Detect orphan-owned subdirs under data/ from a prior install with
        # different ownership. Without sudo, "rm -rf ~/MyOldMachine" can't
        # enter mode-0700 dirs owned by another user, so they survive and
        # break the next wizard run. Only check inside actual MyOldMachine
        # repo dirs (install.sh present) to avoid wiping unrelated paths.
        for repo_loc in "$HOME/MyOldMachine" "$(pwd)"; do
            [ -d "$repo_loc/data" ] && [ -f "$repo_loc/install.sh" ] || continue
            orphan_count=$(find "$repo_loc/data" ! -user "$(id -un)" -print 2>/dev/null | head -1 | wc -l | tr -d ' ')
            if [ "${orphan_count:-0}" -gt 0 ]; then
                warn "Detected leftover files under $repo_loc/data not owned by you."
                warn "Cleaning with sudo..."
                if sudo rm -rf "$repo_loc/data"; then
                    info "Removed stale $repo_loc/data"
                else
                    warn "Could not remove $repo_loc/data — install may fail. Try: sudo rm -rf $repo_loc/data"
                fi
                break
            fi
        done
        # Tear down stale system services from a previous install so a
        # fresh install does not inherit a crash-looping LaunchDaemon /
        # systemd unit referencing keys or users we have removed.
        # NOTE: $OS is not yet detected at this point in the script (the
        # detect_os step runs after this block), so we re-check via
        # `uname -s` inline.
        case "$(uname -s)" in
            Darwin)
                for label in com.myoldmachine.bot com.telegram-bot-api; do
                    plist="/Library/LaunchDaemons/${label}.plist"
                    if [ -f "$plist" ]; then
                        info "Removing stale LaunchDaemon: $plist"
                        sudo launchctl bootout "system/${label}" 2>/dev/null || true
                        sudo launchctl unload "$plist" 2>/dev/null || true
                        sudo rm -f "$plist" 2>/dev/null || true
                    fi
                done
                ;;
            Linux)
                for unit in myoldmachine telegram-bot-api; do
                    unit_path="/etc/systemd/system/${unit}.service"
                    if [ -f "$unit_path" ]; then
                        info "Removing stale systemd unit: $unit_path"
                        sudo systemctl stop "$unit" 2>/dev/null || true
                        sudo systemctl disable "$unit" 2>/dev/null || true
                        sudo rm -f "$unit_path" 2>/dev/null || true
                        sudo systemctl daemon-reload 2>/dev/null || true
                    fi
                done
                ;;
        esac
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
            echo "linux"
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
    if [ "${MACOS_MAJOR:-0}" -eq 10 ] 2>/dev/null && [ "${MACOS_MINOR:-0}" -lt 14 ] 2>/dev/null; then
        die "macOS $MACOS_VER is too old. Minimum supported version is 10.14 (Mojave).
  Homebrew requires macOS 10.14 or later. Consider installing Linux on this machine instead."
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
# The `< /dev/null` is critical: without it, the subshell shares stdin with
# the parent and could swallow script bytes when stdin is a pipe (curl|bash).
(while true; do sudo -n true 2>/dev/null; sleep 50; done) < /dev/null &
SUDO_KEEPALIVE_PID=$!
cleanup_sudo() { kill $SUDO_KEEPALIVE_PID 2>/dev/null; }
trap cleanup_sudo EXIT INT TERM

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
            if command -v apt-get &>/dev/null; then
                sudo apt-get update -qq 2>/dev/null || warn "apt-get update had warnings"
                sudo apt-get install -y -qq git || die "Failed to install git"
            elif command -v dnf &>/dev/null; then
                sudo dnf install -y git || die "Failed to install git"
            elif command -v yum &>/dev/null; then
                sudo yum install -y git || die "Failed to install git"
            elif command -v pacman &>/dev/null; then
                sudo pacman -Sy --noconfirm git || die "Failed to install git"
            elif command -v zypper &>/dev/null; then
                sudo zypper install -y git || die "Failed to install git"
            elif command -v apk &>/dev/null; then
                sudo apk add git || die "Failed to install git"
            else
                die "No supported package manager found. Install git manually and re-run."
            fi
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

add_git_safe_directory() {
    # Idempotent: only add if not already present.
    local path="$1"
    if ! git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$path"; then
        git config --global --add safe.directory "$path" 2>/dev/null || true
    fi
}

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bot.py" ]; then
    REPO_DIR="$SCRIPT_DIR"
    info "Running from cloned repository: $REPO_DIR"
    add_git_safe_directory "$REPO_DIR"
else
    REPO_DIR="$HOME/MyOldMachine"
    if [ -d "$REPO_DIR/.git" ]; then
        # Mark safe BEFORE touching git — handles the "dubious ownership"
        # error that older sudo-tainted installs trigger on git pull.
        add_git_safe_directory "$REPO_DIR"
        info "Existing installation found. Pulling latest..."
        cd "$REPO_DIR" && git pull || warn "git pull failed — continuing with existing code"
    else
        info "Cloning MyOldMachine repository..."
        if [ -d "$REPO_DIR" ]; then
            rm -rf "$REPO_DIR"
        fi
        git clone https://github.com/nickathens/MyOldMachine.git "$REPO_DIR" || die "Failed to clone repository"
        add_git_safe_directory "$REPO_DIR"
    fi
    cd "$REPO_DIR" || die "Failed to enter repository directory"
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
            if command -v apt-get &>/dev/null; then
                sudo apt-get update -qq 2>/dev/null || warn "apt-get update had warnings"
                sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip 2>/dev/null || \
                sudo apt-get install -y -qq python3 python3-venv python3-pip || \
                    die "Failed to install Python"
            elif command -v dnf &>/dev/null; then
                sudo dnf install -y python3 python3-pip python3-venv 2>/dev/null || \
                sudo dnf install -y python3 python3-pip || \
                    die "Failed to install Python"
            elif command -v yum &>/dev/null; then
                sudo yum install -y python3 python3-pip || \
                    die "Failed to install Python"
            elif command -v pacman &>/dev/null; then
                sudo pacman -Sy --noconfirm python python-pip || \
                    die "Failed to install Python"
            elif command -v zypper &>/dev/null; then
                sudo zypper install -y python3 python3-pip python3-virtualenv || \
                    die "Failed to install Python"
            elif command -v apk &>/dev/null; then
                sudo apk add python3 py3-pip py3-virtualenv || \
                    die "Failed to install Python"
            else
                die "No supported package manager found. Install Python 3.10+ manually and re-run."
            fi
        else
            ensure_homebrew

            info "Installing Python 3.12 via Homebrew..."
            info "(On older macOS, this compiles from source — may take 15-30 minutes)"

            # Stream output so user sees progress during long compilations.
            # `< /dev/null` is critical so brew never reads from script stdin.
            brew install python@3.12 < /dev/null 2>&1 | while IFS= read -r line; do
                echo "    $line"
            done
            # Note: PIPESTATUS doesn't work in all shells, so we verify below

            # Force-link python into PATH
            brew link --overwrite python@3.12 < /dev/null 2>/dev/null || true

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

    ok "Python: $("$PYTHON" --version)"
    checkpoint_set "python"
else
    PYTHON=$(find_python) || die "Python was previously installed but can't be found. Remove $CHECKPOINT_FILE and re-run."
    ok "Python: $("$PYTHON" --version) (cached)"
fi

# ─────────────────────────────────────────────────────────
# Step 5: Virtual environment and dependencies
# ─────────────────────────────────────────────────────────

if ! checkpoint_done "venv"; then
    if [ ! -d "$REPO_DIR/.venv" ]; then
        info "Creating virtual environment..."
        "$PYTHON" -m venv "$REPO_DIR/.venv" < /dev/null || die "Failed to create virtual environment.
  Ubuntu/Debian: sudo apt install python3-venv
  Fedora/RHEL:   sudo dnf install python3-virtualenv
  Arch:          sudo pacman -S python-virtualenv
  Alpine:        sudo apk add py3-virtualenv"
    fi

    source "$REPO_DIR/.venv/bin/activate" || die "Failed to activate virtual environment"
    ok "Virtual environment active"

    info "Installing Python dependencies..."
    pip install --quiet --upgrade pip < /dev/null 2>/dev/null || warn "pip upgrade had warnings"
    pip install --quiet -r "$REPO_DIR/requirements.txt" < /dev/null || die "Failed to install Python dependencies"
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

"$REPO_DIR/.venv/bin/python" "$REPO_DIR/install/wizard.py" \
    --repo-dir "$REPO_DIR" --os "$OS" < "$TTY_INPUT"
exit $?
