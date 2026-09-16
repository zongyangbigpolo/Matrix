"""Bounded historical reads for the registered finite-window production strategies."""

import sqlite3
from contextlib import closing, contextmanager

import pandas as pd

from matrix_etf.analytics.replay import MARKET_TABLES
from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.hold_days import SUGGESTED_HOLD_DAYS

logger = get_logger(__name__)


def history_window(strategies):
    """Minimum safe shared read window; unknown/custom strategies retain full history.

    MA200's previous value needs 201 bars. RSI uses a finite rolling mean, not EWM.
    RPS periods are included whether a strategy uses direct SQL or per-symbol reads.
    """
    window = 21  # Shared recommendation ranking.
    prepared = False
    for strategy in strategies:
        name = type(strategy).__name__
        if name not in SUGGESTED_HOLD_DAYS or not hasattr(strategy, "settings"):
            return None
        settings = strategy.settings
        if name in {"TrendMaStrategy", "UsTrendMaStrategy", "MeanReversionStrategy"}:
            window = max(window, 201)
        elif name == "RpsMomentumStrategy":
            if int(settings.rps_period) < 1:
                return None
            window = max(window, int(settings.rps_period) + 1)
        elif name in {"RpsBreakoutStrategy", "UsRpsMomentumStrategy"}:
            period = int(
                settings.stock_rps_period if name == "RpsBreakoutStrategy"
                else settings.us_rps_period
            )
            if period < 1:
                return None
            window = max(window, period + 1, 50)
        elif name == "BreakoutVolumeStrategy":
            if int(settings.breakout_period) < 1:
                return None
            window = max(window, int(settings.breakout_period) + 2)
        elif name == "UsBreakoutVolumeStrategy":
            if int(settings.us_breakout_period) < 1:
                return None
            window = max(window, int(settings.us_breakout_period) + 2)
        elif name == "HighTightFlagStrategy":
            window = max(window, 40)
        elif name == "UptrendLimitDownStrategy":
            window = max(window, 61)
        elif name in {
            "RiskAdjustedMomentumStrategy", "VolumeConfirmedMomentumStrategy",
            "LowVolTrendRotationStrategy",
        }:
            from matrix_etf.strategy.etf.mega7_rotation import _parse_periods

            if min(
                int(settings.mega7_volatility_days),
                int(settings.mega7_downside_lookback_days),
                int(settings.mega7_volume_long_days), int(settings.mega7_volume_short_days),
            ) < 1:
                return None
            prepared = True
            window = max(
                window, 201, max(_parse_periods(settings.mega7_momentum_periods)) + 1,
                int(settings.mega7_volatility_days) + 1,
                int(settings.mega7_downside_lookback_days) + 1,
                int(settings.mega7_volume_long_days), int(settings.mega7_volume_short_days),
            )
        elif name not in {
            "MaVolumeStrategy", "TurtleTradeStrategy", "LimitUpShakeoutStrategy",
            "UsMaVolumeStrategy",
        }:
            return None
        window = max(window, int(getattr(strategy, "_MIN_BARS", 0)))
    return window, prepared


def snapshot_window(strategies, sample_days):
    """Keep enough observations for the earliest replay day, including direct SQL RPS."""
    plan = history_window(strategies)
    if plan is None:
        return None
    window, prepared = plan
    for strategy in strategies:
        name = type(strategy).__name__
        if name in {"RpsBreakoutStrategy", "UsRpsMomentumStrategy"}:
            period = int(
                strategy.settings.stock_rps_period if name == "RpsBreakoutStrategy"
                else strategy.settings.us_rps_period
            )
            if period < 1:
                return None
            # SQL RPS includes all bars within this many calendar days. Calendar days
            # conservatively upper-bound the number of observations (including weekends).
            window = max(window, int((period + 60) * 1.6) + 1)
    return window + sample_days, prepared


def trim_snapshot_history(snapshot, table, strategies, sample_days, symbols):
    """Prune only the working copy using indexed per-symbol cutoffs, not a global sort.

    A unique (symbol, date) daily row means at most sample_days future observations can
    disappear before the earliest replay day. Keeping that margin preserves every
    registered strategy's required observations. Dirty Mega7 histories are not pruned.
    """
    plan = snapshot_window(strategies, sample_days)
    if plan is None:
        return
    retain, prepared = plan
    logger.info(f"历史工作副本裁剪：每标的保留至少 {retain} 根日线（含回放窗口）")
    with closing(sqlite3.connect(snapshot)) as conn:
        conn.execute("PRAGMA cache_size=-8192")
        for index, symbol in enumerate(symbols):
            row = conn.execute(
                f"SELECT date FROM {table} WHERE symbol=? ORDER BY date DESC LIMIT 1 OFFSET ?",
                (symbol, retain - 1),
            ).fetchone()
            if not row:
                continue
            cutoff = row[0]
            if prepared and conn.execute(
                f"SELECT 1 FROM {table} WHERE symbol=? AND date>=? AND "
                "(typeof(close) NOT IN ('integer','real') OR "
                "typeof(amount) NOT IN ('integer','real')) LIMIT 1",
                (symbol, cutoff),
            ).fetchone():
                continue
            conn.execute(
                f"DELETE FROM {table} WHERE symbol=? AND date<?", (symbol, cutoff)
            )
            if index % 128 == 0:
                conn.commit()
        conn.commit()


@contextmanager
def historical_reads(engine, strategies, market, symbols=None):
    """Reuse one read connection and symbol list per as-of date, without frame caching.

    Only the read adapter changes; each unmodified strategy still runs normally. Frames
    remain independent, so strategy mutations cannot contaminate another strategy.
    """
    plan = history_window(strategies)
    if plan is None:
        yield
        return
    window, prepared = plan
    table = MARKET_TABLES[market][0]
    original = {
        name: engine.__dict__.get(name) for name in ("get_ohlcv", "get_local_symbols")
    }
    overridden = {name: name in engine.__dict__ for name in original}
    with closing(sqlite3.connect(engine.db_path)) as conn:
        conn.execute("PRAGMA cache_size=-8192")
        if symbols is None:
            symbols = [
                row[0] for row in conn.execute(f"SELECT DISTINCT symbol FROM {table} ORDER BY symbol")
            ]

        def get_ohlcv(symbol):
            limit = window
            while True:
                frame = pd.read_sql(
                    f"SELECT * FROM (SELECT * FROM {table} WHERE symbol=? "
                    "ORDER BY date DESC LIMIT ?) ORDER BY date",
                    conn, params=(symbol, limit),
                )
                if not prepared or len(frame) < limit:
                    return frame
                # Mega7 drops invalid close/amount rows before taking rolling windows.
                # Fetch older rows when necessary, rather than changing its valid sample.
                valid = (
                    pd.to_numeric(frame["close"], errors="coerce").notna()
                    & pd.to_numeric(frame["amount"], errors="coerce").notna()
                )
                if int(valid.sum()) >= window:
                    return frame
                limit *= 2

        engine.get_ohlcv = get_ohlcv
        engine.get_local_symbols = lambda: list(symbols)
        try:
            yield
        finally:
            for name, value in original.items():
                if overridden[name]:
                    setattr(engine, name, value)
                else:
                    delattr(engine, name)
