"""RPS 极强动量突破选股策略（股票）：横截面动量排位 + 阶段新高突破。"""

import sqlite3

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略。

    与逐只遍历的策略不同，本策略一次性读取全市场日 K 做横截面排位：
    1. 计算每只股票近 ``stock_rps_period`` 个交易日的涨幅。
    2. 横截面按涨幅百分位排名得到 RPS，取 RPS >= ``stock_rps_threshold`` 的强势股。
    3. 在强势股中保留今日 close >= 阶段滚动最高价 × 0.90 的突破标的。

    Attributes:
        webhook_key: 路由到 'stock_rps' 专属飞书机器人。
    """

    webhook_key: str = "stock_rps"

    def run(self) -> list[str]:
        period = int(self.settings.stock_rps_period)
        threshold = float(self.settings.stock_rps_threshold)

        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql(
                    "SELECT symbol, date, close, high FROM stock_daily", conn
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"读取股票数据库失败：{exc}")
            return []

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])

        # 纵向：区间涨幅
        df["close_shift"] = df.groupby("symbol")["close"].shift(period)
        df["pct_change"] = (df["close"] - df["close_shift"]) / df["close_shift"]

        # 纵向：阶段滚动最高价
        df["roll_high"] = (
            df.groupby("symbol")["high"]
            .rolling(window=period, min_periods=period // 2)
            .max()
            .reset_index(level=0, drop=True)
        )

        latest_date = df["date"].max()
        latest = df[df["date"] == latest_date].copy()
        latest = latest.dropna(subset=["pct_change"])
        if latest.empty:
            return []

        # 横向：RPS 百分位排名
        latest["rps"] = latest["pct_change"].rank(pct=True) * 100
        strong = latest[latest["rps"] >= threshold]

        breakout = strong[strong["close"] >= strong["roll_high"] * 0.90]
        selected = breakout["symbol"].tolist()

        logger.info(f"RpsBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected
