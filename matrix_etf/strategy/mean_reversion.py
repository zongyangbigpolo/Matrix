"""强势回踩策略：长期趋势向上（close>MA200）时回踩 MA20 且 RSI 超卖反弹。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.data.engine import DataEngine
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """强势回踩（均值回归）策略。

    只在长期上升趋势中做回调买入，避免接下跌趋势的飞刀。

    选股条件（全部满足）：
    1. ``close > MA200``（长期趋势向上）
    2. 回踩 MA20 附近：``low ≤ MA20 × 1.02`` 且 ``close ≥ MA20 × 0.98``
    3. ``RSI(14) < 45``（短期超卖但未破位）
    4. 近 20 日平均成交额 ≥ 流动性门槛

    结果按 RSI 从低到高排序（越超卖越靠前）。

    Attributes:
        webhook_key: 路由到 'pullback' 专属飞书机器人。
    """

    webhook_key: str = "pullback"

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        scored: list[tuple[str, float]] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 200:
                    continue
                if not self._passes_liquidity(df["amount"]):
                    continue

                df = df.copy()
                df["ma20"] = df["close"].rolling(20).mean()
                df["ma200"] = df["close"].rolling(200).mean()

                last = df.iloc[-1]
                rsi = DataEngine._rsi(df["close"])
                if rsi is None:
                    continue

                uptrend = last["close"] > last["ma200"]
                pullback = (
                    last["low"] <= last["ma20"] * 1.02
                    and last["close"] >= last["ma20"] * 0.98
                )
                oversold = rsi < 45.0

                if uptrend and pullback and oversold:
                    scored.append((symbol, float(rsi)))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] 策略计算失败：{exc}")
                continue

        scored.sort(key=lambda item: item[1])
        result = [symbol for symbol, _ in scored]
        logger.info(f"MeanReversionStrategy 选出 {len(result)} 只 ETF")
        return result
