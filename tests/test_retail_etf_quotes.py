import sqlite3
from dataclasses import replace
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from matrix_etf.core.config import Settings
from matrix_etf.us_etf import source
from matrix_etf.us_etf.listing import CATEGORIES, Product, Quote

DAY = date(2026, 9, 18)
PRODUCT = Product("513100.SH", "纳斯达克100ETF", CATEGORIES[0])


def history(closes, days=("2026-09-17", "2026-09-18")):
    return pd.DataFrame({
        "trade_date": days, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [100] * len(closes), "amount": [1234] * len(closes),
    })


@pytest.fixture
def retail_source(tmp_path, monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(source, "create_tickflow_client", lambda *a, **k: client)
    instance = source.USEtfSource(Settings(
        _env_file=None, feishu_webhook_url="", db_path=str(tmp_path / "shared.db"),
    ))
    client.universes.get.return_value = {"symbols": [PRODUCT.symbol]}
    client.instruments.batch.return_value = [
        {"symbol": PRODUCT.symbol, "name": PRODUCT.name, "type": "etf"},
    ]
    responses = {
        "forward": {PRODUCT.symbol: history([2, 2.2])},
        "none": {PRODUCT.symbol: history([4, 4.8])},
    }
    client.klines.batch.side_effect = lambda symbols, **kw: responses[kw["adjust"]]
    yield instance, client, responses
    instance.close()


def stored_prices(instance):
    with sqlite3.connect(instance.engine.db_path) as conn:
        return conn.execute(
            "SELECT date, close FROM etf_daily WHERE symbol = ? ORDER BY date",
            (PRODUCT.symbol,),
        ).fetchall()


def test_raw_exchange_close_and_change_never_contaminate_strategy_cache(retail_source):
    instance, client, _ = retail_source
    quote, = instance.fetch(DAY)
    assert quote.close == 2.2
    assert quote.change == pytest.approx(10)
    assert quote.market_close == 4.8
    assert quote.market_close * 100 == pytest.approx(480)
    assert quote.market_change == pytest.approx(20)
    assert quote.market_date == DAY
    assert quote.market_previous_date == date(2026, 9, 17)
    assert quote.market_previous_is_market_day
    assert quote.amount == 1234
    assert stored_prices(instance) == [("2026-09-17", 2), ("2026-09-18", 2.2)]
    assert [call.kwargs["adjust"] for call in client.klines.batch.call_args_list] == [
        "forward", "none",
    ]


def test_empty_raw_history_does_not_reuse_previous_raw_or_forward_close(retail_source):
    instance, _, responses = retail_source
    assert instance.fetch(DAY)[0].market_close == 4.8
    responses["none"] = {PRODUCT.symbol: pd.DataFrame()}
    quote, = instance.fetch(DAY)
    assert quote.close == 2.2
    assert quote.market_close is None
    assert quote.market_date is None
    assert quote.market_change is None
    assert quote.market_previous_date is None
    assert not quote.market_previous_is_market_day
    assert stored_prices(instance) == [("2026-09-17", 2), ("2026-09-18", 2.2)]


def test_raw_price_is_available_when_forward_history_is_empty(retail_source):
    instance, _, responses = retail_source
    responses["forward"] = {PRODUCT.symbol: pd.DataFrame()}
    quote, = instance.fetch(DAY)
    assert quote.close is None and quote.trade_date is None and quote.change is None
    assert quote.market_close == 4.8 and quote.market_date == DAY
    assert quote.market_change == pytest.approx(20)
    assert stored_prices(instance) == []


def test_invalid_forward_observation_stays_missing_while_raw_is_valid(retail_source):
    instance, _, responses = retail_source
    instance.fetch(DAY)
    responses["forward"][PRODUCT.symbol].loc[1, "close"] = float("nan")
    quote, = instance.fetch(DAY)
    assert quote.close is None and quote.change is None
    assert quote.market_close == 4.8
    assert quote.market_change == pytest.approx(20)
    assert stored_prices(instance)[-1] == ("2026-09-18", 2.2)


def test_raw_and_forward_dates_and_changes_stay_independent(retail_source):
    instance, _, responses = retail_source
    responses["none"] = {
        PRODUCT.symbol: history([5, 4], ("2026-09-14", "2026-09-16")),
    }
    quote, = instance.fetch(DAY)
    assert quote.trade_date == DAY and not quote.stale
    assert quote.previous_date == date(2026, 9, 17) and quote.previous_is_market_day
    assert quote.change == pytest.approx(10)
    assert quote.market_date == date(2026, 9, 16)
    assert quote.market_previous_date == date(2026, 9, 14)
    assert not quote.market_previous_is_market_day
    assert quote.market_change == pytest.approx(-20)


def test_single_raw_bar_has_no_change_and_requires_only_price_fields(retail_source):
    instance, _, responses = retail_source
    responses["none"] = {PRODUCT.symbol: pd.DataFrame({
        "trade_date": [DAY.isoformat()], "close": [4.8], "symbol": [PRODUCT.symbol],
    })}
    quote, = instance.fetch(DAY)
    assert quote.market_close == 4.8 and quote.market_date == DAY
    assert quote.market_change is None and quote.market_previous_date is None
    assert not quote.market_previous_is_market_day


@pytest.mark.parametrize("response", [
    None, [], {}, {"159941.SZ": pd.DataFrame()}, {PRODUCT.symbol: None},
    {PRODUCT.symbol: []}, {PRODUCT.symbol: pd.DataFrame({"close": [4.8]})},
    {PRODUCT.symbol: pd.DataFrame({"trade_date": [DAY.isoformat()]})},
    {PRODUCT.symbol: pd.DataFrame(), "159941.SZ": pd.DataFrame()},
])
def test_missing_or_malformed_raw_response_is_explicit_failure(retail_source, response):
    instance, _, responses = retail_source
    responses["none"] = response
    with pytest.raises(source.SourceError, match="不复权"):
        instance.fetch(DAY)


@pytest.mark.parametrize("value", [0, -1, None, float("nan"), float("inf"), "bad", True])
def test_raw_close_must_be_finite_and_positive(retail_source, value):
    instance, _, responses = retail_source
    responses["none"] = {PRODUCT.symbol: pd.DataFrame({
        "trade_date": [DAY.isoformat()], "close": [value],
    })}
    with pytest.raises(source.SourceError, match="收盘价无效"):
        instance.fetch(DAY)


@pytest.mark.parametrize("days,error", [
    (("2026-09-17", "bad"), "日期无效"),
    (("2026-09-17", "2026-02-30"), "日期无效"),
    (("2026-09-17", "2026-09-19"), "未来日期"),
    (("2026-09-18", "2026-09-18"), "重复日期"),
    (("2026-09-17", "20260918"), "日期无效"),
])
def test_raw_dates_are_validated(retail_source, days, error):
    instance, _, responses = retail_source
    responses["none"] = {PRODUCT.symbol: history([4, 4.8], days)}
    with pytest.raises(source.SourceError, match=error):
        instance.fetch(DAY)


@pytest.mark.parametrize("symbols", [
    ["159941.SZ", "159941.SZ"], [PRODUCT.symbol, None], [PRODUCT.symbol, "159941.SZ"],
])
def test_raw_embedded_symbols_must_match_requested_product(retail_source, symbols):
    instance, _, responses = retail_source
    responses["none"][PRODUCT.symbol]["symbol"] = symbols
    with pytest.raises(source.SourceError, match="代码不匹配"):
        instance.fetch(DAY)


def test_raw_duplicate_columns_are_rejected(retail_source):
    instance, _, responses = retail_source
    responses["none"] = {PRODUCT.symbol: pd.DataFrame(
        [[DAY.isoformat(), 4, 4.8]], columns=["trade_date", "close", "close"],
    )}
    with pytest.raises(source.SourceError, match="格式异常"):
        instance.fetch(DAY)


def test_oversized_raw_history_is_rejected(retail_source):
    instance, _, responses = retail_source
    responses["none"] = {PRODUCT.symbol: history(
        [4] * 31, pd.date_range(end=DAY, periods=31).strftime("%Y-%m-%d"),
    )}
    with pytest.raises(source.SourceError, match="格式异常"):
        instance.fetch(DAY)


def test_both_requests_are_bounded_selected_only_and_same_completed_day(
    retail_source, monkeypatch,
):
    instance, client, _ = retail_source
    products = [replace(PRODUCT, symbol=f"{513000 + i}.SH") for i in range(41)]
    monkeypatch.setattr(instance, "discover", lambda: products)
    client.klines.batch.side_effect = lambda symbols, **kw: {
        symbol: history([2, 2.2] if kw["adjust"] == "forward" else [4, 4.8])
        for symbol in symbols
    }
    quotes = instance.fetch(DAY)
    assert len(quotes) == 41
    expected_end = int(datetime(
        2026, 9, 18, 23, 59, 59, tzinfo=ZoneInfo("Asia/Shanghai"),
    ).timestamp() * 1000)
    calls = client.klines.batch.call_args_list
    assert len(calls) == 6
    for adjust in ("forward", "none"):
        selected = []
        matching = [call for call in calls if call.kwargs["adjust"] == adjust]
        assert [len(call.args[0]) for call in matching] == [20, 20, 1]
        for call in matching:
            selected.extend(call.args[0])
            assert call.kwargs == {
                "period": "1d", "count": 30, "adjust": adjust, "as_dataframe": True,
                "max_workers": 1, "batch_size": 20, "end_time": expected_end,
            }
        assert selected == [product.symbol for product in products]
    with sqlite3.connect(instance.engine.db_path) as conn:
        assert conn.execute("SELECT DISTINCT close FROM etf_daily ORDER BY close").fetchall() == [
            (2,), (2.2,),
        ]


def test_legacy_quote_construction_does_not_invent_raw_price():
    quote = Quote(PRODUCT, DAY, 2.2, 1234, 10, date(2026, 9, 17), True, False)
    assert quote.market_close is None and quote.market_date is None
    assert quote.market_change is None and quote.market_previous_date is None
    assert not quote.market_previous_is_market_day
