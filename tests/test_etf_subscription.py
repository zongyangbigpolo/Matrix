from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from matrix_etf.us_etf import subscription as source

DAY = date(2026, 9, 18)


def pcf(exchange="SH", **overrides):
    """Minimal direct-header fixtures matching the public exchange XML schemas."""
    shanghai = exchange == "SH"
    fields = {
        "FundInstrumentID" if shanghai else "SecurityID": "513100" if shanghai else "159941",
        "TradingDay": "20260918",
        "CreationRedemptionUnit": "500000" if shanghai else "1300000.00",
        "CreationLimit": "5000000" if shanghai else "0.00",
        "CreationLimitPerAcct" if shanghai else "CreationLimitPerUser":
            "500000" if shanghai else "1300000.00",
        "CreationRedemptionSwitch" if shanghai else "Creation": "1" if shanghai else "Y",
    }
    if not shanghai:
        fields.update(NetCreationLimit="36000000.00", NetCreationLimitPerUser="0.00")
    fields.update(overrides)
    body = "".join(f"<{key}>{value}</{key}>" for key, value in fields.items() if value is not None)
    if shanghai:
        return f"<SSEPortfolioCompositionFile>{body}</SSEPortfolioCompositionFile>".encode()
    return f'<PCFFile xmlns="{source.SZ_NAMESPACE}">{body}</PCFFile>'.encode()


def assert_unknown(quota, issue):
    assert quota.status == "unknown"
    assert quota.issue == issue
    assert all(getattr(quota, field) is None for field in (
        "creation_unit", "fund_cumulative", "fund_net", "account_cumulative", "account_net",
    ))


def mock_http(monkeypatch, payload=None, chunks=None, status=200, headers=None):
    response = Mock(status_code=status, headers=headers or {})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = chunks if chunks is not None else [payload or pcf()]
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr(source.requests, "Session", Mock(return_value=session))
    return session, response


def test_shanghai_exact_cumulative_and_net_shares():
    quota = source.parse_quota(
        pcf(NetCreationLimit="12345678901234567890.123456789", NetCreationLimitPerAcct="0"),
        "513100.SH", DAY, "official",
    )
    assert quota.effective_date == DAY
    assert quota.status == "open"
    assert quota.creation_unit == Decimal("500000")
    assert quota.fund_cumulative == Decimal("5000000")
    assert quota.account_cumulative == Decimal("500000")
    assert quota.fund_net == Decimal("12345678901234567890.123456789")
    assert quota.account_net == Decimal("0")
    assert quota.source_url == "official"
    with pytest.raises(FrozenInstanceError):
        quota.status = "suspended"


def test_shenzhen_zero_is_not_missing_or_unlimited():
    quota = source.parse_quota(pcf("SZ"), "159941.SZ", DAY)
    assert quota.status == "open"
    assert quota.creation_unit == Decimal("1300000.00")
    assert quota.fund_cumulative == 0
    assert quota.fund_net == 36000000
    assert quota.account_cumulative == 1300000
    assert quota.account_net == 0
    missing = source.parse_quota(pcf(CreationLimit=None, CreationLimitPerAcct=""), "513100.SH", DAY)
    assert missing.fund_cumulative is None
    assert missing.account_cumulative is None
    assert missing.fund_net is None


def test_suspended_remains_suspended_with_positive_caps():
    quota = source.parse_quota(pcf("SZ", Creation="N", CreationLimit="100"), "159941.SZ", DAY)
    assert quota.status == "suspended"
    assert quota.fund_cumulative == 100


@pytest.mark.parametrize("switch", ["0", "2", "3", "4", "", None])
def test_unverified_sse_switch_is_not_inferred(switch):
    quota = source.parse_quota(pcf(CreationRedemptionSwitch=switch), "513100.SH", DAY)
    assert_unknown(quota, "unverified-creation-status")


@pytest.mark.parametrize("switch", ["1", "y", "", None])
def test_unverified_szse_switch(switch):
    assert_unknown(source.parse_quota(pcf("SZ", Creation=switch), "159941.SZ", DAY),
                   "unverified-creation-status")


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1e6", "1,000", "+1", "1%", "9" * 129])
def test_invalid_numeric_header_invalidates_all_limits(value):
    quota = source.parse_quota(pcf(NetCreationLimit=value), "513100.SH", DAY)
    assert_unknown(quota, "invalid-quantity")
    assert quota.effective_date == DAY


@pytest.mark.parametrize("value", [None, "", "2026-09-18", "20260230", "202609180", "２０２６０９１８"])
def test_missing_or_invalid_date(value):
    quota = source.parse_quota(pcf(TradingDay=value), "513100.SH", DAY)
    assert_unknown(quota, "invalid-effective-date")
    assert quota.effective_date is None


@pytest.mark.parametrize("day", ["20260917", "20260921"])
def test_stale_and_future_data_preserve_only_diagnostic_date(day):
    quota = source.parse_quota(pcf(TradingDay=day), "513100.SH", DAY)
    assert_unknown(quota, "effective-date-mismatch")
    assert quota.effective_date == date(int(day[:4]), int(day[4:6]), int(day[6:]))


@pytest.mark.parametrize("payload,issue", [
    (b"<html>error</html>", "invalid-root"),
    (b"<SSEPortfolioCompositionFile>", "invalid-xml"),
    (pcf(FundInstrumentID="513500"), "fund-code-mismatch"),
    (pcf(FundInstrumentID=None), "fund-code-mismatch"),
    (pcf().replace(b"</SSEPortfolioCompositionFile>", b"<TradingDay>20260917</TradingDay>"
                   b"</SSEPortfolioCompositionFile>"), "duplicate-header"),
    (pcf().replace(b"</SSEPortfolioCompositionFile>", b"<CreationLimit>1</CreationLimit>"
                   b"</SSEPortfolioCompositionFile>"), "duplicate-header"),
    (pcf(CreationLimit="<Amount>1</Amount>"), "invalid-header"),
    (b'<!DOCTYPE SSEPortfolioCompositionFile>' + pcf(), "xml-directives-forbidden"),
    (b'<!ENTITY placeholder "value">' + pcf(), "xml-directives-forbidden"),
    (pcf().decode().encode("utf-16"), "unsupported-xml-encoding"),
    (b"\xff", "unsupported-xml-encoding"),
    (b"x" * (source.MAX_RESPONSE_BYTES + 1), "response-too-large"),
])
def test_invalid_documents(payload, issue):
    assert_unknown(source.parse_quota(payload, "513100.SH", DAY), issue)


def test_only_correct_root_namespace_and_direct_headers_are_used():
    xml = pcf("SZ", SecurityID=None).replace(
        b"</PCFFile>",
        b"<Components><Component><SecurityID>159941</SecurityID></Component></Components></PCFFile>",
    )
    assert_unknown(source.parse_quota(xml, "159941.SZ", DAY), "fund-code-mismatch")
    wrong_namespace = pcf("SZ").replace(source.SZ_NAMESPACE.encode(), b"https://example.invalid")
    assert_unknown(source.parse_quota(wrong_namespace, "159941.SZ", DAY), "invalid-root")
    wrong_header = pcf("SZ").replace(b"<Creation>", b'<Creation xmlns="">')
    assert_unknown(source.parse_quota(wrong_header, "159941.SZ", DAY), "invalid-header-namespace")
    xml = pcf("SZ").replace(
        b"</PCFFile>", b"<Components><Component><SecurityID>OTHER</SecurityID>"
        b"<CreationLimit>999999999</CreationLimit></Component></Components></PCFFile>",
    )
    assert source.parse_quota(xml, "159941.SZ", DAY).fund_cumulative == 0


def test_fetch_urls_timeouts_no_redirects_and_no_credentials(monkeypatch):
    session, response = mock_http(monkeypatch)
    quota = source.fetch_quotas(["513100.SH"], DAY)["513100.SH"]
    assert quota.status == "open"
    assert quota.source_url == (
        "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=513100"
    )
    assert session.trust_env is False
    assert session.get.call_args.kwargs["timeout"] == source.REQUEST_TIMEOUT
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["stream"] is True
    response.__exit__.assert_called_once()
    session.__exit__.assert_called_once()
    session, _ = mock_http(monkeypatch, payload=pcf("SZ"))
    quota = source.fetch_quotas(["159941.SZ"], DAY)["159941.SZ"]
    assert quota.source_url == (
        "https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_159941_20260918.xml"
    )


@pytest.mark.parametrize("symbol", ["513100", "513100.sh", "../513100.SH", "１２３４５６.SH", "X\n.SH"])
def test_unsafe_symbols_never_reach_network_or_logs(monkeypatch, symbol):
    network = Mock()
    warning = Mock()
    monkeypatch.setattr(source.requests, "Session", network)
    monkeypatch.setattr(source.logger, "warning", warning)
    assert_unknown(source.fetch_quotas([symbol], DAY)[symbol], "invalid-symbol")
    network.assert_not_called()
    assert warning.call_args.args[1] == "<invalid symbol>"


@pytest.mark.parametrize("status", [301, 302, 404, 429, 500])
def test_http_errors_and_redirects_are_not_followed(monkeypatch, status):
    session, response = mock_http(monkeypatch, status=status)
    assert_unknown(source.fetch_quotas(["513100.SH"], DAY)["513100.SH"], "http-status-not-200")
    response.iter_content.assert_not_called()
    session.get.assert_called_once()


def test_request_failure_is_logged_without_sensitive_details(monkeypatch):
    session, _ = mock_http(monkeypatch)
    warning = Mock()
    monkeypatch.setattr(source.logger, "warning", warning)
    session.get.side_effect = requests.Timeout("credential=do-not-log")
    assert_unknown(source.fetch_quotas(["513100.SH"], DAY)["513100.SH"], "request-failed")
    assert "do-not-log" not in str(warning.call_args)
    warning.assert_called_once()


@pytest.mark.parametrize("headers,chunks,issue", [
    ({"Content-Length": str(source.MAX_RESPONSE_BYTES + 1)}, [], "response-too-large"),
    ({"Content-Length": "-1"}, [], "invalid-content-length"),
    ({}, [b"x" * source.MAX_RESPONSE_BYTES, b"x"], "response-too-large"),
    ({"Content-Length": "10"}, [b"x" * (source.MAX_RESPONSE_BYTES + 1)], "response-too-large"),
])
def test_bounded_network_response(monkeypatch, headers, chunks, issue):
    _, response = mock_http(monkeypatch, chunks=chunks, headers=headers)
    assert_unknown(source.fetch_quotas(["513100.SH"], DAY)["513100.SH"], issue)
    response.__exit__.assert_called_once()


def test_stream_deadline_and_stream_failure(monkeypatch):
    mock_http(monkeypatch, chunks=[b"x", b"x"])
    monkeypatch.setattr(source.time, "monotonic", Mock(side_effect=[0, 1, 21]))
    assert_unknown(source.fetch_quotas(["513100.SH"], DAY)["513100.SH"], "request-deadline-exceeded")
    monkeypatch.setattr(source.time, "monotonic", lambda: 0)
    _, response = mock_http(monkeypatch)
    response.iter_content.side_effect = requests.ConnectionError("private diagnostic")
    assert_unknown(source.fetch_quotas(["513100.SH"], DAY)["513100.SH"], "request-failed")


def test_all_symbols_returned_failures_isolated_and_no_retry(monkeypatch):
    calls = []

    def download(url):
        calls.append(url)
        if "513100" in url:
            raise requests.ConnectionError("error")
        return pcf("SZ")

    monkeypatch.setattr(source, "_download", download)
    result = source.fetch_quotas(["513100.SH", "159941.SZ", "159941.SZ"], DAY)
    assert set(result) == {"513100.SH", "159941.SZ"}
    assert result["513100.SH"].status == "unknown"
    assert result["159941.SZ"].status == "open"
    assert len(calls) == 2


def test_empty_and_over_limit_selection_do_not_request(monkeypatch):
    network = Mock()
    monkeypatch.setattr(source.requests, "Session", network)
    assert source.fetch_quotas([], DAY) == {}
    symbols = [f"{code:06d}.SH" for code in range(51)]
    result = source.fetch_quotas(symbols, DAY)
    assert set(result) == set(symbols)
    assert all(quota.issue == "too-many-symbols" for quota in result.values())
    network.assert_not_called()


def test_programming_errors_are_not_silenced(monkeypatch):
    monkeypatch.setattr(source, "_download", Mock(side_effect=TypeError("programming error")))
    with pytest.raises(TypeError, match="programming error"):
        source.fetch_quotas(["513100.SH"], DAY)
