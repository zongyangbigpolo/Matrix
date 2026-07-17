"""限流重试封装（matrix_etf.data.rate_limit）的单元测试。"""

import pytest

from matrix_etf.data import rate_limit
from matrix_etf.data.rate_limit import (
    call_with_retry,
    is_rate_limit_error,
    suggested_delay_seconds,
)


@pytest.mark.parametrize(
    "message",
    [
        "请求频率超限 (60/min)，请 452ms 后重试",
        "Rate limit exceeded, please retry",
        "HTTP 429 Too Many Requests",
        "请求过于频繁",
    ],
)
def test_is_rate_limit_error_detects_markers(message):
    assert is_rate_limit_error(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "connection reset by peer",
        "timeout while reading response",
        "some unrelated failure",
    ],
)
def test_is_rate_limit_error_ignores_non_rate_limit(message):
    assert is_rate_limit_error(RuntimeError(message)) is False


def test_suggested_delay_parses_milliseconds():
    exc = RuntimeError("请求频率超限 (60/min)，请 452ms 后重试")
    assert suggested_delay_seconds(exc) == pytest.approx(0.452)


def test_suggested_delay_parses_seconds():
    exc = RuntimeError("rate limited, retry after 3s")
    assert suggested_delay_seconds(exc) == pytest.approx(3.0)


def test_suggested_delay_none_when_absent():
    assert suggested_delay_seconds(RuntimeError("boom")) is None


def test_call_with_retry_returns_first_success(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(rate_limit.time, "sleep", lambda s: sleeps.append(s))

    result = call_with_retry(lambda: 42, attempts=3)

    assert result == 42
    assert sleeps == []  # 首次即成功，不应有任何等待


def test_call_with_retry_recovers_after_rate_limit(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(rate_limit.time, "sleep", lambda s: sleeps.append(s))
    # 让抖动确定化，便于断言
    monkeypatch.setattr(rate_limit.random, "uniform", lambda a, b: 0.0)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("请求频率超限 (60/min)，请 452ms 后重试")
        return "ok"

    result = call_with_retry(
        flaky, attempts=5, base_delay=2.0, max_delay=60.0
    )

    assert result == "ok"
    assert calls["n"] == 3
    # 两次重试：退避取 max(建议值, base*2**n) → 2.0, 4.0
    assert sleeps == [pytest.approx(2.0), pytest.approx(4.0)]


def test_call_with_retry_raises_after_exhausting_attempts(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(rate_limit.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(rate_limit.random, "uniform", lambda a, b: 0.0)

    def always_limited():
        raise RuntimeError("请求频率超限 (60/min)")

    with pytest.raises(RuntimeError, match="频率超限"):
        call_with_retry(always_limited, attempts=3, base_delay=1.0)

    # attempts=3 → 尝试 3 次、重试 2 次（最后一次失败后直接抛出，不再 sleep）
    assert len(sleeps) == 2


def test_call_with_retry_honors_suggested_delay_when_larger(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(rate_limit.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(rate_limit.random, "uniform", lambda a, b: 0.0)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            # 建议 10s，远大于首个退避基准 2s，应采用较大者
            raise RuntimeError("频率超限，请 10s 后重试")
        return "done"

    call_with_retry(flaky, attempts=3, base_delay=2.0, max_delay=60.0)

    assert sleeps == [pytest.approx(10.0)]
