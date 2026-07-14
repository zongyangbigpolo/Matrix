"""海龟交易突破选股策略（股票）。"""

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class TurtleTradeStrategy(BaseStrategy):
    """海龟交易突破策略（A 股防诱多改良版）。

    选股条件（向量化）：
    1. 突破新高：今日 close > 前 20 个交易日 high 的最大值（不含当日）。
    2. 流动性：今日成交额 > ``stock_liquidity_min_amount``。
    3. 防诱多过滤：今日为实体阳线（close > open）且相对昨日真涨（close > 昨日 close）。

    结果按当日涨幅从大到小排序。

    Attributes:
        webhook_key: 路由到 'stock_turtle' 专属飞书机器人。
    """

    webhook_key: str = "stock_turtle"
    _MIN_BARS: int = 21

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        min_amount = self.settings.stock_liquidity_min_amount
        candidates: list[tuple[str, float]] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                high_20 = df["high"].shift(1).rolling(20).max().iloc[-1]
                if pd.isna(high_20):
                    continue

                last = df.iloc[-1]
                prev_close = df["close"].iloc[-2]

                breakout = last["close"] > high_20
                liquid = last["amount"] > min_amount
                is_yang = last["close"] > last["open"]
                is_up = last["close"] > prev_close

                if breakout and liquid and is_yang and is_up and prev_close:
                    change_pct = (last["close"] - prev_close) / prev_close * 100
                    candidates.append((symbol, change_pct))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] TurtleTradeStrategy 计算失败：{exc}")
                continue

        candidates.sort(key=lambda item: item[1], reverse=True)
        selected = [symbol for symbol, _ in candidates]
        logger.info(f"TurtleTradeStrategy 选出 {len(selected)} 只股票")
        return selected
