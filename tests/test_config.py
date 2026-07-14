"""配置管理属性测试。"""

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st
from pydantic import ValidationError


@given(
    db_path=st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="/_.-"
        ),
    )
)
@h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_env_overrides_default(db_path: str, monkeypatch) -> None:
    """任意合法 db_path 通过环境变量设置后，Settings 实例应反映该值。"""
    import matrix_etf.core.config as cfg_module

    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(cfg_module, "_settings", None)
    from matrix_etf.core.config import Settings

    s = Settings()
    assert s.db_path == db_path


def test_missing_required_field_raises() -> None:
    """缺少 feishu_webhook_url 时，实例化 Settings 应抛出 ValidationError。"""
    import os

    from matrix_etf.core.config import Settings

    env_backup = os.environ.pop("FEISHU_WEBHOOK_URL", None)
    try:
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "feishu_webhook_url" in str(exc_info.value).lower()
    finally:
        if env_backup is not None:
            os.environ["FEISHU_WEBHOOK_URL"] = env_backup


def test_strategy_webhook_env_collected(monkeypatch) -> None:
    """STRATEGY_WEBHOOK_ 前缀的环境变量应被收集到 strategy_webhooks 并可路由。"""
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.com/default")
    monkeypatch.setenv("STRATEGY_WEBHOOK_RPS", "https://example.com/rps")
    from matrix_etf.core.config import Settings

    s = Settings()
    assert s.strategy_webhooks.get("rps") == "https://example.com/rps"
    assert s.get_webhook_url("rps") == "https://example.com/rps"
    # 未配置的策略回退到默认 webhook
    assert s.get_webhook_url("trend") == "https://example.com/default"
