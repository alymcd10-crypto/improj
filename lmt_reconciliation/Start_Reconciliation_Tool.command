#!/usr/bin/env bash
# LMT CRM Reconciliation Tool - Mac/Linux launcher.
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Enter to close." _; exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "First run: setting up. This takes a few minutes and needs internet once."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "Starting. Your browser will open at http://localhost:8501 (close this window or press Ctrl+C to stop)."
exec .venv/bin/python launch.py
