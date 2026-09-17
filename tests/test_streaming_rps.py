import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from matrix_etf.core.config import Settings
from matrix_etf.strategy.stock.rps_breakout import RpsBreakoutStrategy
from matrix_etf.strategy.us.rps_momentum import UsRpsMomentumStrategy


@pytest.mark.parametrize("strategy_type", [RpsBreakoutStrategy, UsRpsMomentumStrategy])
def test_streaming_rps_matches_full_market_reference(tmp_path, strategy_type):
    rng = np.random.default_rng(37)
    dates = pd.bdate_range("2025-01-01", periods=200).strftime("%Y-%m-%d")
    frames = []
    for i in range(40):
        count = 8 if i == 0 else 200
        close = 100 * np.cumprod(1 + rng.normal(0.001 * (i % 4), 0.02, count))
        frame = pd.DataFrame({
            "symbol": f"S{i:03}", "date": dates[-count:], "close": close,
            "high": close * 1.02, "volume": rng.uniform(1e4, 1e7, count),
        })
        if i % 7 == 0:
            frame = frame.iloc[:-1]  # Stale symbols must not enter today's rank.
        if i == 5:
            frame.loc[190, "volume"] = np.nan
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])
    path = str(tmp_path / "prices.db")
    with sqlite3.connect(path) as conn:
        data.to_sql("stock_daily", conn, index=False)
        conn.execute("CREATE INDEX symbol_date ON stock_daily(symbol, date)")
    settings = Settings(
        feishu_webhook_url="https://example.com", stock_rps_period=120,
        us_rps_period=120, stock_rps_threshold=50, us_rps_threshold=50,
        us_liquidity_min_dollar_volume=1e8,
    )
    group = data.groupby("symbol")
    base = group["close"].shift(120)
    data["pct_change"] = (data["close"] - base) / base
    data["ma50"] = group["close"].transform(lambda s: s.rolling(50).mean())
    data["dollar_volume"] = data["close"] * data["volume"]
    data["dollar_vol20"] = data.groupby("symbol")["dollar_volume"].transform(
        lambda s: s.rolling(20).mean()
    )
    data["roll_high"] = group["high"].transform(
        lambda s: s.rolling(120, min_periods=60).max()
    )
    latest = data[data.date == data.date.max()].dropna(subset=["pct_change"]).copy()
    latest["rps"] = latest["pct_change"].rank(pct=True) * 100
    expected = latest[latest.rps >= 50]
    if strategy_type is UsRpsMomentumStrategy:
        expected = expected[
            (expected.close >= expected.ma50) & (expected.dollar_vol20 >= 1e8)
        ].sort_values("rps", ascending=False)
    else:
        expected = expected[expected.close >= expected.roll_high * 0.9]
    original = pd.DataFrame.from_records
    batch_sizes = []

    def bounded_frame(rows, **kwargs):
        batch_sizes.append(len(rows))
        return original(rows, **kwargs)

    with patch.object(pd.DataFrame, "from_records", side_effect=bounded_frame):
        result = strategy_type(SimpleNamespace(db_path=path), settings).run()
    assert result == expected.symbol.tolist()
    assert len(batch_sizes) == 40
    assert max(batch_sizes) <= 200  # Never materialize the whole market.
