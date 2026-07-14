"""放量突破策略：突破 N 日新高 + 成交额放大 + 阳线。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class BreakoutVolumeStrategy(BaseStrategy):
    """放量突破策略。

    选股条件（全部满足）：
    1. 今日 ``close`` 突破前 ``BREAKOUT_PERIOD``（默认 60）日的最高价
    2. 今日成交额 > 近 20 日平均成交额 × ``VOLUME_SURGE``（默认 1.5）
    3. 今日为阳线（``close > open``）
    4. 近 20 日平均成交额 ≥ 流动性门槛

    结果按当日涨幅从高到低排序。

    Attributes:
        webhook_key: 路由到 'breakout' 专属飞书机器人。
    """

    webhook_key: str = "breakout"

    def run(self) -> list[str]:
        period = self.settings.breakout_period
        surge = self.settings.volume_surge
        symbols = self.engine.get_local_symbols()
        scored: list[tuple[str, float]] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < period + 2:
                    continue
                if not self._passes_liquidity(df["amount"]):
                    continue

                last = df.iloc[-1]
                # 前 period 日（不含今日）的最高价
                prior_high = float(df["high"].iloc[-1 - period:-1].max())
                avg_amount_20 = float(df["amount"].tail(20).mean())

                breakout = last["close"] > prior_high
                volume_surge = last["amount"] > avg_amount_20 * surge
                bullish = last["close"] > last["open"]

                if breakout and volume_surge and bullish:
                    change = float(last["close"] / df["close"].iloc[-2] - 1.0)
                    scored.append((symbol, change))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] 策略计算失败：{exc}")
                continue

        scored.sort(key=lambda item: item[1], reverse=True)
        result = [symbol for symbol, _ in scored]
        logger.info(f"BreakoutVolumeStrategy 选出 {len(result)} 只 ETF")
        return result
