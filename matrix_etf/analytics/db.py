"""绩效分析数据库层：独立的 ``matrix_analytics.db`` 引擎与建表逻辑。

与 ETF / A 股 / 美股三个行情库物理隔离，前向跟踪表：
    - ``strategy_signal``：信号台账（一次选股一条标的一行）
    - ``signal_evaluation``：单信号在各持有期的兑现表现
    - ``strategy_scorecard``：策略级聚合评分
    - ``benchmark_daily``：基准行情缓存
历史模拟独立使用 ``backtest_run/result/equity/trade``，不混入前向样本。

表结构详见 docs/analytics.md 第 4 节。
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)


_CREATE_SIGNAL_SQL = """
CREATE TABLE IF NOT EXISTS strategy_signal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date            TEXT NOT NULL,
    market              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    entry_date          TEXT,
    entry_price         REAL,
    suggested_hold_days INTEGER,
    webhook_key         TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE (run_date, market, strategy, symbol)
);
"""

_CREATE_SIGNAL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_signal_strategy
    ON strategy_signal (market, strategy, run_date);
"""

_CREATE_EVALUATION_SQL = """
CREATE TABLE IF NOT EXISTS signal_evaluation (
    signal_id      INTEGER NOT NULL,
    horizon_days   INTEGER NOT NULL,
    as_of_date     TEXT NOT NULL,
    exit_price     REAL,
    ret            REAL,
    benchmark_ret  REAL,
    excess_ret     REAL,
    status         TEXT NOT NULL,
    PRIMARY KEY (signal_id, horizon_days)
);
"""

_CREATE_SCORECARD_SQL = """
CREATE TABLE IF NOT EXISTS strategy_scorecard (
    market          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    as_of_date      TEXT NOT NULL,
    window_days     INTEGER NOT NULL,
    sample_size     INTEGER,
    total_return    REAL,
    ann_return      REAL,
    excess_alpha    REAL,
    max_drawdown    REAL,
    win_rate        REAL,
    sharpe          REAL,
    sortino         REAL,
    composite_score REAL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (market, strategy, as_of_date, window_days)
);
"""

_CREATE_BENCHMARK_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_daily (
    benchmark  TEXT NOT NULL,
    date       TEXT NOT NULL,
    close      REAL NOT NULL,
    PRIMARY KEY (benchmark, date)
);
"""

_CREATE_BACKTEST_SQL = """
CREATE TABLE IF NOT EXISTS backtest_run (
    run_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    config_json TEXT NOT NULL,
    report TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_result (
    run_id TEXT NOT NULL,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    total_return REAL,
    ann_return REAL,
    max_drawdown REAL,
    trade_count INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, strategy)
);
CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    date TEXT NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    PRIMARY KEY (run_id, strategy, date)
);
CREATE TABLE IF NOT EXISTS backtest_trade (
    run_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    trade_index INTEGER NOT NULL,
    trade_json TEXT NOT NULL,
    PRIMARY KEY (run_id, strategy, trade_index)
);
"""


class AnalyticsEngine:
    """绩效分析独立数据库引擎（``matrix_analytics.db``）。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.analytics_db_path
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_SIGNAL_SQL)
            conn.execute(_CREATE_SIGNAL_INDEX_SQL)
            conn.execute(_CREATE_EVALUATION_SQL)
            conn.execute(_CREATE_SCORECARD_SQL)
            conn.execute(_CREATE_BENCHMARK_SQL)
            conn.executescript(_CREATE_BACKTEST_SQL)
            conn.commit()
        logger.info(f"绩效分析数据库初始化完成：{self.db_path}")

    @contextmanager
    def connect(self):
        """提供一个自动提交/关闭的 sqlite3 连接上下文。"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
