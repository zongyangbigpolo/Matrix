#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${MATRIX_ETF_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${MATRIX_ETF_LOCK_FILE:-${PROJECT_DIR}/.matrix_etf.lock}"

cd "$PROJECT_DIR"
mkdir -p data reports logs

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "Matrix ETF is already running; skip this invocation."
    exit 0
  fi
fi

if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  exec "${PROJECT_DIR}/.venv/bin/python" main.py "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run python main.py "$@"
fi

exec "${PYTHON:-python3}" main.py "$@"
