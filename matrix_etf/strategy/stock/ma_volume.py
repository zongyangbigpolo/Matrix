"""均线金叉 + 放量确认选股策略（股票）。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class MaVolumeStrategy(BaseStrategy):
    """均线金叉 + 放量确认策略。

    选股条件（全部向量化，避免逐行遍历）：
    1. 5 日均线上穿 20 日均线（昨日 ma5 < ma20，今日 ma5 > ma20）。
    2. 当日成交量 > 20 日均量 × ``stock_ma_volume_surge``（放量确认）。

    Attributes:
        webhook_key: 路由到 'stock_ma_volume' 专属飞书机器人。
    """

    webhook_key: str = "stock_ma_volume"
    _MIN_BARS: int = 21

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        surge = self.settings.stock_ma_volume_surge
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                ma5 = df["close"].rolling(5).mean()
                ma20 = df["close"].rolling(20).mean()
                vol_ma20 = df["volume"].rolling(20).mean()

                golden_cross = ma5.iloc[-2] < ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1]
                volume_surge = df["volume"].iloc[-1] > vol_ma20.iloc[-1] * surge

                if golden_cross and volume_surge:
                    selected.append(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] MaVolumeStrategy 计算失败：{exc}")
                continue

        logger.info(f"MaVolumeStrategy 选出 {len(selected)} 只股票")
        return selected
