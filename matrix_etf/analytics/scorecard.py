"""策略级评分卡：把逐笔兑现收益聚合为总收益/超额/回撤/胜率/夏普/Sortino 与
0–100 综合评分，写入 ``strategy_scorecard``。

评分口径以策略「建议持有期」对应的那一档兑现收益为准（就近对齐到已评估的持有期）。
样本不足（< ``analytics_min_samples``）时综合评分记 NULL，避免误导性高分。
"""

from datetime import date, datetime, timedelta

from matrix_etf.analytics import metrics
from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.hold_days import get_suggested_hold_days

logger = get_logger(__name__)

# 综合评分各维度的归一化边界 (lo, hi)：低于 lo 记 0 分，高于 hi 记满分。
_NORM_BOUNDS = {
    "ann_return": (-0.20, 0.60),
    "excess_alpha": (-0.20, 0.40),
    "sharpe": (-0.5, 2.5),
    "sortino": (-0.5, 3.5),
    "max_drawdown": (-0.40, 0.0),
}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _norm(x: float, lo: float, hi: float) -> float:
    """线性归一到 [0,1]；hi<=lo 时返回 0。"""
    if hi <= lo:
        return 0.0
    return _clip((x - lo) / (hi - lo), 0.0, 1.0)


def _snap_to_horizon(hold_days: int, horizons: list[int]) -> int:
    """把建议持有天数就近对齐到已评估的持有期之一。"""
    if not horizons:
        return hold_days
    if hold_days in horizons:
        return hold_days
    return min(horizons, key=lambda h: abs(h - hold_days))


_UPSERT_SCORECARD_SQL = """
INSERT INTO strategy_scorecard
    (market, strategy, as_of_date, window_days, sample_size, total_return, ann_return,
     excess_alpha, max_drawdown, win_rate, sharpe, sortino, composite_score, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(market, strategy, as_of_date, window_days) DO UPDATE SET
    sample_size     = excluded.sample_size,
    total_return    = excluded.total_return,
    ann_return      = excluded.ann_return,
    excess_alpha    = excluded.excess_alpha,
    max_drawdown    = excluded.max_drawdown,
    win_rate        = excluded.win_rate,
    sharpe          = excluded.sharpe,
    sortino         = excluded.sortino,
    composite_score = excluded.composite_score,
    updated_at      = excluded.updated_at;
"""


class ScorecardBuilder:
    """把 ``signal_evaluation`` 聚合成 ``strategy_scorecard``。"""

    def __init__(self, analytics_engine: AnalyticsEngine, settings: Settings) -> None:
        self.db = analytics_engine
        self.settings = settings
        self.horizons = settings.get_analytics_horizons()
        self.weights = settings.get_score_weights()

    def build_all(self, as_of_date: str | None = None) -> int:
        """为所有 (市场, 策略) × 统计窗口构建评分卡，返回写入行数。"""
        as_of_date = as_of_date or date.today().isoformat()
        pairs = self._distinct_strategies()
        written = 0
        for market, strategy in pairs:
            for window in self.settings.get_analytics_windows():
                if self.build(market, strategy, as_of_date, window):
                    written += 1
        logger.info(f"评分卡构建完成：写入 {written} 行")
        return written

    def _distinct_strategies(self) -> list[tuple[str, str]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT market, strategy FROM strategy_signal"
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def _closed_trades(
        self, market: str, strategy: str, horizon: int, window_start: str
    ) -> list[tuple[float, float | None]]:
        """返回窗口内该策略在指定持有期已定盘的 (ret, excess_ret)，按入场日升序。"""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.ret, e.excess_ret
                FROM strategy_signal s
                JOIN signal_evaluation e ON s.id = e.signal_id
                WHERE s.market = ? AND s.strategy = ? AND e.horizon_days = ?
                  AND e.status = 'closed' AND s.run_date >= ?
                ORDER BY s.entry_date
                """,
                (market, strategy, horizon, window_start),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def build(
        self, market: str, strategy: str, as_of_date: str, window_days: int
    ) -> bool:
        """构建单个 (市场, 策略, 窗口) 评分卡。无样本则不写，返回是否写入。"""
        hold_days = get_suggested_hold_days(strategy)
        horizon = _snap_to_horizon(hold_days, self.horizons)
        window_start = (
            datetime.fromisoformat(as_of_date) - timedelta(days=window_days)
        ).strftime("%Y-%m-%d")

        trades = self._closed_trades(market, strategy, horizon, window_start)
        if not trades:
            return False

        rets = [t[0] for t in trades]
        excess = [t[1] for t in trades if t[1] is not None]

        ppy = self.settings.analytics_trading_days / max(1, hold_days)
        rf = self.settings.analytics_risk_free

        total_return = metrics.compound_return(rets)
        mean_ret = metrics.mean_return(rets)
        ann_return = metrics.annualized_return(mean_ret, ppy)
        win = metrics.win_rate(rets)
        mdd = metrics.max_drawdown(rets)
        sharpe = metrics.sharpe_ratio(rets, ppy, rf)
        sortino = metrics.sortino_ratio(rets, ppy, rf)

        excess_alpha = self._excess_alpha(rets, excess, ppy)

        sample_size = len(rets)
        composite = self._composite(
            ann_return, excess_alpha, sharpe, sortino, win, mdd
        ) if sample_size >= self.settings.analytics_min_samples else None

        updated_at = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute(
                _UPSERT_SCORECARD_SQL,
                (
                    market, strategy, as_of_date, window_days, sample_size,
                    total_return, ann_return, excess_alpha, mdd, win,
                    sharpe, sortino, composite, updated_at,
                ),
            )
        return True

    def _excess_alpha(
        self, rets: list[float], excess: list[float], ppy: float
    ) -> float | None:
        """年化超额 = 年化策略收益 − 年化基准收益（缺基准数据时返回 None）。"""
        if not excess:
            return None
        mean_ret = metrics.mean_return(rets)
        mean_excess = metrics.mean_return(excess)
        mean_bench = mean_ret - mean_excess
        ann_strategy = metrics.annualized_return(mean_ret, ppy)
        ann_bench = metrics.annualized_return(mean_bench, ppy)
        return ann_strategy - ann_bench

    def _composite(
        self,
        ann_return: float,
        excess_alpha: float | None,
        sharpe: float,
        sortino: float,
        win_rate: float,
        max_drawdown: float,
    ) -> float:
        """六维加权归一到 0–100 综合评分。缺失超额时该维取中性 0.5。"""
        w = self.weights
        excess_norm = (
            _norm(excess_alpha, *_NORM_BOUNDS["excess_alpha"])
            if excess_alpha is not None else 0.5
        )
        raw = (
            w["ann_return"] * _norm(ann_return, *_NORM_BOUNDS["ann_return"])
            + w["excess_alpha"] * excess_norm
            + w["sharpe"] * _norm(sharpe, *_NORM_BOUNDS["sharpe"])
            + w["sortino"] * _norm(sortino, *_NORM_BOUNDS["sortino"])
            + w["win_rate"] * _clip(win_rate, 0.0, 1.0)
            + w["max_drawdown"] * _norm(max_drawdown, *_NORM_BOUNDS["max_drawdown"])
        )
        return round(100.0 * _clip(raw, 0.0, 1.0), 1)
