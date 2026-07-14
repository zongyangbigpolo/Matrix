"""策略引擎属性测试。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from matrix_etf.core.config import Settings
from matrix_etf.data.engine import DataEngine
from matrix_etf.strategy.etf.breakout_volume import BreakoutVolumeStrategy
from matrix_etf.strategy.etf.mean_reversion import MeanReversionStrategy
from matrix_etf.strategy.etf.mega7_rotation import (
    LowVolTrendRotationStrategy,
    RiskAdjustedMomentumStrategy,
    VolumeConfirmedMomentumStrategy,
)
from matrix_etf.strategy.etf.rps_momentum import RpsMomentumStrategy
from matrix_etf.strategy.etf.trend_ma import TrendMaStrategy

ALL_STRATEGIES = [
    RpsMomentumStrategy,
    TrendMaStrategy,
    BreakoutVolumeStrategy,
    MeanReversionStrategy,
    RiskAdjustedMomentumStrategy,
    VolumeConfirmedMomentumStrategy,
    LowVolTrendRotationStrategy,
]


def make_settings(tmp_dir: str) -> Settings:
    return Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
    )


@pytest.mark.parametrize("strategy_cls", ALL_STRATEGIES)
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0, max_size=3, unique=True,
    )
)
@h_settings(max_examples=20, deadline=None)
def test_strategy_run_returns_list_of_str(strategy_cls, symbols: list[str]) -> None:
    """所有策略 run() 应返回 list[str]，每个元素为非空字符串。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = DataEngine(settings)
        full_symbols = [f"{s}.SH" for s in symbols]
        with patch.object(engine, "get_local_symbols", return_value=full_symbols):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                strategy = strategy_cls(engine=engine, settings=settings)
                result = strategy.run()

    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)


def _uptrend_frame(n: int = 220, start: float = 10.0, step: float = 0.05) -> pd.DataFrame:
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "symbol": "510300.SH",
        "date": [f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)],
        "open": [c - 0.02 for c in closes],
        "high": [c + 0.05 for c in closes],
        "low": [c - 0.05 for c in closes],
        "close": closes,
        "volume": [1_000_000.0] * n,
        "amount": [1e8] * n,
    })


def test_trend_ma_selects_bullish_cross() -> None:
    """构造上穿场景，TrendMaStrategy 应选出该 ETF。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = DataEngine(settings)
        df = _uptrend_frame()
        # 制造上穿：让倒数第二日收盘略低于 MA50，最后一日收盘高于 MA50
        ma50_prev = df["close"].iloc[-51:-1].mean()
        df.loc[df.index[-2], "close"] = ma50_prev - 0.1
        df.loc[df.index[-1], "close"] = df["close"].iloc[-50:].mean() + 0.5

        with patch.object(engine, "get_local_symbols", return_value=["510300.SH"]):
            with patch.object(engine, "get_ohlcv", return_value=df):
                result = TrendMaStrategy(engine=engine, settings=settings).run()

    assert "510300.SH" in result


def test_liquidity_filter_excludes_illiquid() -> None:
    """成交额低于门槛的 ETF 应被所有策略过滤掉。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = DataEngine(settings)
        df = _uptrend_frame()
        df["amount"] = 1000.0  # 远低于默认 5000 万门槛

        with patch.object(engine, "get_local_symbols", return_value=["510300.SH"]):
            with patch.object(engine, "get_ohlcv", return_value=df):
                for strategy_cls in ALL_STRATEGIES:
                    result = strategy_cls(engine=engine, settings=settings).run()
                    assert result == []


def test_risk_adjusted_momentum_selects_positive_multi_period_trend() -> None:
    """多周期正动量、低下行频率且流动性达标时，风险调整动量策略应选出 ETF。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = DataEngine(settings)
        df = _uptrend_frame(n=220, step=0.03)

        with patch.object(engine, "get_local_symbols", return_value=["510300.SH"]):
            with patch.object(engine, "get_ohlcv", return_value=df):
                result = RiskAdjustedMomentumStrategy(engine=engine, settings=settings).run()

    assert result == ["510300.SH"]


def test_volume_confirmed_momentum_requires_short_volume_expansion() -> None:
    """短期成交额高于长期均值时，成交额确认动量策略应选出 ETF。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = DataEngine(settings)
        df = _uptrend_frame(n=220, step=0.03)
        df["amount"] = [8e7] * 160 + [1.6e8] * 60

        with patch.object(engine, "get_local_symbols", return_value=["510300.SH"]):
            with patch.object(engine, "get_ohlcv", return_value=df):
                result = VolumeConfirmedMomentumStrategy(engine=engine, settings=settings).run()

    assert result == ["510300.SH"]


def test_low_vol_trend_rotation_selects_bullish_low_downside_etf() -> None:
    """多头排列、60日收益为正且下行频率低时，低波趋势轮动策略应选出 ETF。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = make_settings(tmp_dir)
        engine = DataEngine(settings)
        df = _uptrend_frame(n=220, step=0.02)

        with patch.object(engine, "get_local_symbols", return_value=["510300.SH"]):
            with patch.object(engine, "get_ohlcv", return_value=df):
                result = LowVolTrendRotationStrategy(engine=engine, settings=settings).run()

    assert result == ["510300.SH"]
