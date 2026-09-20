"""Mainland-listed US-equity ETF closing-price list, independent of strategies."""

import argparse
import json
import os
import signal
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from tickflow import TickFlowError

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
load_dotenv(PROJECT_ROOT / ".env")

from matrix_etf.core.config import Settings, get_settings  # noqa: E402
from matrix_etf.core.logger import get_logger  # noqa: E402
from matrix_etf.core.trading_calendar import get_non_trading_day_reason  # noqa: E402
from matrix_etf.notify.feishu import FeishuNotifier  # noqa: E402
from matrix_etf.us_etf.card import build_cards  # noqa: E402
from matrix_etf.us_etf.listing import expected_close_day, select_quotes  # noqa: E402
from matrix_etf.us_etf.source import SourceError, USEtfSource  # noqa: E402
from matrix_etf.us_etf.subscription import fetch_quotas  # noqa: E402

logger = get_logger(__name__)


def shanghai_now():
    return datetime.now(ZoneInfo("Asia/Shanghai"))


@contextmanager
def time_budget():
    def expired(_signum, _frame):
        raise TimeoutError("境内美股 ETF 本次运行超过12分钟")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(12 * 60)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="境内场内美股 ETF 收盘行情（非推荐策略）")
    parser.add_argument("--dry-run", action="store_true", help="查询并更新共享行情库、打印卡片，不推送")
    parser.add_argument("--force", action="store_true", help="跳过北京时间非交易日保护")
    args = parser.parse_args(argv)
    notifier = None
    source = None
    try:
        with time_budget():
            settings = Settings(feishu_webhook_url="") if args.dry_run else get_settings()
            now = shanghai_now()
            reason = get_non_trading_day_reason(now.date(), settings)
            if settings.skip_non_trading_day and reason and not args.force:
                logger.info(f"境内美股 ETF 跳过：{reason}（北京时间 {now:%Y-%m-%d}）")
                return 0
            if not args.dry_run:
                notifier = FeishuNotifier(settings)
            expected = expected_close_day(now, settings)
            source = USEtfSource(settings)
            quotes = source.fetch(expected)
            selected = select_quotes(quotes)
            quotas = fetch_quotas([q.product.symbol for q in selected], expected)
            selected = [
                replace(q, subscription=quotas[q.product.symbol]) for q in selected
            ]
            cards = build_cards(selected, candidate_count=len(quotes), now=now, expected=expected)
            logger.info(
                f"境内美股 ETF 收盘清单：候选{len(quotes)}只，展示{len(selected)}只，"
                f"滞后{sum(q.stale for q in selected)}只，"
                f"暂无行情{sum(q.close is None for q in selected)}只"
            )
            if args.dry_run:
                print(json.dumps(cards[0] if len(cards) == 1 else cards,
                                 ensure_ascii=False, indent=2))
            else:
                for index, card in enumerate(cards, 1):
                    if not notifier.send_card(card, webhook_key="us_etf"):
                        logger.error(f"境内美股 ETF 推送失败：第{index}/{len(cards)}条")
                        return 1
            return 0
    except (SourceError, TickFlowError, OSError, ValueError, sqlite3.Error) as exc:
        # SDK/config errors may include credentials; report only their type.
        detail = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
        logger.error(f"境内美股 ETF 清单失败（{detail}）；未以旧缓存替代刷新")
        if notifier is not None:
            notifier.send_alert(
                "境内美股 ETF 本次查询失败，未发送收盘清单，未以旧缓存替代刷新。"
                "请检查数据源、分类元数据及服务状态。",
                category="境内美股 ETF", webhook_key="us_etf",
            )
        return 1
    finally:
        if source is not None:
            source.close()


if __name__ == "__main__":
    raise SystemExit(main())
