"""涨停洗盘选股策略（股票）：昨日涨停后今日放量收阴但不破昨收。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class LimitUpShakeoutStrategy(BaseStrategy):
    """涨停洗盘策略。

    选股条件（向量化）：
    1. 昨日涨停：昨日 close >= 前日 close × 1.095。
    2. 今日收阴：今日 close < 今日 open。
    3. 今日放量：今日 volume > 昨日 volume × 2.0。
    4. 支撑不破：今日 low >= 昨日 close。

    Attributes:
        webhook_key: 路由到 'stock_shakeout' 专属飞书机器人。
    """

    webhook_key: str = "stock_shakeout"
    _MIN_BARS: int = 3

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                prev2 = df.iloc[-3]
                prev1 = df.iloc[-2]
                today = df.iloc[-1]

                limit_up_yesterday = prev1["close"] >= prev2["close"] * 1.095
                bearish_today = today["close"] < today["open"]
                volume_surge = today["volume"] > prev1["volume"] * 2.0
                support_hold = today["low"] >= prev1["close"]

                if limit_up_yesterday and bearish_today and volume_surge and support_hold:
                    selected.append(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] LimitUpShakeoutStrategy 计算失败：{exc}")
                continue

        logger.info(f"LimitUpShakeoutStrategy 选出 {len(selected)} 只股票")
        return selected
