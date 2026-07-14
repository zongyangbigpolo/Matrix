"""相对强度动量策略：横截面 120 日收益 RPS 领先 + 处于强势区 + 流动性达标。"""

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class RpsMomentumStrategy(BaseStrategy):
    """相对强度（RPS）动量策略。

    选股条件（全部满足）：
    1. 近 ``RPS_PERIOD``（默认 120）日收益率在全体 ETF 中的百分位 ≥ ``RPS_THRESHOLD``（默认 90）
    2. 当前价 ≥ 近 RPS_PERIOD 日最高价 × 0.9（处于强势区）
    3. 近 20 日平均成交额 ≥ 流动性门槛

    结果按 RPS 从高到低排序。

    Attributes:
        webhook_key: 路由到 'rps' 专属飞书机器人。
    """

    webhook_key: str = "rps"

    def run(self) -> list[str]:
        period = self.settings.rps_period
        threshold = self.settings.rps_threshold
        symbols = self.engine.get_local_symbols()

        records: list[dict] = []
        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) <= period:
                    continue
                if not self._passes_liquidity(df["amount"]):
                    continue

                close = df["close"]
                ret = close.iloc[-1] / close.iloc[-1 - period] - 1.0
                high_window = float(close.tail(period).max())
                near_high = close.iloc[-1] >= high_window * 0.9

                records.append(
                    {"symbol": symbol, "ret": float(ret), "near_high": bool(near_high)}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] 策略计算失败：{exc}")
                continue

        if not records:
            logger.info("RpsMomentumStrategy 无候选 ETF")
            return []

        frame = pd.DataFrame(records)
        frame["rps"] = frame["ret"].rank(pct=True) * 100.0
        selected = (
            frame[(frame["rps"] >= threshold) & (frame["near_high"])]
            .sort_values("rps", ascending=False)
        )

        result = selected["symbol"].tolist()
        logger.info(f"RpsMomentumStrategy 选出 {len(result)} 只 ETF")
        return result
