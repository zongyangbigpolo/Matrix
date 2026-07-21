"""策略建议持有天数（交易日口径）集中映射。

为绩效评估（analytics 模块）提供每个策略的默认建议持有期。与 ``names.py`` 的
中文名映射同理，集中维护避免在多处硬编码。取值须落在
``Settings.analytics_horizons``（默认 5/10/20/60）之内，否则评分卡会就近取档。

风格与持有期对应关系（详见 docs/analytics.md 第 11 节）：
    - 动量 / RPS：中周期，20 日
    - 趋势 / 均线：中周期，20 日
    - 突破：偏短，10 日
    - 均值回归 / 短线洗盘：短，5 日
"""

# 键为策略类名（``type(strategy).__name__``），值为建议持有交易日数。
SUGGESTED_HOLD_DAYS: dict[str, int] = {
    # ── ETF 策略 ──
    "RpsMomentumStrategy": 20,
    "TrendMaStrategy": 20,
    "BreakoutVolumeStrategy": 10,
    "MeanReversionStrategy": 5,
    "RiskAdjustedMomentumStrategy": 20,
    "VolumeConfirmedMomentumStrategy": 20,
    "LowVolTrendRotationStrategy": 20,
    # ── A 股策略 ──
    "MaVolumeStrategy": 20,
    "TurtleTradeStrategy": 10,
    "HighTightFlagStrategy": 10,
    "LimitUpShakeoutStrategy": 5,
    "UptrendLimitDownStrategy": 10,
    "RpsBreakoutStrategy": 20,
    # ── 美股策略 ──
    "UsRpsMomentumStrategy": 20,
    "UsTrendMaStrategy": 20,
    "UsMaVolumeStrategy": 20,
    "UsBreakoutVolumeStrategy": 10,
}

# 查表缺省时的兜底持有期（交易日）。
DEFAULT_HOLD_DAYS = 20


def get_suggested_hold_days(class_name: str) -> int:
    """返回策略类名对应的建议持有天数；未登记时回退 ``DEFAULT_HOLD_DAYS``。"""
    return SUGGESTED_HOLD_DAYS.get(class_name, DEFAULT_HOLD_DAYS)


def resolve_hold_days(strategy) -> int:
    """解析某策略实例的建议持有天数。

    优先使用实例/类上显式声明的 ``suggested_hold_days``（子类可覆盖），
    未声明（None）时按类名回退到集中映射表。

    Args:
        strategy: 策略实例，通常继承自 ``BaseStrategy``。

    Returns:
        建议持有的交易日数。
    """
    explicit = getattr(strategy, "suggested_hold_days", None)
    if explicit is not None:
        return int(explicit)
    return get_suggested_hold_days(type(strategy).__name__)
