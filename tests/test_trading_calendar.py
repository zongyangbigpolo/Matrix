"""交易日保护测试。"""

from datetime import date

from matrix_etf.core.config import Settings
from matrix_etf.core.trading_calendar import get_non_trading_day_reason, is_cn_trading_day


def make_settings(**kwargs) -> Settings:
    return Settings(
        db_path="data/test.db",
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
        **kwargs,
    )


def test_weekend_is_non_trading_day() -> None:
    """周末应被日常模式交易日保护识别为非交易日。"""
    settings = make_settings()
    assert not is_cn_trading_day(date(2026, 7, 18), settings)
    assert get_non_trading_day_reason(date(2026, 7, 18), settings) == "周末"


def test_configured_holiday_is_non_trading_day() -> None:
    """CN_MARKET_HOLIDAYS 中的日期应被识别为 A 股休市日。"""
    settings = make_settings(cn_market_holidays="2026-10-01,2026-10-02")
    assert not is_cn_trading_day(date(2026, 10, 1), settings)
    assert get_non_trading_day_reason(date(2026, 10, 1), settings) == "配置的 A 股休市日"


def test_weekday_not_in_holidays_is_trading_day() -> None:
    """普通工作日且不在休市列表中时应允许日常模式运行。"""
    settings = make_settings(cn_market_holidays="2026-10-01")
    assert is_cn_trading_day(date(2026, 7, 14), settings)
