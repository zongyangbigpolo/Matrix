#!/usr/bin/env bash
set -euo pipefail

# 股票线独立运行脚本，与 ETF 线 run_matrix.sh 解耦（独立锁文件，互不阻塞）。
PROJECT_DIR="${MATRIX_STOCK_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${MATRIX_STOCK_LOCK_FILE:-${PROJECT_DIR}/.matrix_stock.lock}"

cd "$PROJECT_DIR"
mkdir -p data reports logs

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "Matrix stock pipeline is already running; skip this invocation."
    exit 0
  fi
fi

if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  exec "${PROJECT_DIR}/.venv/bin/python" stock_main.py "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run python stock_main.py "$@"
fi

exec "${PYTHON:-python3}" stock_main.py "$@"
