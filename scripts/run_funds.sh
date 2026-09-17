#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${MATRIX_FUNDS_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR"

if command -v flock >/dev/null 2>&1; then
  exec 9>"${MATRIX_FUNDS_LOCK_FILE:-${PROJECT_DIR}/.matrix_funds.lock}"
  if ! flock -n 9; then
    echo "Matrix fund monitoring is already running; skip this invocation."
    exit 0
  fi
fi

if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  exec "${PROJECT_DIR}/.venv/bin/python" fund_main.py "$@"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run python fund_main.py "$@"
fi
exec "${PYTHON:-python3}" fund_main.py "$@"
