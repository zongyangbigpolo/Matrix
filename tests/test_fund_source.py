import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from matrix_etf.funds import source


def row(code="050025", status="暂停申购", minimum="10", limit="100", fee="0.15"):
    return [
        code, "博时标普500指数（QDII）A", "QDII-指数", "1.2345", "2026-09-16",
        status, "开放赎回", "", minimum, limit, "", "", fee,
    ]


def envelope(rows=None, **metadata):
    rows = [row()] if rows is None else rows
    data = {
        "datas": rows,
        "record": str(len(rows)),
        "pages": "1",
        "curpage": "1",
        "showday": ["2026-09-16", "2026-09-15"],
    }
    data.update(metadata)
    return "var reData={" + ",".join(
        key + ":" + json.dumps(value, ensure_ascii=False) for key, value in data.items()
    ) + "}"


def response(text=None, *, status=200, headers=None, chunks=None):
    result = Mock()
    result.status_code = status
    result.headers = headers or {}
    result.iter_content.return_value = (
        chunks if chunks is not None else [(text or envelope()).encode("utf-8")]
    )
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    get = Mock(side_effect=AssertionError("Unexpected network access"))
    monkeypatch.setattr(source.requests, "get", get)
    monkeypatch.setattr(source.time, "sleep", Mock())
    return get


def test_quotes_preserve_unicode_status_and_stale_numeric_limit():
    quote = source.parse_quotes(envelope(), {"050025"})["050025"]
    assert quote == source.FundQuote(
        "050025", "博时标普500指数（QDII）A", "QDII-指数", "暂停申购",
        Decimal("10"), Decimal("100"), Decimal("0.15"),
    )
    with pytest.raises(FrozenInstanceError):
        quote.status = "开放申购"


@pytest.mark.parametrize("status", ["开放申购", "限大额", "暂停申购", "场内交易", "封闭期", "认购期", ""])
def test_status_unchanged(status):
    quote = source.parse_quotes(envelope([row(status=status)]), {"050025"})["050025"]
    assert quote.status == status


def test_tiny_limits_missing_codes_and_only_requested_quotes(monkeypatch):
    build = Mock(wraps=source._quote)
    monkeypatch.setattr(source, "_quote", build)
    result = source.parse_quotes(
        envelope([
            row("270042", "限大额", "2", "2"),
            row("006479", "限大额", "2", "2"),
            row(),
        ]),
        {"270042", "006479", "999999"},
    )
    assert set(result) == {"270042", "006479"}
    assert all(q.minimum == q.daily_limit == Decimal("2") for q in result.values())
    assert build.call_count == 2


@pytest.mark.parametrize("value", ["0", "-0", "100000000000", "1234.5678", "1e2"])
def test_valid_amounts_are_not_reinterpreted(value):
    quote = source.parse_quotes(
        envelope([row(minimum=value, limit=value, fee=value)]), {"050025"}
    )["050025"]
    assert quote.minimum == quote.daily_limit == quote.fee_percent == Decimal(value)
    assert quote.issue is None


@pytest.mark.parametrize("value", ["", " ", "-", "--", "---", "—", "N/A", "null", "None"])
def test_missing_amounts(value):
    quote = source.parse_quotes(
        envelope([row(minimum=value, limit=value, fee=value)]), {"050025"}
    )["050025"]
    assert quote.minimum is quote.daily_limit is quote.fee_percent is None
    assert quote.issue is None


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "-1", "abc", "1_000", "不限", "1%%"])
@pytest.mark.parametrize("field,index", [("minimum", 8), ("daily_limit", 9), ("fee_percent", 12)])
def test_invalid_amounts_are_quote_specific_issues(value, field, index):
    item = row()
    item[index] = value
    result = source.parse_quotes(envelope([item, row("270042")]), {"050025", "270042"})
    assert getattr(result["050025"], field) is None
    assert field in result["050025"].issue
    assert result["270042"].issue is None


def test_live_fee_percent_suffix():
    quote = source.parse_quotes(
        envelope([row("270042", "限大额", "2.0", "2.0", "0.13%")]), {"270042"}
    )["270042"]
    assert quote.fee_percent == Decimal("0.13")
    assert quote.issue is None


def test_percent_suffix_is_not_a_currency_amount():
    quote = source.parse_quotes(
        envelope([row(minimum="1%", limit="10%", fee="0.00%")]), {"050025"}
    )["050025"]
    assert quote.minimum is quote.daily_limit is None
    assert quote.fee_percent == 0
    assert quote.issue is not None


def test_free_catalog_fetch_uses_bounded_transport(no_network):
    no_network.side_effect = None
    no_network.return_value = response("var r = [];")
    assert source.fetch_catalog_text() == "var r = [];"
    assert no_network.call_args.args[0] == source.CATALOG_URL
    assert no_network.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("suffix", ["; alert(1)", " garbage", "\nvar other={}", ";;", "//comment"])
def test_rejects_trailing_javascript(suffix):
    with pytest.raises(source.FundSourceError):
        source.parse_quotes(envelope() + suffix, {"050025"})


def test_optional_terminal_semicolon():
    assert source.parse_quotes(envelope() + "; \n", {"050025"})


@pytest.mark.parametrize("text", [
    "", "<html>blocked</html>", "var reData={}", "var reData={datas:alert(1)}",
    envelope()[:-1], envelope()[:100], envelope().replace('"10"', "NaN"),
    envelope().replace('record:"1"', 'record:"1",record:"1"'),
    envelope().replace('record:"1",', ""),
    envelope().replace("datas:", "wrong:"),
    envelope()[:-1] + ",}",
])
def test_malformed_schema(text):
    with pytest.raises(source.FundSourceError):
        source.parse_quotes(text, {"050025"})


@pytest.mark.parametrize("metadata", [
    {"record": "2"}, {"record": 1}, {"record": "0"}, {"pages": "2"},
    {"curpage": "2"}, {"pages": 1}, {"showday": []}, {"showday": ["x", "y"]},
])
def test_incomplete_metadata(metadata):
    with pytest.raises(source.FundSourceError):
        source.parse_quotes(envelope(**metadata), {"050025"})


@pytest.mark.parametrize("rows", [[], [row(), row()], [row()[:-1]], [row() + ["x"]], ["not a row"]])
def test_invalid_batch_rows(rows):
    with pytest.raises(source.FundSourceError):
        source.parse_quotes(envelope(rows), {"999999"})


@pytest.mark.parametrize("index,value", [
    (0, "０５００２５"), (0, "12345"), (0, "1234567"), (0, "050025\n"),
    (1, ""), (1, " "), (1, "x" * 201), (1, "bad\nname"), (1, "bad\x00name"),
    (1, "bad\u202ename"), (2, None), (8, 1),
])
def test_invalid_row_fields_even_when_not_requested(index, value):
    item = row()
    item[index] = value
    with pytest.raises(source.FundSourceError):
        source.parse_quotes(envelope([item]), {"999999"})


@pytest.mark.parametrize("code", ["１２３４５６", "12345", "123456\n", 123456])
def test_requested_code_validation(code):
    with pytest.raises(ValueError):
        source.parse_quotes(envelope(), {code})
    with pytest.raises(ValueError):
        source.fetch_quotes({code})


def test_fetch_fixed_public_endpoint_and_fresh_requests(no_network):
    replies = [response(), response()]
    no_network.side_effect = replies
    for _ in range(2):
        assert source.fetch_quotes({"050025"})["050025"].status == "暂停申购"
    assert no_network.call_count == 2
    no_network.assert_called_with(
        source.SOURCE_URL,
        params={"t": "8", "page": "1,50000", "js": "reData", "sort": "fcode,asc"},
        timeout=20.0, stream=True, allow_redirects=False,
    )
    assert all(reply.__exit__.call_count == 1 for reply in replies)


@pytest.mark.parametrize("error", [
    requests.ConnectionError("disconnected"), requests.Timeout("timeout"),
    requests.exceptions.ChunkedEncodingError("truncated"),
])
def test_connection_transient_retry(no_network, error):
    no_network.side_effect = [error, response()]
    assert source.fetch_quotes({"050025"})
    assert no_network.call_count == 2
    source.time.sleep.assert_called_once_with(1.0)


def test_body_transient_retry_closes_response(no_network):
    first = response()
    first.iter_content.side_effect = requests.ConnectionError("stream interrupted")
    no_network.side_effect = [first, response()]
    assert source.fetch_quotes({"050025"})
    first.__exit__.assert_called_once()


def test_connection_retries_exhausted(no_network):
    no_network.side_effect = requests.ConnectionError("offline")
    with pytest.raises(source.FundSourceError):
        source.fetch_quotes({"050025"}, attempts=2)
    assert no_network.call_count == 2
    source.time.sleep.assert_called_once()


@pytest.mark.parametrize("status", [429, 500, 503, 599])
def test_retryable_http_and_bounded_retry_after(no_network, status):
    first = response(status=status, headers={"Retry-After": "999999"})
    no_network.side_effect = [first, response()]
    assert source.fetch_quotes({"050025"})
    source.time.sleep.assert_called_once_with(30.0)
    first.__exit__.assert_called_once()


@pytest.mark.parametrize("header,expected", [
    ("2", 2.0), ("-1", 0.0), ("invalid", 1.0), ("NaN", 1.0),
    ("Wed, 01 Jan 2100 00:00:00 GMT", 30.0),
    ("Wed, 01 Jan 2020 00:00:00 GMT", 0.0),
])
def test_retry_after(header, expected):
    assert source._retry_delay(header, 0) == expected


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404])
def test_http_failure_no_retry_or_redirect(no_network, status):
    reply = response(status=status)
    no_network.side_effect = [reply]
    with pytest.raises(source.FundSourceError, match=f"HTTP {status}"):
        source.fetch_quotes({"050025"})
    assert no_network.call_count == 1
    source.time.sleep.assert_not_called()
    reply.__exit__.assert_called_once()


def test_tls_failure_not_retried(no_network):
    no_network.side_effect = requests.exceptions.SSLError("certificate")
    with pytest.raises(source.FundSourceError, match="TLS"):
        source.fetch_quotes({"050025"})
    assert no_network.call_count == 1


@pytest.mark.parametrize("text", [envelope(record="2"), "<html>403</html>", envelope()[:-10]])
def test_schema_failure_is_not_retried_and_closes_response(no_network, text):
    reply = response(text)
    no_network.side_effect = [reply]
    with pytest.raises(source.FundSourceError):
        source.fetch_quotes({"050025"})
    assert no_network.call_count == 1
    source.time.sleep.assert_not_called()
    reply.__exit__.assert_called_once()


@pytest.mark.parametrize("headers,chunks", [
    ({"Content-Length": "101"}, [b"x"]),
    ({}, [b"x" * 60, b"x" * 41]),
])
def test_body_size_limit(no_network, monkeypatch, headers, chunks):
    monkeypatch.setattr(source, "MAX_RESPONSE_BYTES", 100)
    reply = response(headers=headers, chunks=chunks)
    no_network.side_effect = [reply]
    with pytest.raises(source.FundSourceError, match="size limit"):
        source.fetch_quotes({"050025"})
    assert no_network.call_count == 1
    reply.__exit__.assert_called_once()


def test_parse_size_limit(monkeypatch):
    text = envelope()
    monkeypatch.setattr(source, "MAX_RESPONSE_BYTES", len(text))
    with pytest.raises(source.FundSourceError, match="size limit"):
        source.parse_quotes(text, {"050025"})


def test_invalid_encoding_not_retried(no_network):
    reply = response(chunks=[b"\xff"])
    no_network.side_effect = [reply]
    with pytest.raises(source.FundSourceError, match="encoding"):
        source.fetch_quotes({"050025"})
    assert no_network.call_count == 1
    reply.__exit__.assert_called_once()


@pytest.mark.parametrize("kwargs", [
    {"timeout": 0}, {"timeout": float("inf")}, {"timeout": float("nan")},
    {"timeout": 61}, {"attempts": 0}, {"attempts": 6}, {"attempts": 1.5},
    {"attempts": True},
])
def test_bounded_network_options(kwargs):
    with pytest.raises(ValueError):
        source.fetch_quotes({"050025"}, **kwargs)


def test_empty_requested_set_skips_network(no_network):
    assert source.fetch_quotes(set()) == {}
    no_network.assert_not_called()
