"""主程序入口属性测试。"""

from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

import main as main_module


@given(error_msg=st.text(min_size=1, max_size=100))
@h_settings(max_examples=30, deadline=None)
def test_main_exits_nonzero_on_exception(error_msg: str) -> None:
    """main() 中任意未捕获异常应导致 sys.exit(1)。"""
    with patch.object(main_module, "get_settings", side_effect=RuntimeError(error_msg)):
        with pytest.raises(SystemExit) as exc_info:
            main_module.main()
        assert exc_info.value.code != 0


def test_parse_symbols() -> None:
    """--symbols 解析应去空白并忽略空项。"""
    assert main_module._parse_symbols(None) is None
    assert main_module._parse_symbols("") is None
    assert main_module._parse_symbols("510300.SH, 159915.SZ ,") == [
        "510300.SH",
        "159915.SZ",
    ]
