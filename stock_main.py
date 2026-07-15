"""Matrix 股票（A 股）推荐系统入口程序。

与 ETF 的 main.py 完全独立：使用独立的股票数据库、独立的标的池（CN_Equity_A）
和独立的股票策略集，互不影响。

运行模式：
  python stock_main.py                    # 日常模式：增量同步 + 跑股票策略 + 飞书推送
  python stock_main.py --backfill         # 回填模式：拉取 CN_Equity_A 全量历史日 K（首次使用）
  python stock_main.py --sync-universe    # 仅同步股票标的池与基础信息（stock_basic）
  python stock_main.py --symbols 600519.SH,000001.SZ   # 仅处理指定股票
  python stock_main.py --force            # 非交易日也强制运行
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

from matrix_etf.core.config import get_settings  # noqa: E402
from matrix_etf.core.logger import get_logger  # noqa: E402
from matrix_etf.core.trading_calendar import get_non_trading_day_reason  # noqa: E402
from matrix_etf.data.stock_engine import StockDataEngine  # noqa: E402
from matrix_etf.notify.feishu import FeishuNotifier  # noqa: E402
from matrix_etf.strategy.base import BaseStrategy  # noqa: E402
from matrix_etf.strategy.stock.high_tight_flag import HighTightFlagStrategy  # noqa: E402
from matrix_etf.strategy.stock.limit_up_shakeout import LimitUpShakeoutStrategy  # noqa: E402
from matrix_etf.strategy.stock.ma_volume import MaVolumeStrategy  # noqa: E402
from matrix_etf.strategy.stock.rps_breakout import RpsBreakoutStrategy  # noqa: E402
from matrix_etf.strategy.stock.turtle_trade import TurtleTradeStrategy  # noqa: E402
from matrix_etf.strategy.stock.uptrend_limit_down import UptrendLimitDownStrategy  # noqa: E402


def _parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    symbols = [item.strip() for item in value.split(",") if item.strip()]
    return symbols or None


def _build_strategies(engine: StockDataEngine, settings) -> list[BaseStrategy]:
    return [
        MaVolumeStrategy(engine=engine, settings=settings),
        TurtleTradeStrategy(engine=engine, settings=settings),
        HighTightFlagStrategy(engine=engine, settings=settings),
        LimitUpShakeoutStrategy(engine=engine, settings=settings),
        UptrendLimitDownStrategy(engine=engine, settings=settings),
        RpsBreakoutStrategy(engine=engine, settings=settings),
    ]


def _run_backfill(engine: StockDataEngine, requested: list[str] | None, logger) -> None:
    logger.info("进入股票回填模式...")
    if requested:
        symbols = requested
        engine.sync_basic_info(symbols)
    else:
        symbols = engine.sync_universe_and_get_symbols()
    engine.backfill(symbols)
    logger.info("Matrix 股票回填模式运行完成（股票名称已随日 K 自动入库）")


def _run_explicit_command(
    args, engine: StockDataEngine, requested: list[str] | None, logger
) -> bool:
    commands = [
        (args.backfill, lambda: _run_backfill(engine, requested, logger)),
        (args.sync_universe, lambda: engine.sync_universe()),
    ]
    for enabled, runner in commands:
        if enabled:
            runner()
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix 股票推荐系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：拉取 CN_Equity_A 全量历史日 K（首次使用）",
    )
    parser.add_argument(
        "--sync-universe",
        action="store_true",
        help="仅同步股票标的池与基础信息（stock_basic）",
    )
    parser.add_argument(
        "--symbols",
        help="仅处理指定股票，多个代码用逗号分隔，例如 600519.SH,000001.SZ",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="日常模式下即使今天是周末或配置的休市日也继续运行",
    )
    args = parser.parse_args()

    try:
        settings = get_settings()

        logger = get_logger(__name__)
        logger.info("Matrix 股票启动")

        engine = StockDataEngine(settings)
        requested_symbols = _parse_symbols(args.symbols)

        if _run_explicit_command(args, engine, requested_symbols, logger):
            return

        # ── 日常模式：增量同步 + 跑股票策略 + 推送 ──
        if settings.skip_non_trading_day and not args.force:
            reason = get_non_trading_day_reason(date.today(), settings)
            if reason:
                logger.info(f"今日为非交易日（{reason}），跳过股票同步；如需运行请加 --force")
                return

        data_stale = False
        stale_reason = ""

        if requested_symbols:
            symbols = requested_symbols
            try:
                engine.sync_basic_info(requested_symbols)
            except Exception as exc:  # noqa: BLE001
                data_stale = True
                stale_reason = str(exc)
                logger.warning(f"基础信息同步失败，将使用本地已有数据继续：{exc}")
        else:
            try:
                symbols = engine.sync_universe_and_get_symbols()
            except Exception as exc:  # noqa: BLE001
                data_stale = True
                stale_reason = str(exc)
                symbols = engine.get_local_symbols()
                logger.warning(
                    f"标的池/基础信息同步失败，改用本地已有 {len(symbols)} 只股票继续：{exc}"
                )

        local_symbols = set(engine.get_local_symbols())
        has_local_data = any(symbol in local_symbols for symbol in symbols)
        if not has_local_data:
            logger.info("本地暂无股票数据，自动执行首次回填...")
            engine.backfill(symbols)
        else:
            logger.info("开始增量同步最新日 K...")
            try:
                engine.sync_daily(symbols)
            except Exception as exc:  # noqa: BLE001
                data_stale = True
                stale_reason = str(exc)
                logger.warning(
                    f"增量同步最新日 K 失败，将基于本地已有历史数据跑策略：{exc}"
                )

        stale_warning = (
            FeishuNotifier.build_stale_warning(stale_reason) if data_stale else None
        )

        strategies = _build_strategies(engine, settings)
        notifier = FeishuNotifier(settings, engine=engine)

        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")

            if selected:
                notifier.send(
                    symbols=selected,
                    strategy_name=strategy_name,
                    webhook_key=strategy.webhook_key,
                    category="Stock",
                    stale_warning=stale_warning,
                )
            else:
                logger.info(f"{strategy_name} 无选股结果，跳过推送")

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("股票主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Matrix 股票运行完成")


if __name__ == "__main__":
    main()
