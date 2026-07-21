"""绩效指标纯函数库（无 IO，便于精确单测与 hypothesis 性质测试）。

统一以「逐笔交易收益序列」为输入口径：每条已到期信号视为一笔交易，其在建议
持有期上的兑现收益为一个样本。年化相关指标通过 ``periods_per_year`` 换算，
其中 ``periods_per_year = 交易日/年 ÷ 平均持有天数``。

约定：
    - 序列需按时间（入场日）升序排列，最大回撤据此构造净值曲线。
    - 退化输入（空序列、样本不足、零波动）返回 0.0 而非抛异常或 NaN，
      以便上层评分卡稳健处理。
"""

from collections.abc import Sequence

import numpy as np

# 判定「零波动/零下行波动」的数值容差：常数序列的样本标准差因浮点误差
# 可能是 ~1e-18 而非精确 0，用容差避免夏普/Sortino 被放大成天文数字。
_EPS = 1e-12


def _to_array(returns: Sequence[float]) -> np.ndarray:
    """转为一维 float 数组并丢弃 NaN。"""
    arr = np.asarray(list(returns), dtype="float64")
    return arr[~np.isnan(arr)]


def compound_return(returns: Sequence[float]) -> float:
    """逐笔收益按顺序复利后的累计收益：``∏(1+r) − 1``。"""
    arr = _to_array(returns)
    if arr.size == 0:
        return 0.0
    return float(np.prod(1.0 + arr) - 1.0)


def mean_return(returns: Sequence[float]) -> float:
    """逐笔收益的算术平均。"""
    arr = _to_array(returns)
    if arr.size == 0:
        return 0.0
    return float(arr.mean())


def win_rate(returns: Sequence[float]) -> float:
    """胜率：收益为正的交易占比，取值 [0, 1]。"""
    arr = _to_array(returns)
    if arr.size == 0:
        return 0.0
    return float((arr > 0).mean())


def annualized_return(mean_per_trade: float, periods_per_year: float) -> float:
    """由单笔平均收益按每年交易笔数复利年化：``(1+mean)^ppy − 1``。

    当 ``1 + mean_per_trade <= 0``（极端亏损）时，直接返回 -1.0 避免复数幂。
    """
    base = 1.0 + mean_per_trade
    if base <= 0.0:
        return -1.0
    if periods_per_year <= 0.0:
        return 0.0
    return float(base**periods_per_year - 1.0)


def sharpe_ratio(
    returns: Sequence[float],
    periods_per_year: float,
    risk_free_annual: float = 0.0,
) -> float:
    """夏普比率：``(mean − rf_per) / std × √ppy``。

    ``rf_per`` 为按 ``periods_per_year`` 折算到单笔口径的无风险收益。
    波动为 0 或样本 < 2 时返回 0.0。
    """
    arr = _to_array(returns)
    if arr.size < 2 or periods_per_year <= 0.0:
        return 0.0
    std = arr.std(ddof=1)
    if std < _EPS:
        return 0.0
    rf_per = risk_free_annual / periods_per_year
    return float((arr.mean() - rf_per) / std * np.sqrt(periods_per_year))


def sortino_ratio(
    returns: Sequence[float],
    periods_per_year: float,
    risk_free_annual: float = 0.0,
) -> float:
    """Sortino 比率：只用下行波动作分母，惩罚亏损而不惩罚上行波动。

    下行标准差 = ``√(mean(min(r − target, 0)²))``，target 取单笔无风险收益。
    无下行波动（从未跌破 target）或样本 < 2 时返回 0.0。
    """
    arr = _to_array(returns)
    if arr.size < 2 or periods_per_year <= 0.0:
        return 0.0
    rf_per = risk_free_annual / periods_per_year
    downside = np.minimum(arr - rf_per, 0.0)
    downside_dev = np.sqrt(np.mean(downside**2))
    if downside_dev < _EPS:
        return 0.0
    return float((arr.mean() - rf_per) / downside_dev * np.sqrt(periods_per_year))


def max_drawdown(returns: Sequence[float]) -> float:
    """最大回撤（负数）：按序复利成净值曲线后的最深回撤。

    序列应按时间升序。空序列返回 0.0；从无回撤时返回 0.0。
    """
    arr = _to_array(returns)
    if arr.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + arr)
    running_max = np.maximum.accumulate(equity)
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())
