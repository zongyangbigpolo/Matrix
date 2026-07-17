"""Matrix ETF 推荐系统主程序入口。

运行模式：
  python main.py                    # 日常模式：增量同步 + 刷新指标 + 跑策略 + 飞书推送
  python main.py --backfill         # 回填模式：拉取 CN_ETF 全量历史日 K（首次使用）
  python main.py --sync-universe    # 仅同步 ETF 标的池与基础信息
  python main.py --refresh-metrics  # 仅重算 etf_metrics 指标
  python main.py --etf-report       # 生成四梯队 Markdown 报告
  python main.py --symbols 510300.SH,159915.SZ   # 仅处理指定 ETF
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
from matrix_etf.data.engine import DataEngine  # noqa: E402
from matrix_etf.data.sync_runner import sync_until_stable  # noqa: E402
from matrix_etf.notify.feishu import FeishuNotifier  # noqa: E402
from matrix_etf.strategy.base import BaseStrategy  # noqa: E402
from matrix_etf.strategy.etf.breakout_volume import BreakoutVolumeStrategy  # noqa: E402
from matrix_etf.strategy.etf.etf_pool import EtfPoolReport  # noqa: E402
from matrix_etf.strategy.etf.mean_reversion import MeanReversionStrategy  # noqa: E402
from matrix_etf.strategy.etf.mega7_rotation import (  # noqa: E402
    LowVolTrendRotationStrategy,
    RiskAdjustedMomentumStrategy,
    VolumeConfirmedMomentumStrategy,
)
from matrix_etf.strategy.etf.rps_momentum import RpsMomentumStrategy  # noqa: E402
from matrix_etf.strategy.etf.trend_ma import TrendMaStrategy  # noqa: E402


def _parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    symbols = [item.strip() for item in value.split(",") if item.strip()]
    return symbols or None


def _run_backfill(engine: DataEngine, requested: list[str] | None, logger) -> None:
    logger.info("进入回填模式...")
    if requested:
        symbols = requested
        engine.sync_basic_info(symbols)
    else:
        symbols = engine.sync_universe_and_get_symbols()
    engine.backfill(symbols)
    engine.refresh_metrics(symbols)
    logger.info("Matrix 回填模式运行完成（ETF 名称已随日 K 自动入库）")


def _run_explicit_command(args, engine: DataEngine, requested: list[str] | None, logger) -> bool:
    commands = [
        (args.backfill, lambda: _run_backfill(engine, requested, logger)),
        (args.sync_universe, lambda: engine.sync_universe()),
        (
            args.refresh_metrics,
            lambda: engine.refresh_metrics(requested or engine.get_local_symbols()),
        ),
        (
            args.etf_report,
            lambda: logger.info(
                f"报告路径：{EtfPoolReport(engine).write_report(limit=args.report_limit)}"
            ),
        ),
    ]

    for enabled, runner in commands:
        if enabled:
            runner()
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix ETF 推荐系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：拉取 CN_ETF 全量历史日 K（首次使用）",
    )
    parser.add_argument(
        "--sync-universe",
        action="store_true",
        help="仅同步 ETF 标的池与基础信息（etf_basic）",
    )
    parser.add_argument(
        "--refresh-metrics",
        action="store_true",
        help="仅重算 etf_metrics 指标",
    )
    parser.add_argument(
        "--etf-report",
        action="store_true",
        help="生成四梯队 ETF Markdown 报告",
    )
    parser.add_argument(
        "--symbols",
        help="仅处理指定 ETF，多个代码用逗号分隔，例如 510300.SH,159915.SZ",
    )
    parser.add_argument(
        "--report-limit",
        type=int,
        default=30,
        help="四梯队报告每个梯队最多展示的 ETF 数",
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
        logger.info("Matrix ETF 启动")

        engine = DataEngine(settings)
        requested_symbols = _parse_symbols(args.symbols)

        if _run_explicit_command(args, engine, requested_symbols, logger):
            return

        # ── 日常模式：增量同步 + 刷新指标 + 跑策略 + 推送 ──
        if settings.skip_non_trading_day and not args.force:
            reason = get_non_trading_day_reason(date.today(), settings)
            if reason:
                logger.info(f"今日为非交易日（{reason}），跳过日常同步；如需运行请加 --force")
                return

        if requested_symbols:
            symbols = requested_symbols
            try:
                engine.sync_basic_info(requested_symbols)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"基础信息同步失败，将使用本地已有数据继续：{exc}")
        else:
            try:
                symbols = engine.sync_universe_and_get_symbols()
            except Exception as exc:  # noqa: BLE001
                symbols = engine.get_local_symbols()
                logger.warning(
                    f"标的池/基础信息同步失败，改用本地已有 {len(symbols)} 只 ETF 继续：{exc}"
                )

        notifier = FeishuNotifier(settings, engine=engine)

        local_symbols = set(engine.get_local_symbols())
        has_local_data = any(symbol in local_symbols for symbol in symbols)
        if not has_local_data:
            logger.info("本地暂无 ETF 数据，自动执行首次回填...")
            engine.backfill(symbols)
            engine.refresh_metrics(symbols)
        else:
            logger.info("开始持续同步最新日 K，直到拉全或达标...")
            outcome = sync_until_stable(
                engine,
                symbols,
                expected_latest_date=date.today().isoformat(),
                max_seconds=settings.sync_persist_max_seconds,
                round_interval=settings.sync_persist_round_interval,
                target_coverage=settings.sync_persist_target_coverage,
                min_coverage=settings.sync_persist_min_coverage,
                log=logger,
            )
            if not outcome.success:
                notifier.send_alert(
                    message=(
                        f"⚠️ {date.today():%Y-%m-%d} ETF 数据经持续重试后仍未拉全："
                        f"{outcome.describe()}。本次跳过策略推送，可能为数据源限流/网络"
                        "异常，请排查后手动重跑 `python main.py --force`。"
                    ),
                    category="ETF",
                )
                logger.warning("数据持续拉取失败，已发送告警卡片并跳过 ETF 策略推送")
                return
            engine.refresh_metrics(symbols)

        strategies: list[BaseStrategy] = [
            RpsMomentumStrategy(engine=engine, settings=settings),
            TrendMaStrategy(engine=engine, settings=settings),
            BreakoutVolumeStrategy(engine=engine, settings=settings),
            MeanReversionStrategy(engine=engine, settings=settings),
            RiskAdjustedMomentumStrategy(engine=engine, settings=settings),
            VolumeConfirmedMomentumStrategy(engine=engine, settings=settings),
            LowVolTrendRotationStrategy(engine=engine, settings=settings),
        ]

        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只 ETF")

            if selected:
                notifier.send(
                    symbols=selected,
                    strategy_name=strategy_name,
                    webhook_key=strategy.webhook_key,
                )
            else:
                logger.info(f"{strategy_name} 无选股结果，跳过推送")

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Matrix ETF 运行完成")


if __name__ == "__main__":
    main()
