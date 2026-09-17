"""Live-only mainland US-index fund subscription monitoring (free public data)."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
load_dotenv(PROJECT_ROOT / ".env")

from matrix_etf.core.config import Settings, get_settings  # noqa: E402
from matrix_etf.core.logger import get_logger  # noqa: E402
from matrix_etf.core.trading_calendar import get_non_trading_day_reason  # noqa: E402
from matrix_etf.funds.card import build_card, build_discovery_card  # noqa: E402
from matrix_etf.funds.catalog import load_catalog, parse_candidates  # noqa: E402
from matrix_etf.funds.monitor import select_funds  # noqa: E402
from matrix_etf.funds.source import (  # noqa: E402
    FundSourceError,
    fetch_catalog_text,
    fetch_quotes,
)
from matrix_etf.notify.feishu import FeishuNotifier  # noqa: E402

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="境内美股基金申购监控（天天基金公开口径）")
    parser.add_argument("--dry-run", action="store_true", help="在线查询并打印卡片，不推送、不落库")
    parser.add_argument("--discover", action="store_true", help="发现目录外候选，不自动纳入可买清单")
    parser.add_argument("--catalog", help="覆盖基金代码目录路径")
    args = parser.parse_args(argv)
    notifier = None
    try:
        settings = Settings(feishu_webhook_url="") if args.dry_run else get_settings()
        if not args.dry_run:
            notifier = FeishuNotifier(settings)
        catalog = load_catalog(args.catalog or settings.fund_catalog_path)
        codes = {code for group in catalog for code in group}
        kwargs = {
            "timeout": settings.fund_source_timeout_seconds,
            "attempts": settings.fund_source_attempts,
        }
        if args.discover:
            candidates = parse_candidates(fetch_catalog_text(**kwargs), codes)
            if not candidates:
                logger.info("基金目录检查完成：没有新的待核验候选")
                return 0
            fetched_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            payload = build_discovery_card(candidates, fetched_at)
        else:
            quotes = fetch_quotes(codes, **kwargs)
            fetched_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            selected = select_funds(catalog, quotes, settings.recommendation_limit)
            payload = build_card(
                selected, fetched_at,
                get_non_trading_day_reason(fetched_at.date(), settings),
            )
            logger.info(
                f"基金查询完成：目录{selected.catalog_count}类份额，"
                f"符合条件{selected.eligible_groups}个产品，展示{len(selected.groups)}个，"
                f"待核实{len(selected.unknown)}类份额"
            )
        # Feishu custom bots limit request bodies to 20 KiB.
        if len(json.dumps(payload).encode("utf-8")) > 20 * 1024:
            raise ValueError("Fund card exceeds Feishu's 20 KiB body limit")
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif not notifier.send_card(payload, webhook_key="fund_us"):
            logger.error("基金清单飞书推送失败")
            return 1
        return 0
    except (FundSourceError, OSError, ValueError) as exc:
        logger.error(f"基金监控失败：{exc}")
        if notifier is not None:
            notifier.send_alert(
                message="美股基金本次查询失败，未发送可申购清单，也未使用历史额度。"
                "请检查服务日志、目录配置及公开数据源状态。",
                category="美股基金",
                webhook_key="fund_us",
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
