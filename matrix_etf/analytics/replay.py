"""历史回放（replay）：无前视偏差地重建过去若干交易日的选股信号。

前向跟踪模块只对「运行时已落库」的信号计算兑现收益；刚上线时台账为空，需要
等每天跑积累。本模块提供一次性「历史回放」：让每个策略**假装回到过去某个交易日**
重新选一次股，据此补齐历史信号，之后即可用 ``ForwardEvaluator`` 计算其真实兑现收益。

核心：**杜绝前视偏差**。做法是把行情引擎的 ``db_path`` 临时指向一个「日期封顶」的
临时库副本——副本里只保留 ``date <= 回放日`` 的日 K，于是：

* 逐只读取（``engine.get_ohlcv``）只看得到回放日及以前的数据；
* 横截面 RPS 策略（直接 ``sqlite3.connect(engine.db_path)`` 读全表）同样被封顶，
  其 ``MAX(date)`` 即回放日。

选股（重建信号）用封顶库；随后的兑现收益评估用**真实库**读回放日之后的价格——
那是合法的「未来实现」，不是前视。副本按回放日从新到旧递减，逐步 ``DELETE`` 掉更晚
的行，一次拷贝 + 若干次轻量删除即可，内存友好（副本落盘，逐只读取不整表载入）。
"""

import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager

from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.analytics.signals import SignalStore
from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.hold_days import resolve_hold_days

logger = get_logger(__name__)

# 市场 -> (日K表名, 基础信息表名)。用于按市场定位封顶库要裁剪的表。
MARKET_TABLES: dict[str, tuple[str, str]] = {
    "ETF": ("etf_daily", "etf_basic"),
    "CN": ("stock_daily", "stock_basic"),
    "US": ("stock_daily", "stock_basic"),
}


def get_as_of_dates(db_path: str, daily_table: str, days: int) -> list[str]:
    """返回日 K 表中最近 ``days`` 个交易日（升序），作为回放的 as-of 日期。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT DISTINCT date FROM {daily_table} ORDER BY date DESC LIMIT ?",  # noqa: S608
            (days,),
        ).fetchall()
    return sorted(row[0] for row in rows if row[0])


def _shrink_to(db_path: str, daily_table: str, as_of: str) -> None:
    """把封顶库裁剪到 ``date <= as_of``（删除更晚的日 K）。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"DELETE FROM {daily_table} WHERE date > ?",  # noqa: S608
            (as_of,),
        )
        conn.commit()


@contextmanager
def capped_engine_db(engine, daily_table: str):
    """上下文内把 ``engine.db_path`` 指向一个可裁剪的封顶库副本，退出时复原并清理。

    Yields:
        临时封顶库路径（副本，初始为源库全量拷贝，调用方按 as-of 逐步裁剪）。
    """
    src = engine.db_path
    tmp_dir = os.path.dirname(os.path.abspath(src)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix="replay_", suffix=".db", dir=tmp_dir)
    os.close(fd)
    shutil.copyfile(src, tmp_path)
    original = engine.db_path
    engine.db_path = tmp_path
    try:
        yield tmp_path
    finally:
        engine.db_path = original
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(tmp_path + suffix)
            except OSError:
                pass


def replay_market(
    engine,
    strategies: list,
    market: str,
    signal_store: SignalStore,
    days: int,
) -> int:
    """回放单个市场最近 ``days`` 个交易日的选股，落库为历史信号。

    Args:
        engine: 该市场的行情引擎（回放期间其 ``db_path`` 会被临时改写并复原）。
        strategies: 该市场的策略实例列表。
        market: 市场标识 'ETF' / 'CN' / 'US'。
        signal_store: 信号台账（幂等落库）。
        days: 回放的交易日数。

    Returns:
        新增落库的信号条数（幂等，重复回放不重复计数）。
    """
    daily_table, _ = MARKET_TABLES.get(market, (None, None))
    if daily_table is None:
        logger.warning(f"未知市场 {market}，跳过回放。")
        return 0

    as_of_dates = get_as_of_dates(engine.db_path, daily_table, days)
    if not as_of_dates:
        logger.warning(f"[{market}] 行情库无数据，跳过回放。")
        return 0

    logger.info(
        f"[{market}] 开始历史回放：{len(as_of_dates)} 个交易日 "
        f"（{as_of_dates[0]} ~ {as_of_dates[-1]}）× {len(strategies)} 个策略"
    )

    total = 0
    with capped_engine_db(engine, daily_table) as tmp_path:
        # 从最新回放日往旧走，配合 DELETE 增量裁剪封顶库（一次拷贝 + 多次轻量删除）。
        for as_of in sorted(as_of_dates, reverse=True):
            _shrink_to(tmp_path, daily_table, as_of)
            for strategy in strategies:
                name = type(strategy).__name__
                try:
                    picks = strategy.run()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[{market}/{name}] 回放 {as_of} 选股失败，跳过：{exc}")
                    continue
                if not picks:
                    continue
                total += signal_store.record(
                    run_date=as_of,
                    market=market,
                    strategy=name,
                    symbols=picks,
                    suggested_hold_days=resolve_hold_days(strategy),
                    webhook_key=getattr(strategy, "webhook_key", None),
                )
    logger.info(f"[{market}] 回放完成，新增信号 {total} 条。")
    return total


def replay_summary(analytics_engine: AnalyticsEngine) -> list[dict]:
    """汇总各 (市场, 策略, 持有期) 已定盘信号的收益，供回放后即时查看。

    与评分卡不同，这里不设最小样本门槛——即使样本很少也直接展示逐笔平均收益、
    胜率与平均超额，方便短窗口回放后立刻看到「每种策略的收益率」。
    """
    with analytics_engine.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.market, s.strategy, e.horizon_days,
                   COUNT(*)                                          AS n,
                   AVG(e.ret)                                        AS avg_ret,
                   AVG(CASE WHEN e.ret > 0 THEN 1.0 ELSE 0.0 END)    AS win_rate,
                   AVG(e.excess_ret)                                 AS avg_excess
            FROM strategy_signal s
            JOIN signal_evaluation e ON s.id = e.signal_id
            WHERE e.status = 'closed'
            GROUP BY s.market, s.strategy, e.horizon_days
            ORDER BY s.market, s.strategy, e.horizon_days
            """
        ).fetchall()
    cols = ["market", "strategy", "horizon_days", "n", "avg_ret", "win_rate", "avg_excess"]
    return [dict(zip(cols, row)) for row in rows]


def format_summary(rows: list[dict]) -> str:
    """把 ``replay_summary`` 结果格式化为可读文本表。"""
    if not rows:
        return "（暂无已定盘的兑现收益：回放窗口太短或行情未推进到持有期到期）"

    lines = [
        f"{'市场':<4} {'策略':<28} {'持有期':>5} {'样本':>5} "
        f"{'平均收益':>9} {'胜率':>7} {'平均超额':>9}",
        "-" * 78,
    ]
    for r in rows:
        avg_ret = f"{r['avg_ret'] * 100:+.2f}%" if r["avg_ret"] is not None else "  n/a"
        win = f"{r['win_rate'] * 100:.1f}%" if r["win_rate"] is not None else " n/a"
        excess = (
            f"{r['avg_excess'] * 100:+.2f}%" if r["avg_excess"] is not None else "  n/a"
        )
        lines.append(
            f"{r['market']:<4} {r['strategy']:<28} {r['horizon_days']:>4}d "
            f"{r['n']:>5} {avg_ret:>10} {win:>8} {excess:>10}"
        )
    return "\n".join(lines)
