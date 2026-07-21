"""信号台账：把每次选股结果幂等落库为 ``strategy_signal``。

这是整个绩效模块的地基——没有落库的历史信号，就无法在日后计算其真实兑现收益。
落库时**只记录标的与运行日**，不写入场价：真实可执行的入场点是运行日的下一个
交易日开盘（T+1），由 ``forward.ForwardEvaluator`` 在行情到位后回填，避免前视偏差。
"""

from datetime import datetime

from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)


_INSERT_SIGNAL_SQL = """
INSERT OR IGNORE INTO strategy_signal
    (run_date, market, strategy, symbol, suggested_hold_days, webhook_key, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?);
"""


class SignalStore:
    """选股信号落库器（幂等）。"""

    def __init__(self, analytics_engine: AnalyticsEngine) -> None:
        self.db = analytics_engine

    def record(
        self,
        run_date: str,
        market: str,
        strategy: str,
        symbols: list[str],
        suggested_hold_days: int | None = None,
        webhook_key: str | None = None,
    ) -> int:
        """将一次选股结果落库。

        依赖 ``UNIQUE(run_date, market, strategy, symbol)`` 保证幂等：同一天同一策略
        重复运行不会重复入库（``INSERT OR IGNORE``）。

        Args:
            run_date: 选股运行日（信号产生日），格式 YYYY-MM-DD。
            market: 市场标识，'ETF' / 'CN' / 'US'。
            strategy: 策略类名，如 'RpsBreakoutStrategy'。
            symbols: 本次选中的标的代码列表。
            suggested_hold_days: 该策略建议持有天数（交易日）。
            webhook_key: 冗余记录来源机器人标识，便于溯源。

        Returns:
            实际新增入库的信号条数（已存在的重复项不计入）。
        """
        if not symbols:
            return 0

        created_at = datetime.now().isoformat(timespec="seconds")
        rows = [
            (run_date, market, strategy, symbol, suggested_hold_days, webhook_key, created_at)
            for symbol in symbols
        ]
        with self.db.connect() as conn:
            before = conn.total_changes
            conn.executemany(_INSERT_SIGNAL_SQL, rows)
            inserted = conn.total_changes - before
        logger.info(
            f"信号落库 [{market}/{strategy}] {run_date}：新增 {inserted}/{len(symbols)} 条"
        )
        return inserted

    def signals_needing_entry(self, as_of_date: str) -> list[dict]:
        """返回尚未回填入场价、且入场日（run_date 之后）不晚于 as_of_date 的信号。"""
        with self.db.connect() as conn:
            conn.row_factory = None
            rows = conn.execute(
                """
                SELECT id, run_date, market, strategy, symbol, suggested_hold_days
                FROM strategy_signal
                WHERE entry_price IS NULL AND run_date < ?
                ORDER BY run_date
                """,
                (as_of_date,),
            ).fetchall()
        cols = ["id", "run_date", "market", "strategy", "symbol", "suggested_hold_days"]
        return [dict(zip(cols, row)) for row in rows]

    def evaluable_signals(self) -> list[dict]:
        """返回已回填入场价、可用于兑现收益评估的信号。"""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, run_date, market, strategy, symbol,
                       entry_date, entry_price, suggested_hold_days
                FROM strategy_signal
                WHERE entry_price IS NOT NULL
                ORDER BY run_date
                """
            ).fetchall()
        cols = [
            "id", "run_date", "market", "strategy", "symbol",
            "entry_date", "entry_price", "suggested_hold_days",
        ]
        return [dict(zip(cols, row)) for row in rows]

    def set_entry(self, signal_id: int, entry_date: str, entry_price: float) -> None:
        """回填某信号的入场日与入场价（T+1 开盘）。"""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE strategy_signal SET entry_date = ?, entry_price = ? WHERE id = ?",
                (entry_date, entry_price, signal_id),
            )
