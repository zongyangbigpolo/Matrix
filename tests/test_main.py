"""主程序入口属性测试。"""

from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

import main as main_module
from matrix_etf.core.config import Settings
from matrix_etf.notify.feishu import FeishuNotifier as _RealNotifier


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


def test_main_degrades_and_flags_stale_on_sync_failure(monkeypatch) -> None:
    """日 K 增量同步失败时应基于本地数据继续跑策略，并在推送时携带更新失败提示。"""
    settings = Settings(
        db_path="data/test.db",
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    fake_engine = MagicMock()
    fake_engine.sync_universe_and_get_symbols.side_effect = RuntimeError("universe boom")
    fake_engine.get_local_symbols.return_value = ["510300.SH"]
    fake_engine.sync_daily.side_effect = RuntimeError("kline boom")
    monkeypatch.setattr(main_module, "DataEngine", lambda *a, **k: fake_engine)

    captured: list[dict] = []

    class _FakeNotifier:
        build_stale_warning = staticmethod(_RealNotifier.build_stale_warning)

        def __init__(self, *a, **k) -> None:
            pass

        def send(self, **kwargs) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(main_module, "FeishuNotifier", _FakeNotifier)

    fake_strategy = MagicMock()
    fake_strategy.run.return_value = ["510300.SH"]
    fake_strategy.webhook_key = "default"
    for name in (
        "RpsMomentumStrategy",
        "TrendMaStrategy",
        "BreakoutVolumeStrategy",
        "MeanReversionStrategy",
        "RiskAdjustedMomentumStrategy",
        "VolumeConfirmedMomentumStrategy",
        "LowVolTrendRotationStrategy",
    ):
        monkeypatch.setattr(main_module, name, lambda *a, **k: fake_strategy)

    monkeypatch.setattr("sys.argv", ["main.py", "--force"])
    main_module.main()

    assert captured, "更新失败也应基于本地数据继续推送"
    assert all(c["stale_warning"] and "数据更新失败" in c["stale_warning"] for c in captured)
