"""策略中英文名称映射。

集中维护「策略类名（英文）→ 中文展示名」的映射，供飞书推送卡片等展示层
统一使用，避免在多处硬编码中文名。日志仍使用英文类名以便调试。

新增策略时，在 ``STRATEGY_DISPLAY_NAMES`` 中补充一条映射即可；未登记的类名
会通过 :func:`get_strategy_display_name` 原样回退，不会报错。
"""

# 键为策略类名（``type(strategy).__name__``），值为中文展示名。
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    # ── ETF 策略 ──
    "RpsMomentumStrategy": "相对强度动量",
    "TrendMaStrategy": "均线趋势",
    "BreakoutVolumeStrategy": "放量突破",
    "MeanReversionStrategy": "强势回踩",
    "RiskAdjustedMomentumStrategy": "风险调整动量",
    "VolumeConfirmedMomentumStrategy": "成交额确认动量",
    "LowVolTrendRotationStrategy": "低波趋势轮动",
    # ── 股票策略 ──
    "MaVolumeStrategy": "均线放量",
    "TurtleTradeStrategy": "海龟突破",
    "HighTightFlagStrategy": "高旗形整理",
    "LimitUpShakeoutStrategy": "涨停洗盘",
    "UptrendLimitDownStrategy": "上升趋势跌停",
    "RpsBreakoutStrategy": "RPS动量突破",
    # ── 美股策略 ──
    "UsRpsMomentumStrategy": "美股相对强度动量",
    "UsTrendMaStrategy": "美股均线趋势",
    "UsMaVolumeStrategy": "美股均线放量",
    "UsBreakoutVolumeStrategy": "美股放量突破",
}


def get_strategy_display_name(name: str) -> str:
    """返回策略类名对应的中文展示名；未登记时原样返回英文类名。

    Args:
        name: 策略类名，通常为 ``type(strategy).__name__``。

    Returns:
        对应的中文展示名；若未在映射表中登记，则返回传入的原始名称。
    """
    return STRATEGY_DISPLAY_NAMES.get(name, name)
