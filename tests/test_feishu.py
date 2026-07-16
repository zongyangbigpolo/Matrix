"""飞书通知属性测试。"""

import json
import logging
from unittest.mock import MagicMock, patch

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from matrix_etf.core.config import Settings
from matrix_etf.notify.feishu import FeishuNotifier


def make_settings(webhook_url: str = "https://example.com/default", **kwargs) -> Settings:
    return Settings(
        db_path="data/test.db",
        start_date="2020-01-01",
        feishu_webhook_url=webhook_url,
        **kwargs,
    )


@given(
    codes=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=1, max_size=10, unique=True,
    )
)
@h_settings(max_examples=50)
def test_notification_contains_all_symbols(codes: list[str]) -> None:
    """send() 发出的请求体应包含所有 symbol。"""
    symbols = [f"{c}.SH" for c in codes]
    settings = make_settings(feishu_retry_attempts=1, feishu_retry_backoff_seconds=0.0)
    notifier = FeishuNotifier(settings)

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})
        notifier.send(symbols=symbols, strategy_name="TestStrategy")

    call_args = mock_post.call_args
    body = json.loads(call_args.kwargs["data"])
    card_text = json.dumps(body)
    for symbol in symbols:
        assert symbol in card_text


@given(
    webhook_url=st.from_regex(
        r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[a-z0-9\-]{8,36}", fullmatch=True
    )
)
@h_settings(max_examples=50)
def test_notification_uses_config_url(webhook_url: str) -> None:
    """send() 发出的 HTTP 请求目标 URL 应等于 settings.feishu_webhook_url。"""
    settings = make_settings(webhook_url=webhook_url)
    notifier = FeishuNotifier(settings)

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})
        notifier.send(symbols=["510300.SH"], strategy_name="Test", webhook_key="default")

    called_url = (
        mock_post.call_args.args[0]
        if mock_post.call_args.args
        else mock_post.call_args.kwargs.get("url")
    )
    assert called_url == webhook_url


@given(status_code=st.integers(min_value=400, max_value=599))
@h_settings(max_examples=50)
def test_http_failure_logs_error(status_code: int) -> None:
    """非 200 响应时，send() 应记录 ERROR 级别日志，不抛出异常。"""
    import matrix_etf.notify.feishu as feishu_module

    settings = make_settings(feishu_retry_attempts=1, feishu_retry_backoff_seconds=0.0)
    notifier = FeishuNotifier(settings)

    feishu_logger = logging.getLogger(feishu_module.__name__)
    log_records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = _ListHandler(logging.ERROR)
    feishu_logger.addHandler(handler)
    try:
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=status_code, text="error", json=lambda: {"code": 1}
            )
            notifier.send(symbols=["510300.SH"], strategy_name="Test")
    finally:
        feishu_logger.removeHandler(handler)

    assert any(r.levelno == logging.ERROR for r in log_records)


def test_xueqiu_code_mapping() -> None:
    """tickflow symbol 应正确转换为雪球代码。"""
    assert FeishuNotifier._to_xueqiu_code("510300.SH") == "SH510300"
    assert FeishuNotifier._to_xueqiu_code("159915.SZ") == "SZ159915"
    assert FeishuNotifier._to_xueqiu_code("600519.SH") == "SH600519"
    # 无后缀时按 A 股代码首位推断（6→SH，0/3→SZ，4/8→BJ）
    assert FeishuNotifier._to_xueqiu_code("600519") == "SH600519"
    assert FeishuNotifier._to_xueqiu_code("000001") == "SZ000001"
    assert FeishuNotifier._to_xueqiu_code("830799") == "BJ830799"
    # 美股：雪球直接用 ticker，无市场前缀（含带点的多类别股，如 BRK.B）
    assert FeishuNotifier._to_xueqiu_code("AAPL.US") == "AAPL"
    assert FeishuNotifier._to_xueqiu_code("BRK.B.US") == "BRK.B"


def test_us_category_card_uses_us_labels() -> None:
    """category='US' 时卡片标题应体现「美股」并显示中文策略名，链接用纯 ticker。"""
    settings = make_settings()
    notifier = FeishuNotifier(settings)
    card = notifier._build_card(["AAPL.US"], "UsRpsMomentumStrategy", category="US")
    text = json.dumps(card, ensure_ascii=False)
    assert "Matrix 美股信号 | 美股相对强度动量" in text
    assert "UsRpsMomentumStrategy" not in text
    assert "候选美股" in text
    assert "https://xueqiu.com/S/AAPL" in text


def test_stock_category_card_uses_stock_labels() -> None:
    """category='Stock' 时卡片标题应体现「个股」并显示中文策略名。"""
    settings = make_settings()
    notifier = FeishuNotifier(settings)
    card = notifier._build_card(["600519.SH"], "TurtleTradeStrategy", category="Stock")
    text = json.dumps(card, ensure_ascii=False)
    # 标题除 Matrix 外均为中文（个股信号 + 中文策略名），而非英文类名
    assert "Matrix 个股信号 | 海龟突破" in text
    assert "TurtleTradeStrategy" not in text
    assert "候选个股" in text


def test_card_title_uses_chinese_strategy_name() -> None:
    """ETF 卡片标题与正文的策略名应翻译为中文。"""
    settings = make_settings()
    notifier = FeishuNotifier(settings)
    card = notifier._build_card(["510300.SH"], "RpsMomentumStrategy")
    text = json.dumps(card, ensure_ascii=False)
    assert "Matrix ETF信号 | 相对强度动量" in text
    assert "相对强度动量" in text
    assert "RpsMomentumStrategy" not in text


def test_unknown_strategy_name_falls_back_to_original() -> None:
    """未登记的策略类名应原样展示，不报错。"""
    settings = make_settings()
    notifier = FeishuNotifier(settings)
    card = notifier._build_card(["510300.SH"], "SomeUnknownStrategy")
    assert "SomeUnknownStrategy" in json.dumps(card, ensure_ascii=False)


def test_stale_warning_card_shows_red_banner() -> None:
    """带 stale_warning 时卡片应显示醒目警示并使用红色标题。"""
    settings = make_settings()
    notifier = FeishuNotifier(settings)
    warning = FeishuNotifier.build_stale_warning("网络超时")
    card = notifier._build_card(
        ["510300.SH"], "RpsMomentumStrategy", stale_warning=warning
    )

    assert card["card"]["header"]["template"] == "red"
    text = json.dumps(card, ensure_ascii=False)
    assert "数据更新失败" in text
    assert "网络超时" in text
    # 首个元素应为警示条
    assert "⚠️" in card["card"]["elements"][0]["text"]["content"]


def test_build_stale_warning_truncates_long_reason() -> None:
    """更新失败原因过长时应截断，避免卡片过长。"""
    warning = FeishuNotifier.build_stale_warning("x" * 500)
    assert "数据更新失败" in warning
    assert "…" in warning
    assert len(warning) < 200


def test_normal_card_uses_turquoise_and_no_warning() -> None:
    """未传 stale_warning 时卡片应保持青色标题且不含警示条。"""
    settings = make_settings()
    notifier = FeishuNotifier(settings)
    card = notifier._build_card(["510300.SH"], "RpsMomentumStrategy")
    assert card["card"]["header"]["template"] == "turquoise"
    assert "数据更新失败" not in json.dumps(card, ensure_ascii=False)


def test_retry_on_request_exception_then_success() -> None:
    """网络异常应按配置重试，后续成功则停止重试。"""
    settings = make_settings(feishu_retry_backoff_seconds=0.0)
    notifier = FeishuNotifier(settings)

    with patch("requests.post") as mock_post:
        mock_post.side_effect = [
            __import__("requests").RequestException("timeout"),
            MagicMock(status_code=200, text='{"code":0}', json=lambda: {"code": 0}),
        ]
        notifier.send(symbols=["510300.SH"], strategy_name="Test")

    assert mock_post.call_count == 2


def test_non_json_response_does_not_raise() -> None:
    """飞书返回非 JSON 文本时 send() 应记录错误并返回，不抛异常。"""
    settings = make_settings(feishu_retry_attempts=1, feishu_retry_backoff_seconds=0.0)
    notifier = FeishuNotifier(settings)

    with patch("requests.post") as mock_post:
        response = MagicMock(status_code=200, text="<html>bad gateway</html>")
        response.json.side_effect = ValueError("not json")
        mock_post.return_value = response
        notifier.send(symbols=["510300.SH"], strategy_name="Test")

    assert mock_post.call_count == 1
