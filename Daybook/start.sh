#!/usr/bin/env bash
# Start Daybook: creates a virtual environment on first run, keeps
# dependencies up to date, then runs the server. Extra args go to run.py,
# e.g.  ./start.sh --port 8080   or   ./start.sh --localhost-only
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.11+ is required. Install it from python.org or with: brew install python" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating a virtual environment in .venv…"
  "$PY" -m venv .venv
fi

if [ ! -f .venv/.deps-ok ] || [ requirements.txt -nt .venv/.deps-ok ]; then
  echo "Installing dependencies (first run takes a minute)…"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  touch .venv/.deps-ok
fi

exec .venv/bin/python run.py "$@"
