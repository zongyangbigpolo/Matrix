"""前向兑现收益评估器（轻量 pandas，可上服务器每日运行）。

随行情推进，为每条已落库信号：
    1. 回填入场（T+1 开盘）——真实可执行入场点，避免前视偏差。
    2. 在各持有期上计算真实兑现收益，并对比同期基准得到超额，写入
       ``signal_evaluation``，未到期标 ``open``、已到期标 ``closed``。

内存友好：逐只标的调用 ``engine.get_ohlcv`` 读取，绝不全表载入。
"""

from datetime import date

import pandas as pd

from matrix_etf.analytics.benchmark import BenchmarkStore
from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.analytics.signals import SignalStore
from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)


_UPSERT_EVAL_SQL = """
INSERT INTO signal_evaluation
    (signal_id, horizon_days, as_of_date, exit_price, ret, benchmark_ret, excess_ret, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(signal_id, horizon_days) DO UPDATE SET
    as_of_date    = excluded.as_of_date,
    exit_price    = excluded.exit_price,
    ret           = excluded.ret,
    benchmark_ret = excluded.benchmark_ret,
    excess_ret    = excluded.excess_ret,
    status        = excluded.status;
"""


class ForwardEvaluator:
    """把落库信号推进为带兑现收益的评估记录。"""

    def __init__(
        self,
        analytics_engine: AnalyticsEngine,
        signal_store: SignalStore,
        benchmark_store: BenchmarkStore,
        engines: dict[str, object],
        settings: Settings,
    ) -> None:
        self.db = analytics_engine
        self.signals = signal_store
        self.benchmark = benchmark_store
        self.engines = engines  # {'ETF': ..., 'CN': ..., 'US': ...}
        self.settings = settings
        self.horizons = settings.get_analytics_horizons()

    # ── 入场回填 ──

    def backfill_entries(self, as_of_date: str) -> int:
        """为尚无入场价的信号回填 T+1 开盘价，返回成功回填条数。"""
        pending = self.signals.signals_needing_entry(as_of_date)
        filled = 0
        for sig in pending:
            engine = self.engines.get(sig["market"])
            if engine is None:
                continue
            try:
                ohlcv = engine.get_ohlcv(sig["symbol"])
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{sig['symbol']}] 读取行情失败，跳过入场回填：{exc}")
                continue
            entry = self._first_after(ohlcv, sig["run_date"])
            if entry is None:
                continue  # T+1 行情尚未到位，下轮再试
            entry_date, entry_price = entry
            if entry_price is None:
                continue
            self.signals.set_entry(sig["id"], entry_date, entry_price)
            filled += 1
        if filled:
            logger.info(f"入场回填完成：{filled}/{len(pending)} 条")
        return filled

    @staticmethod
    def _first_after(ohlcv: pd.DataFrame, run_date: str) -> tuple[str, float | None] | None:
        """返回 run_date 之后首个交易日的 (date, open)；无则 None。"""
        if ohlcv is None or ohlcv.empty:
            return None
        after = ohlcv[ohlcv["date"] > run_date]
        if after.empty:
            return None
        row = after.iloc[0]
        open_price = row.get("open")
        price = float(open_price) if pd.notna(open_price) else None
        return str(row["date"]), price

    # ── 兑现收益评估 ──

    def evaluate(self, as_of_date: str | None = None) -> int:
        """推进所有可评估信号的各持有期兑现收益，返回处理的信号数。"""
        as_of_date = as_of_date or date.today().isoformat()
        self.backfill_entries(as_of_date)

        signals = self.signals.evaluable_signals()
        closed_map = self._fully_closed_signal_ids()
        processed = 0
        for sig in signals:
            if sig["id"] in closed_map:
                continue  # 各持有期均已到期定盘，无需重算
            engine = self.engines.get(sig["market"])
            if engine is None:
                continue
            try:
                ohlcv = engine.get_ohlcv(sig["symbol"])
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{sig['symbol']}] 读取行情失败，跳过评估：{exc}")
                continue
            if self._evaluate_signal(sig, ohlcv, as_of_date):
                processed += 1
        logger.info(f"兑现收益评估完成：处理 {processed} 条信号")
        return processed

    def _fully_closed_signal_ids(self) -> set[int]:
        """返回所有持有期都已 closed 的信号 id 集合（可跳过重算）。"""
        want = len(self.horizons)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT signal_id
                FROM signal_evaluation
                WHERE status = 'closed'
                GROUP BY signal_id
                HAVING COUNT(*) >= ?
                """,
                (want,),
            ).fetchall()
        return {row[0] for row in rows}

    def _evaluate_signal(self, sig: dict, ohlcv: pd.DataFrame, as_of_date: str) -> bool:
        """计算单条信号在各持有期的兑现收益并 upsert。"""
        if ohlcv is None or ohlcv.empty:
            return False
        ohlcv = ohlcv.reset_index(drop=True)
        entry_date = sig["entry_date"]
        entry_price = sig["entry_price"]
        idx = ohlcv.index[ohlcv["date"] == entry_date]
        if len(idx) == 0 or not entry_price:
            return False
        i = int(idx[0])
        benchmark = self.settings.get_benchmark_for_market(sig["market"])
        last_pos = len(ohlcv) - 1

        rows = []
        for horizon in self.horizons:
            exit_pos = i + horizon
            if exit_pos <= last_pos:
                status = "closed"
            else:
                exit_pos = last_pos
                status = "open"
            exit_row = ohlcv.iloc[exit_pos]
            exit_close = exit_row.get("close")
            if pd.isna(exit_close):
                continue
            exit_date = str(exit_row["date"])
            exit_price = float(exit_close)
            ret = exit_price / entry_price - 1.0
            bench_ret = self.benchmark.return_between(benchmark, entry_date, exit_date)
            excess = ret - bench_ret if bench_ret is not None else None
            rows.append(
                (sig["id"], horizon, as_of_date, exit_price, ret, bench_ret, excess, status)
            )

        if not rows:
            return False
        with self.db.connect() as conn:
            conn.executemany(_UPSERT_EVAL_SQL, rows)
        return True
