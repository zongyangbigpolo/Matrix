"""A 股交易日保护工具。

不引入额外交易日历依赖；默认过滤周末，并允许通过 ``CN_MARKET_HOLIDAYS``
配置逗号分隔的休市日（YYYY-MM-DD）。
"""

from datetime import date

from matrix_etf.core.config import Settings


def parse_holidays(value: str) -> set[str]:
    """解析逗号分隔的 YYYY-MM-DD 休市日列表。"""
    return {item.strip() for item in value.split(",") if item.strip()}


def get_non_trading_day_reason(day: date, settings: Settings) -> str | None:
    """返回非交易日原因；交易日返回 None。"""
    if day.weekday() >= 5:
        return "周末"
    if day.isoformat() in parse_holidays(settings.cn_market_holidays):
        return "配置的 A 股休市日"
    return None


def is_cn_trading_day(day: date, settings: Settings) -> bool:
    """判断给定日期是否可作为 A 股日常同步交易日。"""
    return get_non_trading_day_reason(day, settings) is None
