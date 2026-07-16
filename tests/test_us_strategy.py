"""美股策略属性与场景测试。

美股免费档数据无成交额（amount 恒为 0），故测试统一以 ``amounts=0`` 构造数据，
验证策略仅依赖 volume / 美元成交额（close×volume）即可正常工作。
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from matrix_etf.core.config import Settings
from matrix_etf.data.us_stock_engine import UsStockDataEngine
from matrix_etf.strategy.us.breakout_volume import UsBreakoutVolumeStrategy
from matrix_etf.strategy.us.ma_volume import UsMaVolumeStrategy
from matrix_etf.strategy.us.rps_momentum import UsRpsMomentumStrategy
from matrix_etf.strategy.us.trend_ma import UsTrendMaStrategy

LOOP_STRATEGIES = [
    UsTrendMaStrategy,
    UsMaVolumeStrategy,
    UsBreakoutVolumeStrategy,
]


def make_settings(tmp_dir: str, **kwargs) -> Settings:
    return Settings(
        us_db_path=str(Path(tmp_dir) / "us.db"),
        feishu_webhook_url="https://example.com/hook",
        **kwargs,
    )


def _frame(opens, highs, lows, closes, volumes, symbol="AAPL.US") -> pd.DataFrame:
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
        "amount": [0.0] * n,  # 美股免费档无成交额
    })


def _run_loop(strategy_cls, df, settings, engine, symbol="AAPL.US") -> list[str]:
    with patch.object(engine, "get_local_symbols", return_value=[symbol]):
        with patch.object(engine, "get_ohlcv", return_value=df):
            return strategy_cls(engine=engine, settings=settings).run()


@pytest.mark.parametrize("strategy_cls", LOOP_STRATEGIES)
def test_loop_strategy_returns_list_of_str_on_empty(strategy_cls) -> None:
    """遍历型美股策略在空数据下应返回空 list[str]，不抛异常。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = UsStockDataEngine(settings)
        with patch.object(engine, "get_local_symbols", return_value=["AAPL.US"]):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                result = strategy_cls(engine=engine, settings=settings).run()
    assert isinstance(result, list)
    assert all(isinstance(s, str) and s for s in result)


def test_ma_volume_selects_golden_cross_with_volume() -> None:
    """均线金叉 + 放量 + 美元成交额达标时，UsMaVolumeStrategy 应选出（amount=0）。"""
    closes = [round(100 - 0.2 * i, 4) for i in range(24)]
    closes.append(closes[-1] + 20.0)  # 末日放量突破
    n = len(closes)
    volumes = [1e6] * (n - 1) + [5e6]
    df = _frame(
        opens=[c - 0.5 for c in closes],
        highs=[c + 1.0 for c in closes],
        lows=[c - 1.0 for c in closes],
        closes=closes,
        volumes=volumes,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = UsStockDataEngine(settings)
        assert _run_loop(UsMaVolumeStrategy, df, settings, engine) == ["AAPL.US"]


def test_ma_volume_excludes_illiquid() -> None:
    """美元成交额不足（close×volume 过低）时，UsMaVolumeStrategy 不应选出。"""
    closes = [round(100 - 0.2 * i, 4) for i in range(24)]
    closes.append(closes[-1] + 20.0)
    n = len(closes)
    volumes = [10] * (n - 1) + [50]  # close~100 × 10 = 1000 美元，远低于 2000 万门槛
    df = _frame(
        opens=[c - 0.5 for c in closes],
        highs=[c + 1.0 for c in closes],
        lows=[c - 1.0 for c in closes],
        closes=closes,
        volumes=volumes,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = UsStockDataEngine(settings)
        assert _run_loop(UsMaVolumeStrategy, df, settings, engine) == []


def test_breakout_volume_selects_liquid_yang_breakout() -> None:
    """突破 60 日新高 + 放量 + 阳线 + 流动性时，UsBreakoutVolumeStrategy 应选出。"""
    base = [100.0] * 60
    closes = base + [120.0]
    volumes = [1e6] * 60 + [4e6]
    df = _frame(
        opens=[100.0] * 60 + [101.0],
        highs=[100.5] * 60 + [121.0],
        lows=[99.5] * 60 + [100.8],
        closes=closes,
        volumes=volumes,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = UsStockDataEngine(settings)
        assert _run_loop(UsBreakoutVolumeStrategy, df, settings, engine) == ["AAPL.US"]


def test_trend_ma_selects_bullish_fresh_cross() -> None:
    """多头排列且当日上穿 MA50 时，UsTrendMaStrategy 应选出（流动性用美元成交额）。"""
    closes = [round(100 + (50 / 202) * i, 4) for i in range(203)]
    closes.append(130.0)  # 倒数第二日回踩至 MA50 下方
    closes.append(152.0)  # 末日回升上穿 MA50
    n = len(closes)
    df = _frame(
        opens=[c - 0.5 for c in closes],
        highs=[c + 1.0 for c in closes],
        lows=[c - 1.0 for c in closes],
        closes=closes,
        volumes=[1e6] * n,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = UsStockDataEngine(settings)
        assert _run_loop(UsTrendMaStrategy, df, settings, engine) == ["AAPL.US"]


def _seed_us_db(engine: UsStockDataEngine, frames: list[pd.DataFrame]) -> None:
    rows = []
    for df in frames:
        for row in df.itertuples(index=False):
            rows.append((
                row.symbol, row.date, row.open, row.high,
                row.low, row.close, row.volume, row.amount,
            ))
    with sqlite3.connect(engine.db_path) as conn:
        conn.executemany(
            "INSERT INTO stock_daily "
            "(symbol, date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def test_rps_momentum_selects_strongest_uptrend() -> None:
    """横截面 RPS 最强且处上升趋势 + 流动性达标的美股应被选出。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(
            tmp_dir,
            us_rps_period=5,
            us_rps_threshold=90.0,
            us_liquidity_min_dollar_volume=1_000.0,
        )
        engine = UsStockDataEngine(settings)

        frames = []
        # 9 只弱势/横盘股：近 5 日几乎不涨
        for i in range(9):
            closes = [100.0 + 0.01 * j for j in range(60)]
            frames.append(_frame(
                opens=closes, highs=[c + 0.1 for c in closes],
                lows=[c - 0.1 for c in closes], closes=closes,
                volumes=[1e5] * 60, symbol=f"WEAK{i}.US",
            ))
        # 1 只强势股：长期上升且近 5 日大涨（RPS 应排第一）
        strong = [100.0 + j for j in range(55)] + [160, 165, 175, 185, 200]
        frames.append(_frame(
            opens=strong, highs=[c + 0.5 for c in strong],
            lows=[c - 0.5 for c in strong], closes=strong,
            volumes=[1e5] * 60, symbol="STRONG.US",
        ))
        _seed_us_db(engine, frames)

        result = UsRpsMomentumStrategy(engine=engine, settings=settings).run()
        assert "STRONG.US" in result
