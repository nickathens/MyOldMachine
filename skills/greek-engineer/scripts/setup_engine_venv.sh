#!/usr/bin/env bash
# Στήνει το απομονωμένο engineering venv για τη διασταύρωση PyNiteFEA.
#
# Γιατί ξεχωριστό venv: το PyNiteFEA απαιτεί numpy >= 2.4, ενώ το numba της
# βασικής εγκατάστασης (0.63.1) θέλει numpy < 2.4 (το whisper δεν καρφώνει, αλλά
# σπάει μέσω numba). Στο ίδιο venv η εγκατάσταση
# του PyNiteFEA ανεβάζει σιωπηλά το numpy και σπάει τη φωνή. Εδώ ζει χωριστά και
# το plaisio.py γεφυρώνει σε αυτό με υποδιεργασία (βλ. _engine_python).
#
# Χρήση:      bash setup_engine_venv.sh
# Θέση venv:  $GREEK_ENGINEER_VENV αν οριστεί, αλλιώς ~/.venvs/engineering
# Βάση:       $PYTHON αν οριστεί, αλλιώς python3
# Ιδεμποτ:    αν το venv υπάρχει, απλώς επιβεβαιώνει/ενημερώνει το PyNiteFEA.
set -eu

VENV_DIR="${GREEK_ENGINEER_VENV:-$HOME/.venvs/engineering}"
PY="${PYTHON:-python3}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Δημιουργία engineering venv: $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
else
  echo "Υπάρχει ήδη: $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
echo "Εγκατάσταση PyNiteFEA (απομονωμένα, δεν αγγίζει τη βασική εγκατάσταση)..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet PyNiteFEA

"$VENV_PY" - <<'PYCHECK'
import numpy
from Pynite import FEModel3D  # noqa: F401
print("OK: engineering venv ready, numpy " + numpy.__version__ + ", PyNiteFEA imports.")
PYCHECK

echo "Έτοιμο. Το 'plaisio.py ... --pynite' θα γεφυρώνει αυτόματα εδώ."
