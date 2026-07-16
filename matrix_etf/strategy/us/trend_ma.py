"""美股均线趋势策略：多头排列（close>MA50>MA200）且当日上穿 MA50。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UsTrendMaStrategy(BaseStrategy):
    """美股均线趋势跟随策略。

    选股条件（全部满足）：

    1. ``close > MA50 > MA200``（多头排列）。
    2. 昨日 ``close <= MA50`` 且今日 ``close > MA50``（上穿确认，捕捉趋势重启）。
    3. 近 20 日平均美元成交额（close×volume）
       ``>= us_liquidity_min_dollar_volume``（流动性）。

    结果按趋势强度（``close / MA200 - 1``）从高到低排序。

    Attributes:
        webhook_key: 路由到 'us_trend' 专属飞书机器人。
    """

    webhook_key: str = "us_trend"

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        min_dollar_volume = float(self.settings.us_liquidity_min_dollar_volume)
        scored: list[tuple[str, float]] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < 200:
                    continue
                if not self._passes_dollar_volume(
                    df["close"], df["volume"], min_dollar_volume
                ):
                    continue

                df = df.copy()
                df["ma50"] = df["close"].rolling(50).mean()
                df["ma200"] = df["close"].rolling(200).mean()

                last = df.iloc[-1]
                prev = df.iloc[-2]

                bullish = last["close"] > last["ma50"] > last["ma200"]
                cross_up = prev["close"] <= prev["ma50"] and last["close"] > last["ma50"]

                if bullish and cross_up:
                    strength = float(last["close"] / last["ma200"] - 1.0)
                    scored.append((symbol, strength))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] UsTrendMaStrategy 计算失败：{exc}")
                continue

        scored.sort(key=lambda item: item[1], reverse=True)
        result = [symbol for symbol, _ in scored]
        logger.info(f"UsTrendMaStrategy 选出 {len(result)} 只美股")
        return result
