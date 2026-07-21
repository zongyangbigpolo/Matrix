"""Matrix 策略绩效分析入口程序（analytics 模块）。

与三条选股流水线（main.py / stock_main.py / us_main.py）**完全独立**：使用独立的
``data/matrix_analytics.db``，只读三条线的行情库来计算兑现收益，不写它们的数据。

运行模式：
  python analytics_main.py --evaluate        # 前向：同步基准 + 兑现收益 + 评分卡（每日）
  python analytics_main.py --sync-benchmark  # 仅更新基准行情缓存
  python analytics_main.py --report          # 打印各策略最新评分卡（人工查看）

历史回测（vectorbt）为离线增强，见 docs/analytics.md 第 12 节，暂不在此入口提供。
"""

import argparse
import os
import socket
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
load_dotenv(PROJECT_ROOT / ".env")

socket.setdefaulttimeout(30.0)

from matrix_etf.analytics.benchmark import BenchmarkStore  # noqa: E402
from matrix_etf.analytics.db import AnalyticsEngine  # noqa: E402
from matrix_etf.analytics.forward import ForwardEvaluator  # noqa: E402
from matrix_etf.analytics.report import format_scorecard_line, get_latest_scorecard  # noqa: E402
from matrix_etf.analytics.scorecard import ScorecardBuilder  # noqa: E402
from matrix_etf.analytics.signals import SignalStore  # noqa: E402
from matrix_etf.core.config import get_settings  # noqa: E402
from matrix_etf.core.logger import get_logger  # noqa: E402
from matrix_etf.data.engine import DataEngine  # noqa: E402
from matrix_etf.data.stock_engine import StockDataEngine  # noqa: E402
from matrix_etf.data.us_stock_engine import UsStockDataEngine  # noqa: E402


def _build_market_engines(settings) -> dict[str, object]:
    """按市场装配行情引擎（只读用于计算兑现收益）。"""
    return {
        "ETF": DataEngine(settings),
        "CN": StockDataEngine(settings),
        "US": UsStockDataEngine(settings),
    }


def _sync_benchmarks(benchmark_store: BenchmarkStore, settings, logger) -> None:
    for benchmark in {settings.benchmark_cn, settings.benchmark_us}:
        benchmark_store.sync(benchmark)


def _run_evaluate(settings, logger) -> None:
    analytics = AnalyticsEngine(settings)
    signal_store = SignalStore(analytics)
    benchmark_store = BenchmarkStore(analytics, settings)

    logger.info("同步基准行情缓存...")
    _sync_benchmarks(benchmark_store, settings, logger)

    logger.info("装配各市场行情引擎...")
    engines = _build_market_engines(settings)

    logger.info("推进前向兑现收益评估...")
    evaluator = ForwardEvaluator(analytics, signal_store, benchmark_store, engines, settings)
    evaluator.evaluate(date.today().isoformat())

    logger.info("构建策略评分卡...")
    ScorecardBuilder(analytics, settings).build_all(date.today().isoformat())


def _run_report(settings, logger) -> None:
    analytics = AnalyticsEngine(settings)
    with analytics.connect() as conn:
        pairs = conn.execute(
            "SELECT DISTINCT market, strategy FROM strategy_signal ORDER BY market, strategy"
        ).fetchall()
    window = settings.get_analytics_windows()[0]
    if not pairs:
        logger.info("暂无任何信号记录，评分卡为空。")
        return
    for market, strategy in pairs:
        card = get_latest_scorecard(analytics, market, strategy, window)
        line = format_scorecard_line(card)
        header = f"[{market}] {strategy}"
        print(header)
        print("  " + (line.replace("\n", "\n  ") if line else "（样本不足或暂无评分）"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix 策略绩效分析")
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="前向评估：同步基准 + 兑现收益 + 评分卡（每日运行）",
    )
    parser.add_argument(
        "--sync-benchmark",
        action="store_true",
        help="仅更新基准行情缓存（沪深300 / 标普500）",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="打印各策略最新评分卡",
    )
    args = parser.parse_args()

    try:
        settings = get_settings()
        logger = get_logger(__name__)
        logger.info("Matrix 绩效分析启动")

        if args.sync_benchmark:
            analytics = AnalyticsEngine(settings)
            _sync_benchmarks(BenchmarkStore(analytics, settings), settings, logger)
        elif args.report:
            _run_report(settings, logger)
        else:
            # 默认与 --evaluate 等价：跑完整前向评估。
            _run_evaluate(settings, logger)

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("绩效分析主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Matrix 绩效分析运行完成")


if __name__ == "__main__":
    main()
