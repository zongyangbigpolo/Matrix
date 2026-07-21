#!/usr/bin/env bash
set -euo pipefail

# 绩效分析线独立运行脚本，与三条选股线（run_matrix.sh / run_stock.sh /
# run_us.sh）解耦（独立锁文件，互不阻塞）。默认执行 --evaluate：
# 同步基准 + 前向兑现收益 + 评分卡。
PROJECT_DIR="${MATRIX_ANALYTICS_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="${MATRIX_ANALYTICS_LOCK_FILE:-${PROJECT_DIR}/.matrix_analytics.lock}"

cd "$PROJECT_DIR"
mkdir -p data reports logs

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "Matrix analytics pipeline is already running; skip this invocation."
    exit 0
  fi
fi

# 默认参数 --evaluate；调用方可覆盖（如 --report / --sync-benchmark）。
ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=("--evaluate")
fi

if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  exec "${PROJECT_DIR}/.venv/bin/python" analytics_main.py "${ARGS[@]}"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run python analytics_main.py "${ARGS[@]}"
fi

exec "${PYTHON:-python3}" analytics_main.py "${ARGS[@]}"
