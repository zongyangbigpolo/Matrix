"""美股均线金叉 + 放量确认策略。"""

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UsMaVolumeStrategy(BaseStrategy):
    """美股均线金叉 + 放量确认策略。

    选股条件（全部向量化，避免逐行遍历）：

    1. 5 日均线上穿 20 日均线（昨日 ma5 < ma20，今日 ma5 > ma20）。
    2. 当日成交量 > 20 日均量 × ``us_ma_volume_surge``（放量确认）。
    3. 近 20 日平均美元成交额（close×volume）
       ``>= us_liquidity_min_dollar_volume``（流动性，剔除仙股 / 冷门股）。

    Attributes:
        webhook_key: 路由到 'us_ma_volume' 专属飞书机器人。
    """

    webhook_key: str = "us_ma_volume"
    _MIN_BARS: int = 21

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        surge = self.settings.us_ma_volume_surge
        min_dollar_volume = float(self.settings.us_liquidity_min_dollar_volume)
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue
                if not self._passes_dollar_volume(
                    df["close"], df["volume"], min_dollar_volume
                ):
                    continue

                ma5 = df["close"].rolling(5).mean()
                ma20 = df["close"].rolling(20).mean()
                vol_ma20 = df["volume"].rolling(20).mean()

                golden_cross = ma5.iloc[-2] < ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1]
                volume_surge = df["volume"].iloc[-1] > vol_ma20.iloc[-1] * surge

                if golden_cross and volume_surge:
                    selected.append(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] UsMaVolumeStrategy 计算失败：{exc}")
                continue

        logger.info(f"UsMaVolumeStrategy 选出 {len(selected)} 只美股")
        return selected
