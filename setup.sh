#!/usr/bin/env bash
# One-command setup for macOS / Linux:  bash setup.sh
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "python3 not found. Install Python 3.10+."; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m copilot_audit setup
python -m copilot_audit doctor
echo
echo "Done. To use the project later:  source .venv/bin/activate"
