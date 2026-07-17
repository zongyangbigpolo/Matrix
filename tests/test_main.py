"""主程序入口属性测试。"""

from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

import main as main_module
from matrix_etf.core.config import Settings
from matrix_etf.data.sync_runner import SyncOutcome


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


def _make_settings() -> Settings:
    return Settings(
        db_path="data/test.db",
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
    )


def test_main_sends_alert_and_skips_strategies_on_sync_failure(monkeypatch) -> None:
    """持续拉取失败时应发送告警卡片并跳过全部策略推送。"""
    settings = _make_settings()
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    fake_engine = MagicMock()
    fake_engine.sync_universe_and_get_symbols.return_value = ["510300.SH"]
    fake_engine.get_local_symbols.return_value = ["510300.SH"]
    monkeypatch.setattr(main_module, "DataEngine", lambda *a, **k: fake_engine)

    failed = SyncOutcome(False, 0, 1, "2026-07-15", 3, 9000.0, "timeout")
    monkeypatch.setattr(main_module, "sync_until_stable", lambda *a, **k: failed)

    sent: list[dict] = []
    alerts: list[dict] = []

    class _FakeNotifier:
        def __init__(self, *a, **k) -> None:
            pass

        def send(self, **kwargs) -> None:
            sent.append(kwargs)

        def send_alert(self, **kwargs) -> None:
            alerts.append(kwargs)

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

    assert len(alerts) == 1, "失败应发送一张告警卡片"
    assert alerts[0]["category"] == "ETF"
    assert not sent, "失败时不应推送任何策略卡片"
    fake_strategy.run.assert_not_called()


def test_main_runs_strategies_on_sync_success(monkeypatch) -> None:
    """持续拉取成功时应正常跑策略并推送（不带 stale_warning）。"""
    settings = _make_settings()
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    fake_engine = MagicMock()
    fake_engine.sync_universe_and_get_symbols.return_value = ["510300.SH"]
    fake_engine.get_local_symbols.return_value = ["510300.SH"]
    monkeypatch.setattr(main_module, "DataEngine", lambda *a, **k: fake_engine)

    ok = SyncOutcome(True, 1, 1, "2026-07-16", 1, 1.0, "covered")
    monkeypatch.setattr(main_module, "sync_until_stable", lambda *a, **k: ok)

    sent: list[dict] = []
    alerts: list[dict] = []

    class _FakeNotifier:
        def __init__(self, *a, **k) -> None:
            pass

        def send(self, **kwargs) -> None:
            sent.append(kwargs)

        def send_alert(self, **kwargs) -> None:
            alerts.append(kwargs)

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

    assert not alerts, "成功时不应发送告警卡片"
    assert sent, "成功时应推送策略卡片"
    assert all("stale_warning" not in c for c in sent)
