#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PROJECT_PYTHON="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PROJECT_PYTHON" ]; then
  PROJECT_PYTHON=$(command -v python3)
fi

exec "$PROJECT_PYTHON" "$PROJECT_DIR/scripts/build_app.py"
