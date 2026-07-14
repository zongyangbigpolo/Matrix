"""Mega7 风格 ETF 轮动策略。

参考 ETF + Mega 7 轮动模型的公开策略思想：多周期动量、风险调整、
成交额确认、下行频率过滤和低波动倾向；本模块使用 Matrix 的沪深 ETF
日线数据重新实现为原创 ETF 选基策略。
"""

from math import isfinite

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


def _parse_periods(value: str) -> list[int]:
    periods: list[int] = []
    for item in value.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            period = int(item)
        except ValueError:
            continue
        if period > 0:
            periods.append(period)
    return sorted(set(periods)) or [21, 63, 126]


def _avg_total_return(close: pd.Series, periods: list[int]) -> float | None:
    returns: list[float] = []
    for period in periods:
        if len(close) <= period:
            continue
        base = close.iloc[-1 - period]
        if not base or pd.isna(base):
            continue
        value = float(close.iloc[-1] / base - 1.0)
        if isfinite(value):
            returns.append(value)
    if not returns:
        return None
    return float(sum(returns) / len(returns))


def _downside_frequency(returns: pd.Series, lookback: int) -> float | None:
    window = returns.tail(lookback)
    if window.empty:
        return None
    return float((window < 0).mean())


def _volume_multiplier(
    amount: pd.Series,
    short_days: int,
    long_days: int,
    floor: float,
    cap: float,
) -> float:
    if len(amount) < max(short_days, long_days):
        return 1.0
    short_avg = float(amount.tail(short_days).mean())
    long_avg = float(amount.tail(long_days).mean())
    if not isfinite(short_avg) or not isfinite(long_avg) or long_avg <= 0:
        return 1.0
    return max(floor, min(short_avg / long_avg, cap))


class _Mega7BaseStrategy(BaseStrategy):
    """Mega7 风格策略公共工具。"""

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in ("open", "high", "low", "close", "amount"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        return out.dropna(subset=["close", "amount"]).sort_values("date")

    def _base_score(self, df: pd.DataFrame) -> tuple[float, float, float] | None:
        periods = _parse_periods(self.settings.mega7_momentum_periods)
        max_period = max(periods)
        min_days = max(
            max_period + 1,
            self.settings.mega7_volatility_days + 1,
            self.settings.mega7_downside_lookback_days + 1,
            self.settings.mega7_volume_long_days,
        )
        if len(df) < min_days:
            return None

        close = df["close"]
        daily_returns = close.pct_change().dropna()
        avg_ret = _avg_total_return(close, periods)
        if avg_ret is None or avg_ret <= 0:
            return None

        volatility = float(daily_returns.tail(self.settings.mega7_volatility_days).std())
        if not isfinite(volatility) or volatility <= 0:
            return None

        downside = _downside_frequency(
            daily_returns,
            self.settings.mega7_downside_lookback_days,
        )
        if downside is None or downside > self.settings.mega7_downside_threshold:
            return None

        multiplier = _volume_multiplier(
            df["amount"],
            self.settings.mega7_volume_short_days,
            self.settings.mega7_volume_long_days,
            self.settings.mega7_volume_multiplier_floor,
            self.settings.mega7_volume_multiplier_cap,
        )
        return avg_ret / volatility * multiplier, multiplier, downside

    def _ranked_symbols(self, scored: list[tuple[str, float]]) -> list[str]:
        scored.sort(key=lambda item: item[1], reverse=True)
        return [symbol for symbol, _ in scored[: self.settings.mega7_top_n]]


class RiskAdjustedMomentumStrategy(_Mega7BaseStrategy):
    """多周期风险调整动量：平均动量 / 短期波动 × 成交额倍率。"""

    webhook_key: str = "mega7_momentum"

    def run(self) -> list[str]:
        scored: list[tuple[str, float]] = []
        for symbol in self.engine.get_local_symbols():
            try:
                df = self._prepare(self.engine.get_ohlcv(symbol))
                if not self._passes_liquidity(df["amount"]):
                    continue
                base = self._base_score(df)
                if base is None:
                    continue
                score, _, _ = base
                if isfinite(score) and score > 0:
                    scored.append((symbol, score))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] Mega7 风险调整动量计算失败：{exc}")

        result = self._ranked_symbols(scored)
        logger.info(f"RiskAdjustedMomentumStrategy 选出 {len(result)} 只 ETF")
        return result


class VolumeConfirmedMomentumStrategy(_Mega7BaseStrategy):
    """成交额确认动量：只保留短期成交额高于中期均值的正动量 ETF。"""

    webhook_key: str = "mega7_volume"

    def run(self) -> list[str]:
        scored: list[tuple[str, float]] = []
        for symbol in self.engine.get_local_symbols():
            try:
                df = self._prepare(self.engine.get_ohlcv(symbol))
                if not self._passes_liquidity(df["amount"]):
                    continue
                base = self._base_score(df)
                if base is None:
                    continue
                score, multiplier, downside = base
                if multiplier <= 1.0:
                    continue
                adjusted_score = score * multiplier * (1.0 - downside)
                if isfinite(adjusted_score) and adjusted_score > 0:
                    scored.append((symbol, adjusted_score))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] Mega7 成交额确认动量计算失败：{exc}")

        result = self._ranked_symbols(scored)
        logger.info(f"VolumeConfirmedMomentumStrategy 选出 {len(result)} 只 ETF")
        return result


class LowVolTrendRotationStrategy(_Mega7BaseStrategy):
    """低波趋势轮动：多头排列中偏好单位波动趋势强度更高的 ETF。"""

    webhook_key: str = "mega7_lowvol"

    def run(self) -> list[str]:
        scored: list[tuple[str, float]] = []
        for symbol in self.engine.get_local_symbols():
            try:
                df = self._prepare(self.engine.get_ohlcv(symbol))
                if len(df) < 200 or not self._passes_liquidity(df["amount"]):
                    continue

                df["ma50"] = df["close"].rolling(50).mean()
                df["ma200"] = df["close"].rolling(200).mean()
                last = df.iloc[-1]
                if not (last["close"] > last["ma50"] > last["ma200"]):
                    continue

                close = df["close"]
                daily_returns = close.pct_change().dropna()
                downside = _downside_frequency(
                    daily_returns,
                    self.settings.mega7_downside_lookback_days,
                )
                if downside is None or downside > self.settings.mega7_downside_threshold:
                    continue

                volatility = float(daily_returns.tail(self.settings.mega7_volatility_days).std())
                if not isfinite(volatility) or volatility <= 0:
                    continue

                ret_60d = close.iloc[-1] / close.iloc[-61] - 1.0 if len(close) > 60 else 0.0
                if ret_60d <= 0:
                    continue

                trend_strength = float(last["close"] / last["ma200"] - 1.0)
                score = trend_strength / volatility * (1.0 - downside)
                if isfinite(score) and score > 0:
                    scored.append((symbol, score))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] Mega7 低波趋势轮动计算失败：{exc}")

        result = self._ranked_symbols(scored)
        logger.info(f"LowVolTrendRotationStrategy 选出 {len(result)} 只 ETF")
        return result
