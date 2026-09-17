"""Matrix 策略绩效分析入口程序（analytics 模块）。

与三条选股流水线（main.py / stock_main.py / us_main.py）**完全独立**：使用独立的
``data/matrix_analytics.db``，只读三条线的行情库来计算兑现收益，不写它们的数据。

运行模式：
  python analytics_main.py --evaluate        # 前向：同步基准 + 兑现收益 + 评分卡（每日）
  python analytics_main.py --sync-benchmark  # 仅更新基准行情缓存
  python analytics_main.py --report          # 打印各策略最新评分卡（人工查看）
  python analytics_main.py --replay --days 20  # --backtest 的兼容别名
  python analytics_main.py --backtest --days 252  # 离线资金约束组合回测（独立存储）
"""

import argparse
import os
import signal
import socket
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
load_dotenv(PROJECT_ROOT / ".env")

socket.setdefaulttimeout(30.0)

from matrix_etf.analytics.backtest import (  # noqa: E402
    BacktestConfig,
    backtest_market,
    format_backtest_report,
)
from matrix_etf.analytics.benchmark import BenchmarkStore  # noqa: E402
from matrix_etf.analytics.db import AnalyticsEngine  # noqa: E402
from matrix_etf.analytics.forward import ForwardEvaluator  # noqa: E402
from matrix_etf.analytics.report import format_scorecard_line, get_latest_scorecard  # noqa: E402
from matrix_etf.analytics.scorecard import ScorecardBuilder  # noqa: E402
from matrix_etf.analytics.signals import SignalStore  # noqa: E402
from matrix_etf.core.config import Settings, get_settings  # noqa: E402
from matrix_etf.core.logger import get_logger  # noqa: E402
from matrix_etf.data.engine import DataEngine  # noqa: E402
from matrix_etf.data.stock_engine import StockDataEngine  # noqa: E402
from matrix_etf.data.us_stock_engine import UsStockDataEngine  # noqa: E402


def _build_market_engines(settings, offline=False) -> dict[str, object]:
    """按市场装配行情引擎（只读用于计算兑现收益）。"""
    engines = {}
    for market, engine_type in (
        ("ETF", DataEngine), ("CN", StockDataEngine), ("US", UsStockDataEngine)
    ):
        if offline:
            class OfflineEngine(engine_type):
                def _init_db(self):
                    # Do not create or migrate source databases during a historical run.
                    pass

                def _client(self):
                    raise RuntimeError("Network clients are disabled during offline backtests")

            engines[market] = OfflineEngine(settings)
        else:
            engines[market] = engine_type(settings)
    return engines


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


@contextmanager
def _backtest_termination():
    """Let service timeouts unwind snapshot/price-store context managers."""
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


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
    """Compatibility alias; historical runs no longer write into live signal tables."""
    logger.warning("--replay 现为 --backtest 别名；历史模拟不再写入前向信号台账。")
    _run_backtest(settings, logger, days, market, strategy)


def _run_backtest(settings, logger, days, market=None, strategy=None, config=None):
    analytics = AnalyticsEngine(settings)
    config = config or BacktestConfig(
        recommendation_limit=getattr(settings, "recommendation_limit", 10)
    )
    engines = _build_market_engines(settings, offline=True)
    strategies_by_market = _build_market_strategies(engines, settings)
    selected_markets = [market.upper()] if market else list(engines)
    if any(mkt not in engines for mkt in selected_markets):
        raise ValueError(f"Unknown market: {market}")
    if strategy and not any(
        strategy.lower() in type(s).__name__.lower()
        for mkt in selected_markets for s in strategies_by_market[mkt]
    ):
        raise ValueError(f"No strategy matches {strategy!r}")
    failed = []
    report_dir = Path(settings.analytics_db_path).parent / "backtests"
    report_dir.mkdir(parents=True, exist_ok=True)
    for mkt in selected_markets:
        logger.info(f"[{mkt}] 离线组合回测：{days} 个交易日，全部策略参与历史排序")
        run = backtest_market(
            engines[mkt], strategies_by_market[mkt], mkt, analytics, days, config
        )
        output = report_dir / f"{mkt}-{run['run_id']}.md"
        output.write_text(run["report"], encoding="utf-8")
        print(format_backtest_report(run["results"], mkt, run["run_id"], strategy))
        print(f"\n完整报告：{output}\n")
        if any(r["status"] in {"error", "no_data"} for r in run["results"].values()):
            failed.append(mkt)
    if failed:
        raise RuntimeError(f"Backtest failed for {', '.join(failed)}; see persisted ERROR reports")


def _run_report(settings, logger) -> None:
    analytics = AnalyticsEngine(settings)
    with analytics.connect() as conn:
        pairs = conn.execute(
            "SELECT DISTINCT market, strategy FROM strategy_signal ORDER BY market, strategy"
        ).fetchall()
        historical = conn.execute(
            """SELECT report FROM backtest_run r
               WHERE run_id = (SELECT run_id FROM backtest_run x WHERE x.market = r.market
                               ORDER BY updated_at DESC, run_id DESC LIMIT 1)
               ORDER BY market"""
        ).fetchall()
    for (report,) in historical:
        print(report)
    window = settings.get_analytics_windows()[0]
    if not pairs:
        logger.info("暂无任何信号记录，评分卡为空。")
        return
    print("\n===== 前向信号跟踪（非实盘账户收益）=====")
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
        help="兼容别名：等价于 --backtest，不写入实盘信号台账",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="离线组合回测：所有策略历史选股、资金约束、成本和期末估值，独立存储",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="--backtest/--replay 的交易日数（默认 60；长样本可设 252，耗时更长）",
    )
    parser.add_argument(
        "--market",
        choices=["ETF", "CN", "US", "etf", "cn", "us"],
        default=None,
        help="--backtest 只回测指定市场（ETF/CN/US），缺省则三个市场全跑",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="--backtest 只展示匹配类名的策略；排序与存储仍包含市场全部策略",
    )
    parser.add_argument("--initial-capital", type=float, default=100_000)
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--commission-bps", type=float, default=5)
    parser.add_argument("--slippage-bps", type=float, default=5)
    args = parser.parse_args()

    try:
        # Offline simulation/reporting does not require a notification webhook.
        settings = (
            Settings(feishu_webhook_url="")
            if args.backtest or args.replay or args.report else get_settings()
        )
        logger = get_logger(__name__)
        logger.info("Matrix 绩效分析启动")

        if args.backtest or args.replay:
            if args.days < 1:
                raise ValueError("--days must be positive")
            if args.replay:
                logger.warning("--replay 已切换为独立的组合回测，不写入前向台账")
            with _backtest_termination():
                _run_backtest(
                    settings,
                    logger,
                    args.days,
                    market=args.market,
                    strategy=args.strategy,
                    config=BacktestConfig(
                        initial_capital=args.initial_capital, max_positions=args.max_positions,
                        commission_bps=args.commission_bps, slippage_bps=args.slippage_bps,
                        recommendation_limit=getattr(settings, "recommendation_limit", 10),
                    ),
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
