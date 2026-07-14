"""股票主程序入口属性测试。"""

from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

import stock_main as stock_main_module


@given(error_msg=st.text(min_size=1, max_size=100))
@h_settings(max_examples=30, deadline=None)
def test_stock_main_exits_nonzero_on_exception(error_msg: str) -> None:
    """main() 中任意未捕获异常应导致 sys.exit(1)。"""
    with patch.object(
        stock_main_module, "get_settings", side_effect=RuntimeError(error_msg)
    ):
        with pytest.raises(SystemExit) as exc_info:
            stock_main_module.main()
        assert exc_info.value.code != 0


def test_stock_parse_symbols() -> None:
    """--symbols 解析应去空白并忽略空项。"""
    assert stock_main_module._parse_symbols(None) is None
    assert stock_main_module._parse_symbols("") is None
    assert stock_main_module._parse_symbols("600519.SH, 000001.SZ ,") == [
        "600519.SH",
        "000001.SZ",
    ]


def test_stock_build_strategies_covers_six_stock_strategies() -> None:
    """股票策略集合应包含全部 6 个股票策略，且 webhook key 均带 stock_ 前缀。"""
    strategies = stock_main_module._build_strategies(engine=None, settings=None)
    names = {type(s).__name__ for s in strategies}
    assert names == {
        "MaVolumeStrategy",
        "TurtleTradeStrategy",
        "HighTightFlagStrategy",
        "LimitUpShakeoutStrategy",
        "UptrendLimitDownStrategy",
        "RpsBreakoutStrategy",
    }
    assert all(s.webhook_key.startswith("stock_") for s in strategies)
