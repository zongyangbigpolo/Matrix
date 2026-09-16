"""美股相对强度动量策略：横截面 RPS 排位 + 趋势与流动性过滤。"""

import sqlite3

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.base import BaseStrategy
from matrix_etf.strategy.history import iter_stock_histories

logger = get_logger(__name__)


class UsRpsMomentumStrategy(BaseStrategy):
    """美股相对强度动量策略（欧奈尔 RPS 思路）。

    流式读取全市场日 K，只将各标的最新指标用于横截面排位：

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
                latest_row = conn.execute(
                    "SELECT MAX(date) FROM stock_daily"
                ).fetchone()
                latest_str = latest_row[0] if latest_row else None
                if not latest_str:
                    return []

                # 只读计算所需的最近窗口，避免把全市场 5 年日 K 一次性载入内存
                # （12021 只 × 数年 ≈ 千万行，小内存机器会 OOM）。RPS 需要 period 天
                # 做区间涨幅 + MA50，故取 period + 60 个交易日冗余，再按 ~1.6 倍换算成
                # 日历天数以覆盖周末与假期。
                lookback_days = int((period + 60) * 1.6)
                cutoff = (
                    pd.Timestamp(latest_str) - pd.Timedelta(days=lookback_days)
                ).strftime("%Y-%m-%d")
                records = []
                for symbol, frame in iter_stock_histories(conn, cutoff):
                    if frame["date"].iloc[-1] != latest_str or len(frame) <= period:
                        continue
                    close = frame["close"]
                    base = close.iloc[-1 - period]
                    ret = (close.iloc[-1] - base) / base
                    records.append({
                        "symbol": symbol,
                        "close": close.iloc[-1],
                        "pct_change": ret,
                        "ma50": close.rolling(50, min_periods=50).mean().iloc[-1],
                        "dollar_vol20": (close * frame["volume"]).rolling(
                            20, min_periods=20
                        ).mean().iloc[-1],
                    })
        except Exception as exc:  # noqa: BLE001
            logger.error(f"读取美股数据库失败：{exc}")
            return []

        if not records:
            return []
        latest = pd.DataFrame(records).dropna(subset=["pct_change"])
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
