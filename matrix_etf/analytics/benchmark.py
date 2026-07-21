"""基准行情缓存：拉取并缓存对比基准（沪深300 / 标普500）的日收盘。

复用 tickflow 客户端与限流重试逻辑，将基准日线缓存进 ``benchmark_daily`` 表，
供前向评估计算同期超额收益，避免每次评估重复请求数据源。
"""

import pandas as pd

from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger
from matrix_etf.data.rate_limit import call_with_retry
from matrix_etf.data.tickflow_client import create_tickflow_client

logger = get_logger(__name__)

# 单次拉取的日 K 根数上限（tickflow 单次上限 10000），足够覆盖数年基准历史。
_BENCHMARK_COUNT = 3000


class BenchmarkStore:
    """基准日线缓存器。"""

    def __init__(
        self,
        analytics_engine: AnalyticsEngine,
        settings: Settings,
        client=None,
    ) -> None:
        self.db = analytics_engine
        self.settings = settings
        self._client = client  # 允许注入（测试）；否则惰性创建

    def _tf(self):
        if self._client is None:
            self._client = create_tickflow_client(self.settings.tickflow_api_key, logger)
        return self._client

    def sync(self, benchmark: str, count: int = _BENCHMARK_COUNT) -> int:
        """拉取并缓存单个基准的日收盘，返回写入行数。异常仅记录日志，返回 0。"""
        tf = self._tf()
        try:
            df = call_with_retry(
                lambda: tf.klines.get(
                    benchmark, period="1d", count=count, as_dataframe=True
                ),
                attempts=self.settings.sync_retry_attempts,
                base_delay=self.settings.sync_retry_base_delay,
                max_delay=self.settings.sync_retry_max_delay,
                logger=logger,
                what=f"基准日 K[{benchmark}]",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"基准 {benchmark} 拉取失败：{exc}")
            return 0

        if df is None or len(df) == 0:
            logger.warning(f"基准 {benchmark} 无数据返回")
            return 0

        rows = [
            (benchmark, str(row["trade_date"]), float(row["close"]))
            for _, row in df.iterrows()
            if pd.notna(row.get("close"))
        ]
        with self.db.connect() as conn:
            conn.executemany(
                """
                INSERT INTO benchmark_daily (benchmark, date, close)
                VALUES (?, ?, ?)
                ON CONFLICT(benchmark, date) DO UPDATE SET close = excluded.close
                """,
                rows,
            )
        logger.info(f"基准 {benchmark} 缓存 {len(rows)} 条日线")
        return len(rows)

    def get_series(self, benchmark: str) -> pd.Series:
        """返回某基准的收盘序列（index 为日期字符串，升序）。"""
        with self.db.connect() as conn:
            df = pd.read_sql(
                "SELECT date, close FROM benchmark_daily WHERE benchmark = ? ORDER BY date",
                conn,
                params=(benchmark,),
            )
        if df.empty:
            return pd.Series(dtype="float64")
        return df.set_index("date")["close"]

    @staticmethod
    def _return_between(series: pd.Series, start_date: str, end_date: str) -> float | None:
        """基于收盘序列计算 [start_date, end_date] 区间收益。

        入场取 start_date 当日或其后首个可得交易日的收盘，出场取 end_date 当日或
        其前最后一个可得交易日的收盘。数据不足时返回 None。
        """
        if series.empty:
            return None
        start_slice = series[series.index >= start_date]
        end_slice = series[series.index <= end_date]
        if start_slice.empty or end_slice.empty:
            return None
        start_close = float(start_slice.iloc[0])
        end_close = float(end_slice.iloc[-1])
        if start_close == 0.0:
            return None
        return end_close / start_close - 1.0

    def return_between(self, benchmark: str, start_date: str, end_date: str) -> float | None:
        """计算某基准在 [start_date, end_date] 的区间收益（缓存缺失返回 None）。"""
        return self._return_between(self.get_series(benchmark), start_date, end_date)
