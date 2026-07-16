"""策略中英文名称映射测试。"""

import ast
from pathlib import Path

from matrix_etf.strategy.names import (
    STRATEGY_DISPLAY_NAMES,
    get_strategy_display_name,
)

STRATEGY_DIRS = [
    Path(__file__).resolve().parent.parent / "matrix_etf/strategy/etf",
    Path(__file__).resolve().parent.parent / "matrix_etf/strategy/stock",
]


def _discover_strategy_class_names() -> set[str]:
    """扫描策略目录，收集所有以 Strategy 结尾的公开策略类名。"""
    names: set[str] = set()
    for directory in STRATEGY_DIRS:
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.endswith("Strategy"):
                    # 跳过以下划线开头的内部基类（如 _Mega7BaseStrategy）
                    if not node.name.startswith("_"):
                        names.add(node.name)
    return names


def test_all_concrete_strategies_have_chinese_names() -> None:
    """所有对外策略类都应在映射表中登记中文名，避免推送时漏翻译。"""
    discovered = _discover_strategy_class_names()
    missing = discovered - set(STRATEGY_DISPLAY_NAMES)
    assert not missing, f"以下策略缺少中文名映射：{sorted(missing)}"


def test_get_display_name_translates_and_falls_back() -> None:
    """已登记类名返回中文，未登记类名原样回退。"""
    assert get_strategy_display_name("RpsMomentumStrategy") == "相对强度动量"
    assert get_strategy_display_name("TurtleTradeStrategy") == "海龟突破"
    assert get_strategy_display_name("NotARealStrategy") == "NotARealStrategy"


def test_display_names_are_nonempty_chinese() -> None:
    """映射值应为非空且包含中文字符。"""
    for english, chinese in STRATEGY_DISPLAY_NAMES.items():
        assert chinese.strip(), f"{english} 的中文名为空"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in chinese), (
            f"{english} 的展示名 '{chinese}' 不含中文字符"
        )
