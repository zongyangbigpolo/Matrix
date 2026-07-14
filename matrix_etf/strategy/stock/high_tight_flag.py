"""高旗形整理选股策略（股票）：强动量后极度收敛缩量。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class HighTightFlagStrategy(BaseStrategy):
    """高旗形整理策略。

    选股条件（向量化）：
    1. 强动量：过去 40 天区间最高价 / 最低价 > 1.6（区间涨幅超 60%）。
    2. 极度收敛：最近 10 天区间最高价 / 最低价 < 1.15（振幅低于 15%）。
    3. 高位抗跌：最近 10 天最低价 >= 40 天最高价 × 0.8。
    4. 缩量：今日成交量 < 过去 20 日均量 × 0.6。

    Attributes:
        webhook_key: 路由到 'stock_flag' 专属飞书机器人。
    """

    webhook_key: str = "stock_flag"
    _MIN_BARS: int = 40

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                tail40 = df.tail(40)
                tail10 = df.tail(10)

                high40 = tail40["high"].max()
                low40 = tail40["low"].min()
                high10 = tail10["high"].max()
                low10 = tail10["low"].min()
                if low40 == 0 or low10 == 0:
                    continue

                momentum = high40 / low40 > 1.6
                consolidation = high10 / low10 < 1.15
                high_level = low10 >= high40 * 0.8
                vol_ma20 = df["volume"].iloc[-21:-1].mean()
                shrink = df["volume"].iloc[-1] < vol_ma20 * 0.6

                if momentum and consolidation and high_level and shrink:
                    selected.append(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] HighTightFlagStrategy 计算失败：{exc}")
                continue

        logger.info(f"HighTightFlagStrategy 选出 {len(selected)} 只股票")
        return selected
