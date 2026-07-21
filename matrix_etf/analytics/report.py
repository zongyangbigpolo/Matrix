"""评分卡只读查询：供飞书卡片增强读取某策略最近战绩。

所有函数都容错：查不到 / 出错时返回 None 或空串，绝不影响选股推送主流程。
"""

from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)


def get_latest_scorecard(
    analytics_engine: AnalyticsEngine,
    market: str,
    strategy: str,
    window_days: int = 90,
) -> dict | None:
    """返回某 (市场, 策略, 窗口) 最新一期评分卡；查不到返回 None。"""
    try:
        with analytics_engine.connect() as conn:
            row = conn.execute(
                """
                SELECT market, strategy, as_of_date, window_days, sample_size,
                       total_return, ann_return, excess_alpha, max_drawdown,
                       win_rate, sharpe, sortino, composite_score
                FROM strategy_scorecard
                WHERE market = ? AND strategy = ? AND window_days = ?
                ORDER BY as_of_date DESC
                LIMIT 1
                """,
                (market, strategy, window_days),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"评分卡查询失败：{exc}")
        return None
    if row is None:
        return None
    cols = [
        "market", "strategy", "as_of_date", "window_days", "sample_size",
        "total_return", "ann_return", "excess_alpha", "max_drawdown",
        "win_rate", "sharpe", "sortino", "composite_score",
    ]
    return dict(zip(cols, row))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def format_scorecard_line(card: dict | None) -> str | None:
    """把评分卡格式化为卡片底部一行战绩文案；样本不足或无卡则返回 None。"""
    if not card or not card.get("sample_size"):
        return None

    window = card.get("window_days", 90)
    ann = _fmt_pct(card.get("ann_return"))
    excess = _fmt_pct(card.get("excess_alpha"))
    win = card.get("win_rate")
    win_txt = f"{win * 100:.0f}%" if win is not None else "—"
    sharpe = _fmt_num(card.get("sharpe"))
    score = card.get("composite_score")
    score_txt = f"{score:.0f}" if score is not None else "样本不足"
    n = card.get("sample_size")

    return (
        f"📊 该策略近{window}日战绩：年化 {ann} | 超额 {excess} | "
        f"胜率 {win_txt} | 夏普 {sharpe} | 评分 {score_txt}\n"
        f"（基于 {n} 条历史信号的真实兑现收益）"
    )


def build_perf_line(
    analytics_engine: AnalyticsEngine,
    market: str,
    strategy: str,
    window_days: int = 90,
) -> str | None:
    """一步获取某策略最近战绩文案；任何异常都吞掉返回 None。"""
    try:
        card = get_latest_scorecard(analytics_engine, market, strategy, window_days)
        return format_scorecard_line(card)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"战绩文案生成失败：{exc}")
        return None
