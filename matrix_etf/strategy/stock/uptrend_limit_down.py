"""上升趋势跌停选股策略（股票）：趋势中放量跌停，捕捉错杀机会。"""

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UptrendLimitDownStrategy(BaseStrategy):
    """上升趋势跌停策略。

    选股条件（向量化）：
    1. 处于上升趋势：昨日 20 日均线 > 昨日 60 日均线。
    2. 放量跌停：今日 close <= 昨日 close × 0.905，且今日 volume > 20 日均量 × 2.0。

    Attributes:
        webhook_key: 路由到 'stock_limit_down' 专属飞书机器人。
    """

    webhook_key: str = "stock_limit_down"
    _MIN_BARS: int = 60

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                ma20 = df["close"].rolling(20).mean()
                ma60 = df["close"].rolling(60).mean()
                vol_ma20 = df["volume"].rolling(20).mean()

                prev_ma20 = ma20.iloc[-2]
                prev_ma60 = ma60.iloc[-2]
                today_vol_ma20 = vol_ma20.iloc[-1]
                if pd.isna(prev_ma20) or pd.isna(prev_ma60) or pd.isna(today_vol_ma20):
                    continue

                uptrend = prev_ma20 > prev_ma60
                limit_down = df["close"].iloc[-1] <= df["close"].iloc[-2] * 0.905
                volume_surge = df["volume"].iloc[-1] > today_vol_ma20 * 2.0

                if uptrend and limit_down and volume_surge:
                    selected.append(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] UptrendLimitDownStrategy 计算失败：{exc}")
                continue

        logger.info(f"UptrendLimitDownStrategy 选出 {len(selected)} 只股票")
        return selected
