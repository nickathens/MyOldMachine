#!/usr/bin/env bash
# Install RIFE 4.25 for the frame rebuild in cgframes.py.
#
# Only the rebuild needs this. Stall detection, the cadence test, judder and
# every colour tool in the skill work without it.
#
# Practical-RIFE is MIT licensed and the weights are published on HuggingFace,
# so this is clean for paid client work. About 80 MB of weights plus torch.
set -euo pipefail

HOME_DIR="${CG_RIFE_HOME:-$HOME/.models/rife}"
VENV="${CG_VENV:-$HOME/.venvs/colorgrade}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "No colorgrade environment at $VENV. Make it first:"
  echo "  python3 -m venv $VENV"
  echo "  $VENV/bin/pip install numpy pillow scipy scenedetect opencv-python-headless"
  exit 1
fi

echo "==> torch"
"$VENV/bin/python" -c "import torch" 2>/dev/null || "$VENV/bin/pip" install torch

echo "==> Practical-RIFE at $HOME_DIR"
mkdir -p "$HOME_DIR"
if [ ! -d "$HOME_DIR/PracticalRIFE" ]; then
  git clone --depth 1 https://github.com/hzwer/Practical-RIFE.git "$HOME_DIR/PracticalRIFE"
fi

echo "==> weights"
if [ ! -f "$HOME_DIR/train_log/flownet.pkl" ]; then
  "$VENV/bin/pip" install -q huggingface_hub
  "$VENV/bin/python" - "$HOME_DIR" <<'PY'
import sys, shutil, os
from huggingface_hub import snapshot_download
dest = sys.argv[1]
# Practical-RIFE 4.25. The repo carries the IFNet definition alongside the
# weights, and the two must come from the same version: an IFNet from a
# different release loads with missing keys and interpolates nonsense.
path = snapshot_download(repo_id="gpanaretou/practical-rife-interpolation")
src = os.path.join(path, "train_log")
if not os.path.isdir(src):
    src = path
os.makedirs(os.path.join(dest, "train_log"), exist_ok=True)
for name in os.listdir(src):
    if name.endswith((".pkl", ".py")):
        shutil.copy2(os.path.join(src, name), os.path.join(dest, "train_log", name))
print("weights and IFNet copied to", os.path.join(dest, "train_log"))
PY
fi

echo "==> check"
CG_RIFE_HOME="$HOME_DIR" "$VENV/bin/python" "$(dirname "$0")/cgrife.py"
