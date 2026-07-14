"""股票策略属性与场景测试。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from matrix_etf.core.config import Settings
from matrix_etf.data.stock_engine import StockDataEngine
from matrix_etf.strategy.stock.high_tight_flag import HighTightFlagStrategy
from matrix_etf.strategy.stock.limit_up_shakeout import LimitUpShakeoutStrategy
from matrix_etf.strategy.stock.ma_volume import MaVolumeStrategy
from matrix_etf.strategy.stock.rps_breakout import RpsBreakoutStrategy
from matrix_etf.strategy.stock.turtle_trade import TurtleTradeStrategy
from matrix_etf.strategy.stock.uptrend_limit_down import UptrendLimitDownStrategy

LOOP_STRATEGIES = [
    MaVolumeStrategy,
    TurtleTradeStrategy,
    HighTightFlagStrategy,
    LimitUpShakeoutStrategy,
    UptrendLimitDownStrategy,
]


def make_settings(tmp_dir: str, **kwargs) -> Settings:
    return Settings(
        stock_db_path=str(Path(tmp_dir) / "stock.db"),
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
        **kwargs,
    )


def _frame(opens, highs, lows, closes, volumes, amounts, symbol="600519.SH") -> pd.DataFrame:
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "symbol": symbol,
        "date": list(dates),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "amount": amounts,
    })


@pytest.mark.parametrize("strategy_cls", LOOP_STRATEGIES)
def test_loop_strategy_returns_list_of_str_on_empty(strategy_cls) -> None:
    """遍历型股票策略在空数据下应返回空 list[str]，不抛异常。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        with patch.object(engine, "get_local_symbols", return_value=["600519.SH"]):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                result = strategy_cls(engine=engine, settings=settings).run()
    assert isinstance(result, list)
    assert all(isinstance(s, str) and s for s in result)


def _run_loop(strategy_cls, df: pd.DataFrame, settings, engine, symbol="600519.SH") -> list[str]:
    with patch.object(engine, "get_local_symbols", return_value=[symbol]):
        with patch.object(engine, "get_ohlcv", return_value=df):
            return strategy_cls(engine=engine, settings=settings).run()


def test_ma_volume_selects_golden_cross_with_volume() -> None:
    """构造均线金叉 + 放量场景，MaVolumeStrategy 应选出该股票。"""
    closes = [round(10 - 0.02 * i, 4) for i in range(24)]
    closes.append(closes[-1] + 2.0)  # 末日放量突破
    n = len(closes)
    volumes = [1e6] * (n - 1) + [5e6]
    df = _frame(
        opens=[c - 0.05 for c in closes],
        highs=[c + 0.1 for c in closes],
        lows=[c - 0.1 for c in closes],
        closes=closes,
        volumes=volumes,
        amounts=[2e8] * n,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        assert _run_loop(MaVolumeStrategy, df, settings, engine) == ["600519.SH"]


def test_turtle_selects_liquid_breakout_yang() -> None:
    """突破 20 日新高 + 成交额过亿 + 实体阳线真涨时，TurtleTradeStrategy 应选出。"""
    closes = [10.0] * 20 + [12.0]
    n = len(closes)
    df = _frame(
        opens=[10.0] * 20 + [10.5],
        highs=[10.05] * 20 + [12.1],
        lows=[9.95] * 20 + [10.4],
        closes=closes,
        volumes=[1e6] * n,
        amounts=[2e8] * n,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        assert _run_loop(TurtleTradeStrategy, df, settings, engine) == ["600519.SH"]


def test_turtle_excludes_illiquid_breakout() -> None:
    """成交额不足时，TurtleTradeStrategy 不应选出。"""
    closes = [10.0] * 20 + [12.0]
    n = len(closes)
    df = _frame(
        opens=[10.0] * 20 + [10.5],
        highs=[10.05] * 20 + [12.1],
        lows=[9.95] * 20 + [10.4],
        closes=closes,
        volumes=[1e6] * n,
        amounts=[1e6] * n,  # 远低于 1 亿门槛
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        assert _run_loop(TurtleTradeStrategy, df, settings, engine) == []


def test_high_tight_flag_selects_consolidation_after_run() -> None:
    """强动量后极度收敛缩量时，HighTightFlagStrategy 应选出。"""
    rise = [round(10 + (10 / 29) * i, 4) for i in range(30)]  # 10 -> 20
    hold = [19.5] * 15
    closes = rise + hold
    n = len(closes)
    volumes = [2e6] * (n - 1) + [0.5e6]  # 末日缩量
    df = _frame(
        opens=[c - 0.05 for c in closes],
        highs=[c + 0.1 for c in closes],
        lows=[c - 0.1 for c in closes],
        closes=closes,
        volumes=volumes,
        amounts=[2e8] * n,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        assert _run_loop(HighTightFlagStrategy, df, settings, engine) == ["600519.SH"]


def test_limit_up_shakeout_selects_bearish_volume_hold() -> None:
    """昨日涨停后今日放量收阴但不破昨收时，LimitUpShakeoutStrategy 应选出。"""
    df = _frame(
        opens=[10.0, 10.2, 11.5],
        highs=[10.1, 11.1, 11.6],
        lows=[9.9, 10.1, 11.05],
        closes=[10.0, 11.0, 11.2],
        volumes=[1e6, 1e6, 3e6],
        amounts=[2e8, 2e8, 2e8],
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        assert _run_loop(LimitUpShakeoutStrategy, df, settings, engine) == ["600519.SH"]


def test_uptrend_limit_down_selects_trend_volume_drop() -> None:
    """上升趋势中放量跌停时，UptrendLimitDownStrategy 应选出。"""
    closes = [round(10 + (10 / 59) * i, 4) for i in range(60)]  # 10 -> 20
    closes.append(round(closes[-1] * 0.90, 4))  # 末日跌停
    n = len(closes)
    volumes = [1e6] * (n - 1) + [5e6]
    df = _frame(
        opens=[c + 0.05 for c in closes],
        highs=[c + 0.1 for c in closes],
        lows=[c - 0.1 for c in closes],
        closes=closes,
        volumes=volumes,
        amounts=[2e8] * n,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = StockDataEngine(settings)
        assert _run_loop(UptrendLimitDownStrategy, df, settings, engine) == ["600519.SH"]


def _insert(engine: StockDataEngine, symbol: str, closes: list[float]) -> None:
    n = len(closes)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    rows = pd.DataFrame({
        "symbol": symbol,
        "date": list(dates),
        "open": closes,
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "volume": [1e6] * n,
        "amount": [2e8] * n,
    })
    engine._upsert_daily(rows)


def test_rps_breakout_selects_strongest_symbol() -> None:
    """横截面动量最强且创阶段新高的股票应被 RpsBreakoutStrategy 选出。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir, stock_rps_period=5, stock_rps_threshold=90.0)
        engine = StockDataEngine(settings)
        # 强势股：持续上行并创新高
        _insert(engine, "600519.SH", [round(10 + i, 2) for i in range(10)])
        # 弱势股：横盘无动量
        _insert(engine, "000001.SZ", [10.0] * 10)

        result = RpsBreakoutStrategy(engine=engine, settings=settings).run()

    assert result == ["600519.SH"]


def test_rps_breakout_empty_db_returns_empty() -> None:
    """空库时 RpsBreakoutStrategy 返回空列表，不抛异常。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir, stock_rps_period=5)
        engine = StockDataEngine(settings)
        assert RpsBreakoutStrategy(engine=engine, settings=settings).run() == []
