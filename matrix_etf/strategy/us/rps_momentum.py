"""美股相对强度动量策略：横截面 RPS 排位 + 趋势与流动性过滤。"""

import sqlite3

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UsRpsMomentumStrategy(BaseStrategy):
    """美股相对强度动量策略（欧奈尔 RPS 思路）。

    一次性读取全市场日 K 做横截面排位（不逐只遍历）：

    1. 计算每只美股近 ``us_rps_period`` 个交易日涨幅，横截面百分位排名得 RPS。
    2. 保留 ``RPS >= us_rps_threshold`` 的强势股。
    3. 趋势过滤：今日 ``close >= MA50``（仍处上升趋势）。
    4. 流动性过滤：近 20 日平均美元成交额（close×volume）
       ``>= us_liquidity_min_dollar_volume``。

    结果按 RPS 从高到低排序。

    Attributes:
        webhook_key: 路由到 'us_rps' 专属飞书机器人。
    """

    webhook_key: str = "us_rps"

    def run(self) -> list[str]:
        period = int(self.settings.us_rps_period)
        threshold = float(self.settings.us_rps_threshold)
        min_dollar_volume = float(self.settings.us_liquidity_min_dollar_volume)

        try:
            with sqlite3.connect(self.engine.db_path) as conn:
                df = pd.read_sql(
                    "SELECT symbol, date, close, volume FROM stock_daily", conn
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"读取美股数据库失败：{exc}")
            return []

        if df.empty:
            return []

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])
        df["dollar_volume"] = df["close"] * df["volume"]

        grp = df.groupby("symbol")
        df["close_shift"] = grp["close"].shift(period)
        df["pct_change"] = (df["close"] - df["close_shift"]) / df["close_shift"]
        df["ma50"] = grp["close"].transform(
            lambda s: s.rolling(50, min_periods=50).mean()
        )
        df["dollar_vol20"] = grp["dollar_volume"].transform(
            lambda s: s.rolling(20, min_periods=20).mean()
        )

        latest_date = df["date"].max()
        latest = df[df["date"] == latest_date].dropna(subset=["pct_change"]).copy()
        if latest.empty:
            return []

        # 横向：先在全市场范围内计算 RPS 百分位，再叠加趋势与流动性过滤
        latest["rps"] = latest["pct_change"].rank(pct=True) * 100
        selected_df = latest[
            (latest["rps"] >= threshold)
            & (latest["close"] >= latest["ma50"])
            & (latest["dollar_vol20"] >= min_dollar_volume)
        ].sort_values("rps", ascending=False)

        selected = selected_df["symbol"].tolist()
        logger.info(f"UsRpsMomentumStrategy 选出 {len(selected)} 只美股")
        return selected
