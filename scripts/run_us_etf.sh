#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${MATRIX_ETF_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${MATRIX_ETF_LOCK_FILE:-${PROJECT_DIR}/.matrix_etf.lock}"

cd "$PROJECT_DIR"
mkdir -p data reports logs

if ! command -v flock >/dev/null 2>&1; then
  echo "Matrix US ETF requires flock; refusing an unlocked run." >&2
  exit 1
fi
exec 9>"$LOCK_FILE"
if flock -n 9; then
  :
else
  status=$?
  if [[ "$status" -eq 1 ]]; then
    echo "Matrix ETF data is already in use; skip this invocation."
    exit 0
  fi
  echo "Matrix ETF lock failed; refusing an unlocked run." >&2
  exit "$status"
fi

if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  exec "${PROJECT_DIR}/.venv/bin/python" us_etf_main.py "$@"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run python us_etf_main.py "$@"
fi
exec "${PYTHON:-python3}" us_etf_main.py "$@"
