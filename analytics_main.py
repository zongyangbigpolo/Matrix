"""Matrix 策略绩效分析入口程序（analytics 模块）。

与三条选股流水线（main.py / stock_main.py / us_main.py）**完全独立**：使用独立的
``data/matrix_analytics.db``，只读三条线的行情库来计算兑现收益，不写它们的数据。

运行模式：
  python analytics_main.py --evaluate        # 前向：同步基准 + 兑现收益 + 评分卡（每日）
  python analytics_main.py --sync-benchmark  # 仅更新基准行情缓存
  python analytics_main.py --report          # 打印各策略最新评分卡（人工查看）
  python analytics_main.py --replay --days 20  # 历史回放：无前视偏差重建过去N个交易日
                                               # 的选股信号并即时评估各策略收益

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
from matrix_etf.analytics.replay import (  # noqa: E402
    filter_strategies,
    format_summary,
    replay_market,
    replay_summary,
)
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


def _build_market_strategies(engines: dict[str, object], settings) -> dict[str, list]:
    """按市场装配策略实例，复用三条选股线的构建函数（单一真源，避免漂移）。"""
    # 惰性导入：仅历史回放需要，避免常规评估路径引入三个 main 脚本的导入开销。
    from main import build_strategies as build_etf
    from stock_main import _build_strategies as build_cn
    from us_main import _build_strategies as build_us

    return {
        "ETF": build_etf(engines["ETF"], settings),
        "CN": build_cn(engines["CN"], settings),
        "US": build_us(engines["US"], settings),
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


def _run_replay(
    settings,
    logger,
    days: int,
    market: str | None = None,
    strategy: str | None = None,
) -> None:
    """历史回放：无前视偏差重建过去 ``days`` 个交易日的选股信号，随后评估并汇总收益。

    Args:
        market: 只回放该市场（'ETF'/'CN'/'US'）；``None`` 则三个市场全跑。
        strategy: 只回放类名包含该子串的策略（不区分大小写）；``None`` 则全部策略。
    """
    analytics = AnalyticsEngine(settings)
    signal_store = SignalStore(analytics)
    benchmark_store = BenchmarkStore(analytics, settings)

    logger.info(f"装配各市场行情引擎与策略（回放窗口 {days} 个交易日）...")
    engines = _build_market_engines(settings)
    strategies_by_market = _build_market_strategies(engines, settings)

    if market:
        market = market.upper()
        if market not in engines:
            logger.error(f"未知市场 {market}，可选：{', '.join(engines)}。")
            return
        engines = {market: engines[market]}

    logger.info("开始逐市场历史回放（无前视偏差重建信号）...")
    total = 0
    for mkt, engine in engines.items():
        picks_strategies = filter_strategies(strategies_by_market[mkt], strategy)
        if not picks_strategies:
            logger.warning(f"[{mkt}] 无策略匹配 --strategy={strategy!r}，跳过。")
            continue
        logger.info(
            f"[{mkt}] 本次回放 {len(picks_strategies)} 个策略："
            f"{', '.join(type(s).__name__ for s in picks_strategies)}"
        )
        total += replay_market(engine, picks_strategies, mkt, signal_store, days)
    logger.info(f"历史回放完成，累计新增信号 {total} 条。")

    logger.info("同步基准行情缓存...")
    _sync_benchmarks(benchmark_store, settings, logger)

    logger.info("推进前向兑现收益评估（用真实库读回放日之后的价格）...")
    evaluator = ForwardEvaluator(analytics, signal_store, benchmark_store, engines, settings)
    evaluator.evaluate(date.today().isoformat())

    logger.info("构建策略评分卡...")
    ScorecardBuilder(analytics, settings).build_all(date.today().isoformat())

    print("\n===== 历史回放：各策略兑现收益汇总（逐笔等权，不设最小样本门槛）=====")
    print(format_summary(replay_summary(analytics, market=market, strategy=strategy)))
    print(
        "\n提示：持有期未到期的信号记为 open、暂不计入上表；"
        "综合评分需样本≥"
        f"{settings.analytics_min_samples} 才给分，短窗口回放多为「样本不足」。\n"
    )


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
    parser.add_argument(
        "--replay",
        action="store_true",
        help="历史回放：无前视偏差重建过去 N 个交易日的选股信号并即时评估各策略收益",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=20,
        help="--replay 回放的交易日数（默认 20）",
    )
    parser.add_argument(
        "--market",
        choices=["ETF", "CN", "US", "etf", "cn", "us"],
        default=None,
        help="--replay 只回放指定市场（ETF/CN/US），缺省则三个市场全跑",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="--replay 只回放类名包含该子串的策略（不区分大小写，如 rps、breakout）",
    )
    args = parser.parse_args()

    try:
        settings = get_settings()
        logger = get_logger(__name__)
        logger.info("Matrix 绩效分析启动")

        if args.replay:
            _run_replay(
                settings,
                logger,
                max(1, args.days),
                market=args.market,
                strategy=args.strategy,
            )
        elif args.sync_benchmark:
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
