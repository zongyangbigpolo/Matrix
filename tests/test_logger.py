"""日志系统属性测试。"""

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st


@given(
    name=st.text(
        min_size=1,
        max_size=50,
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="._"
        ),
    )
)
@h_settings(max_examples=100)
def test_get_logger_same_instance(name: str) -> None:
    """对任意 name，多次调用 get_logger(name) 应返回同一 Logger 实例且不重复添加 handler。"""
    from matrix_etf.core.logger import get_logger

    logger1 = get_logger(name)
    logger2 = get_logger(name)
    assert logger1 is logger2
    assert len(logger1.handlers) == 1
