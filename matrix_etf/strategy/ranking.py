"""Shared, point-in-time ranking of a market's daily strategy candidates."""

from collections import Counter
from math import isfinite

import pandas as pd

from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)

RANKING_VERSION = "momentum40-drawdown20-liquidity20-consensus20-v1"
WEIGHTS = {"momentum": 0.4, "drawdown": 0.2, "liquidity": 0.2, "consensus": 0.2}


def _features(engine, symbol: str) -> dict[str, float] | None:
    frame = engine.get_ohlcv(symbol)
    if frame.empty or not {"date", "close", "volume"}.issubset(frame.columns):
        logger.warning(f"[{symbol}] Cannot rank: missing price history")
        return None
    frame = frame.sort_values("date").tail(21)
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    if len(close) < 21 or not all(isfinite(x) and x > 0 for x in close):
        logger.warning(f"[{symbol}] Cannot rank: need 21 valid closing prices")
        return None
    if not all(isfinite(x) and x >= 0 for x in volume):
        logger.warning(f"[{symbol}] Cannot rank: invalid volume")
        return None
    if symbol.upper().endswith(".US") or "amount" not in frame:
        amount = close * volume
    else:
        amount = pd.to_numeric(frame["amount"], errors="coerce")
    if not all(isfinite(x) and x >= 0 for x in amount.tail(20)):
        logger.warning(f"[{symbol}] Cannot rank: invalid traded amount")
        return None
    liquidity = float(amount.tail(20).mean())
    if liquidity <= 0:
        logger.warning(f"[{symbol}] Cannot rank: no liquidity")
        return None
    returns = close.pct_change(fill_method=None).dropna()
    volatility = float(returns.std())
    # The floor prevents a near-constant series from dominating the ranking.
    momentum = float(close.iloc[-1] / close.iloc[0] - 1) / max(volatility, 0.001)
    drawdown = float((close / close.cummax() - 1).min())
    return {"momentum": momentum, "drawdown": drawdown, "liquidity": liquidity}


def select_recommendations(
    engine, candidates: list[list[str]], limit: int = 10
) -> list[list[str]]:
    """Keep at most ``limit`` unique symbols across all strategies of one market.

    Factors use only data visible through ``engine`` (date-capped during backtests).
    Percentile ranks normalize units; higher scores win, with symbol as tie-breaker.
    A retained symbol remains attributed to every strategy that selected it.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError("recommendation limit must be an integer between 1 and 10")
    votes = Counter(symbol for picks in candidates for symbol in set(picks))
    rows = {}
    for symbol in sorted(votes):
        features = _features(engine, symbol)
        if features is not None:
            rows[symbol] = {**features, "consensus": float(votes[symbol])}
    if not rows:
        return [[] for _ in candidates]
    factors = pd.DataFrame.from_dict(rows, orient="index")
    scores = factors.rank(method="average", pct=True).mul(pd.Series(WEIGHTS)).sum(axis=1)
    ordered = sorted(rows, key=lambda symbol: (-float(scores[symbol]), symbol))[:limit]
    selected = [[symbol for symbol in ordered if symbol in set(picks)] for picks in candidates]
    logger.info(
        f"Daily ranking: {len(votes)} unique candidates -> {len(ordered)} recommendations "
        f"(limit={limit}, {RANKING_VERSION})"
    )
    return selected
