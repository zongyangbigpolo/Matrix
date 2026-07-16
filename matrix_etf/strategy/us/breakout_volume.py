"""美股放量突破策略：突破 N 日新高 + 放量 + 阳线 + 流动性。"""

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UsBreakoutVolumeStrategy(BaseStrategy):
    """美股放量突破策略。

    选股条件（全部满足）：

    1. 今日 ``close`` 突破前 ``us_breakout_period`` 个交易日 high 的最大值
       （不含当日），创阶段新高。
    2. 当日成交量 > ``us_breakout_period`` 日均量 × ``us_volume_surge``（放量）。
    3. 今日为阳线（``close > open``），确认资金真实进场。
    4. 近 20 日平均美元成交额（close×volume）
       ``>= us_liquidity_min_dollar_volume``（流动性）。

    结果按当日涨幅从大到小排序。

    Attributes:
        webhook_key: 路由到 'us_breakout' 专属飞书机器人。
    """

    webhook_key: str = "us_breakout"

    def run(self) -> list[str]:
        period = int(self.settings.us_breakout_period)
        surge = float(self.settings.us_volume_surge)
        min_dollar_volume = float(self.settings.us_liquidity_min_dollar_volume)
        min_bars = period + 1
        candidates: list[tuple[str, float]] = []

        for symbol in self.engine.get_local_symbols():
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < min_bars:
                    continue
                if not self._passes_dollar_volume(
                    df["close"], df["volume"], min_dollar_volume
                ):
                    continue

                high_prev = df["high"].shift(1).rolling(period).max().iloc[-1]
                vol_ma = df["volume"].shift(1).rolling(period).mean().iloc[-1]
                if pd.isna(high_prev) or pd.isna(vol_ma):
                    continue

                last = df.iloc[-1]
                prev_close = df["close"].iloc[-2]

                breakout = last["close"] > high_prev
                volume_surge = last["volume"] > vol_ma * surge
                is_yang = last["close"] > last["open"]

                if breakout and volume_surge and is_yang and prev_close:
                    change_pct = (last["close"] - prev_close) / prev_close * 100
                    candidates.append((symbol, change_pct))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] UsBreakoutVolumeStrategy 计算失败：{exc}")
                continue

        candidates.sort(key=lambda item: item[1], reverse=True)
        selected = [symbol for symbol, _ in candidates]
        logger.info(f"UsBreakoutVolumeStrategy 选出 {len(selected)} 只美股")
        return selected
