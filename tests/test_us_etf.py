import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import us_etf_main
from matrix_etf.core.config import Settings
from matrix_etf.us_etf import source
from matrix_etf.us_etf.card import MAX_CARD_BYTES, build_cards
from matrix_etf.us_etf.listing import (
    CATEGORIES, Product, Quote, classify, expected_close_day, make_quote, select_quotes,
)
from matrix_etf.us_etf.subscription import CreationQuota

NOW = datetime(2026, 9, 18, 18, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
DAY = NOW.date()
PRODUCT = Product("513100.SH", "纳斯达克100ETF", CATEGORIES[0])


def settings(**kwargs):
    return Settings(_env_file=None, feishu_webhook_url="", **kwargs)


def metadata(**changes):
    return {"symbol": PRODUCT.symbol, "name": PRODUCT.name, "type": "etf", **changes}


@pytest.mark.parametrize("name,category", [
    ("纳斯达克100ETF", 0), ("纳指100ETF", 0), ("NASDAQ100ETF", 0),
    ("标普500ETF", 1), ("标普500等权ETF", 1), ("S&P500ETF", 1),
    ("纳指ETF", 0), ("纳斯达克ETF", 0), ("纳斯达克生物科技ETF", 2), ("美国消费ETF", 2),
    ("美国股票ETF", 2), ("美股科技ETF", 2), ("道琼斯工业平均ETF", 2),
    ("道琼斯ETF", 2), ("美国50ETF", 2), ("纳指科技ETF", 2), ("纳指100科技ETF", 2),
    ("标普500消费精选ETF", 2), ("标普500生物科技ETF", 2),
    ("S&P500科技ETF", 2),
])
def test_explicit_equity_classification(name, category):
    assert classify(metadata(name=name)).category == CATEGORIES[category]


@pytest.mark.parametrize("name", [
    "标普ETF", "QDIIETF", "美国ETF", "海外科技ETF",
    "标普全球高端制造ETF", "标普香港ETF", "纳斯达克全球ETF",
    "纳指亚洲ETF", "标普亚太ETF", "标普500联接ETF", "纳指100ETF联接A",
    "标普500ETF(LOF)", "纳指100ETF美元", "标普500ETF港币",
    "纳指100ETF现汇", "美国国债ETF", "美国原油ETF", "美国黄金ETF",
    "美国油气ETF", "标普石油ETF", "美国REITETF", "恒生ETF", "美股原油期货ETF",
    "中国互联网ETF", "标普500指数A", "美国医疗基金", "纳指日本ETF",
    "标普500A股ETF", "纳指A股ETF", "纳指100ETF欧元", "标普500ETF(HKD)",
])
def test_false_positive_names_excluded(name):
    assert classify(metadata(name=name)) is None


@pytest.mark.parametrize("changes", [
    {"symbol": "SPY.US"}, {"symbol": "513100.US"}, {"symbol": "513100.SZ"},
    {"symbol": "159941.SH"}, {"symbol": "000001.SZ"}, {"symbol": "51310.SH"},
    {"symbol": "501018.SH"}, {"symbol": "160000.SZ"}, {"symbol": "５１３１００.SH"},
    {"type": "stock"}, {"type": "fund"}, {"type": None}, {"exchange": "US"},
])
def test_type_and_mainland_etf_symbol_required(changes):
    assert classify(metadata(**changes)) is None


def test_instrument_type_alias_and_sz_supported():
    info = metadata(symbol="159941.SZ", instrument_type="ETF")
    del info["type"]
    assert classify(info).symbol == "159941.SZ"


LIVE_CANDIDATES = [
    ("513850.SH", "美国50ETF易方达", 2),
    ("513650.SH", "标普500ETF南方", 1),
    ("513500.SH", "标普500ETF博时", 1),
    ("513870.SH", "纳指ETF富国", 0),
    ("513300.SH", "纳斯达克ETF华夏", 0),
    ("513400.SH", "道琼斯ETF鹏华", 2),
    ("513350.SH", "标普油气ETF富国", 2),
    ("513110.SH", "纳指ETF华泰柏瑞", 0),
    ("513390.SH", "纳指100ETF博时", 0),
    ("513290.SH", "纳指生物科技ETF汇添富", 2),
    ("513100.SH", "纳指ETF国泰", 0),
    ("159632.SZ", "纳斯达克ETF华安", 0),
    ("159655.SZ", "标普500ETF华夏", 1),
    ("159577.SZ", "美国50ETF汇添富", 2),
    ("159612.SZ", "标普500ETF国泰", 1),
    ("159501.SZ", "纳指ETF嘉实", 0),
    ("159502.SZ", "标普生物科技ETF嘉实", 2),
    ("159660.SZ", "纳指ETF汇添富", 0),
    ("159941.SZ", "纳指ETF广发", 0),
    ("159659.SZ", "纳斯达克100ETF招商", 0),
    ("159518.SZ", "标普油气ETF嘉实", 2),
    ("159509.SZ", "纳指科技ETF景顺", 2),
    ("159513.SZ", "纳斯达克100ETF大成", 0),
    ("159696.SZ", "纳指ETF易方达", 0),
    ("159529.SZ", "标普消费ETF景顺", 2),
]


@pytest.mark.parametrize("symbol,name,category", LIVE_CANDIDATES)
def test_confirmed_live_abbreviated_names(symbol, name, category):
    assert classify(metadata(symbol=symbol, name=name)).category == CATEGORIES[category]


def test_s_and_p_sectors_need_both_reviewed_code_and_name():
    assert classify(metadata(symbol="562060.SH", name="标普A股红利ETF华宝")) is None
    assert classify(metadata(symbol="513350.SH", name="标普原油期货ETF富国")) is None
    assert classify(metadata(symbol="513999.SH", name="标普油气ETF富国")) is None
    assert classify(metadata(symbol="159999.SZ", name="标普生物科技ETF嘉实")) is None
    assert classify(metadata(symbol="159529.SZ", name="标普A股消费ETF景顺")) is None


def test_all_twenty_five_confirmed_products_keep_sectors_separate():
    products = [
        classify(metadata(symbol=symbol, name=name))
        for symbol, name, _ in LIVE_CANDIDATES
    ]
    assert len(products) == len({p.symbol for p in products}) == 25
    assert [sum(p.category == category for p in products) for category in CATEGORIES] == [12, 4, 9]


def test_changes_invalid_values_and_missing_market_days():
    config = settings()
    quote = make_quote(PRODUCT, [
        ("2026-09-18", 11, 123), ("2026-09-17", 10, 456),
    ], DAY, config)
    assert quote.change == pytest.approx(10)
    assert quote.previous_is_market_day and not quote.stale
    gap = make_quote(PRODUCT, [
        ("2026-09-18", 11, "nan"), ("2026-09-17", 0, 20),
        ("2026-09-16", 10, 30),
    ], DAY, config)
    assert gap.change == pytest.approx(10)
    assert not gap.previous_is_market_day and gap.amount is None
    missing = make_quote(PRODUCT, [
        ("2026-09-18", float("inf"), -5), ("2026-09-17", 10, 20),
    ], DAY, config)
    assert missing.close is None and missing.change is None and missing.amount is None
    assert missing.trade_date == DAY
    assert make_quote(PRODUCT, [], DAY, config).trade_date is None
    assert make_quote(PRODUCT, [("bad", 10, 20)], DAY, config).close is None


def test_calendar_and_staleness():
    config = settings(cn_market_holidays="2026-09-17")
    quote = make_quote(PRODUCT, [("2026-09-18", 11, 20), ("2026-09-16", 10, 10)],
                       DAY, config)
    assert quote.previous_is_market_day
    assert expected_close_day(NOW.replace(day=20), config) == DAY
    assert expected_close_day(NOW.replace(hour=10), config) == date(2026, 9, 16)
    stale = make_quote(PRODUCT, [("2026-09-16", 10, 10)], DAY, config)
    assert stale.stale and stale.trade_date == date(2026, 9, 16)


@pytest.mark.parametrize("count", [1, 13, 50, 55])
def test_independent_fifty_cap_and_deterministic_sort(count):
    assert settings(recommendation_limit=1).recommendation_limit == 1
    quotes = [
        Quote(replace(PRODUCT, symbol=f"{513000+i}.SH"), DAY, 10, float(i // 2))
        for i in range(count)
    ]
    selected = select_quotes(quotes)
    assert len(selected) == min(count, 50)
    assert selected == select_quotes(quotes[::-1])
    assert selected[0].amount == (count - 1) // 2
    unknown = Quote(replace(PRODUCT, symbol="159941.SZ"))
    assert select_quotes([unknown, quotes[0]])[-1] == unknown
    with pytest.raises(ValueError):
        select_quotes([quotes[0], quotes[0]])


@pytest.fixture
def live_source(tmp_path, monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(source, "create_tickflow_client", lambda *a, **k: client)
    instance = source.USEtfSource(settings(db_path=str(tmp_path / "shared.db")))
    client.universes.get.return_value = {"symbols": [PRODUCT.symbol]}
    client.instruments.batch.return_value = [metadata()]
    frame = pd.DataFrame({
        "trade_date": ["2026-09-17", "2026-09-18"], "open": [10, 11],
        "high": [10, 11], "low": [10, 11], "close": [10, 11],
        "volume": [10, 20], "amount": [100, 220],
    })
    client.klines.batch.return_value = {PRODUCT.symbol: frame}
    return instance, client, frame


def test_source_reuses_tables_and_only_reads_current_bounded_window(live_source, monkeypatch):
    instance, client, frame = live_source
    old = frame.copy()
    old["trade_date"] = ["2000-01-01", "2000-01-02"]
    instance.engine._upsert_daily(instance.engine._normalize_kline(PRODUCT.symbol, old))
    monkeypatch.setattr(instance.engine, "get_ohlcv",
                        lambda *a: pytest.fail("unbounded full-history read"))
    statements = []
    connect = sqlite3.connect

    def tracked_connect(*a, **k):
        conn = connect(*a, **k)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(source.sqlite3, "connect", tracked_connect)
    quotes = instance.fetch(DAY)
    assert quotes[0].change == pytest.approx(10)
    assert any("date IN" in sql and "LIMIT 30" in sql for sql in statements)
    client.universes.get.assert_called_once_with("CN_ETF")
    args, kwargs = client.klines.batch.call_args_list[0]
    assert args == ([PRODUCT.symbol],)
    assert kwargs["count"] == 30 and kwargs["max_workers"] == 1
    assert kwargs["adjust"] == "forward"
    assert kwargs["end_time"] == int(
        datetime(2026, 9, 18, 23, 59, 59, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000
    )
    with sqlite3.connect(instance.engine.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"etf_daily", "etf_basic", "etf_metrics", "sqlite_sequence"}
        assert conn.execute("SELECT COUNT(*) FROM etf_daily").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM etf_metrics").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM etf_basic").fetchone()[0] == PRODUCT.name


def test_empty_new_fund_never_uses_old_cached_quote(live_source):
    instance, client, frame = live_source
    instance.engine._upsert_daily(instance.engine._normalize_kline(PRODUCT.symbol, frame))
    client.klines.batch.return_value = {PRODUCT.symbol: pd.DataFrame()}
    quotes = instance.fetch(DAY)
    assert len(quotes) == 1 and quotes[0].close is None


@pytest.mark.parametrize("response", [{}, None, {PRODUCT.symbol: None}])
def test_fetch_failure_is_not_local_fallback(live_source, response):
    instance, client, frame = live_source
    instance.engine._upsert_daily(instance.engine._normalize_kline(PRODUCT.symbol, frame))
    client.klines.batch.return_value = response
    with pytest.raises(source.SourceError):
        instance.fetch(DAY)


def test_network_failure_propagates(live_source):
    instance, client, _ = live_source
    client.klines.batch.side_effect = OSError("network")
    with pytest.raises(OSError):
        instance.fetch(DAY)


@pytest.mark.parametrize("bad_date", ["bad", "2026-09-19"])
def test_bad_daily_dates_fail_explicitly(live_source, bad_date):
    instance, _, frame = live_source
    frame.loc[1, "trade_date"] = bad_date
    with pytest.raises(source.SourceError):
        instance.fetch(DAY)


def test_bad_latest_values_remain_visible_and_stale_is_labelled(live_source):
    instance, client, frame = live_source
    raw = frame.copy()
    client.klines.batch.side_effect = lambda symbols, **kwargs: {
        PRODUCT.symbol: raw if kwargs["adjust"] == "none" else frame
    }
    instance.engine._upsert_daily(instance.engine._normalize_kline(PRODUCT.symbol, frame))
    frame["close"] = frame["close"].astype(float)
    frame.loc[1, "close"] = float("inf")
    frame.loc[1, "amount"] = -10
    quote = instance.fetch(date(2026, 9, 21))[0]
    assert quote.close is None and quote.amount is None and quote.stale
    with sqlite3.connect(instance.engine.db_path) as conn:
        assert conn.execute(
            "SELECT close FROM etf_daily WHERE symbol=? AND date=?",
            (PRODUCT.symbol, DAY.isoformat()),
        ).fetchone()[0] == 11


@pytest.mark.parametrize("records", [[], [{}], [metadata(name="")], [metadata(type="")],
                                       [metadata(name="全球科技ETF")]])
def test_incomplete_or_unclassified_metadata_fails(live_source, records):
    instance, client, _ = live_source
    client.instruments.batch.return_value = records
    with pytest.raises(source.SourceError):
        instance.fetch(DAY)
    client.klines.batch.assert_not_called()


@pytest.mark.parametrize("symbols", [[], None, ["x"] * 5001, [1]])
def test_universe_bounds_and_empty_failure(live_source, symbols):
    instance, client, _ = live_source
    client.universes.get.return_value = {"symbols": symbols}
    with pytest.raises(source.SourceError):
        instance.discover()


def test_metadata_batches_and_candidate_limit_before_any_daily_fetch(live_source):
    instance, client, _ = live_source
    symbols = [f"{513000+i}.SH" for i in range(301)]
    client.universes.get.return_value = {"symbols": symbols}
    client.instruments.batch.side_effect = lambda chunk: [
        metadata(symbol=s) for s in chunk
    ]
    with pytest.raises(source.SourceError):
        instance.fetch(DAY)
    assert [len(call.args[0]) for call in client.instruments.batch.call_args_list] == [250, 51]
    client.klines.batch.assert_not_called()


def test_metadata_filters_before_daily_sync_and_keeps_new_missing_products(live_source):
    instance, client, frame = live_source
    symbols = [f"{513000+i}.SH" for i in range(21)] + ["510300.SH"]
    client.universes.get.return_value = {"symbols": symbols}
    client.instruments.batch.side_effect = lambda chunk: [
        metadata(symbol=s, name="沪深300ETF" if s == "510300.SH" else PRODUCT.name)
        for s in chunk
    ]
    client.klines.batch.side_effect = lambda chunk, **kwargs: {
        s: pd.DataFrame() if s == "513020.SH" else frame for s in chunk
    }
    quotes = instance.fetch(DAY)
    assert len(quotes) == 21 and quotes[-1].close is None
    assert all(len(call.args[0]) <= 20 for call in client.klines.batch.call_args_list)
    assert {call.kwargs["adjust"] for call in client.klines.batch.call_args_list} == {"forward", "none"}
    assert all("510300.SH" not in call.args[0]
               for call in client.klines.batch.call_args_list)


def test_reviewed_aliases_never_expand_current_cn_etf_universe(live_source):
    instance, client, _ = live_source
    products = instance.discover()
    assert [p.symbol for p in products] == [PRODUCT.symbol]
    assert not {"513350.SH", "159518.SZ"}.intersection(p.symbol for p in products)
    client.instruments.batch.return_value = [metadata(
        symbol="513350.SH", name="标普油气ETF富国",
    )]
    with pytest.raises(source.SourceError, match="代码不匹配"):
        instance.discover()


@pytest.mark.parametrize("count", [1, 13, 50])
def test_cards_actual_wire_bytes_links_and_no_lost_rows(count):
    quotes = [
        Quote(replace(PRODUCT, symbol=f"{513000+i}.SH",
                      name="纳指ETF[伪链接](https://bad.invalid)<&>" + "名" * 190),
              DAY, 11, i, market_close=11, market_date=date(2026, 9, 17) if i == 0 else DAY,
              market_change=10, market_previous_date=date(2026, 9, 16))
        for i in range(count)
    ]
    cards = build_cards(quotes, candidate_count=count, now=NOW, expected=DAY)
    assert all(len(json.dumps(card).encode("utf-8")) <= MAX_CARD_BYTES for card in cards)
    if count == 50:
        assert len(cards) > 1
    text = json.dumps(cards, ensure_ascii=False)
    for quote in quotes:
        code = quote.product.symbol[:6]
        assert text.count(f"[{code}](https://fund.10jqka.com.cn/{code}/)") == 1
    assert "沪深市场" in text and "数据滞后" in text and "非单日涨跌幅" in text
    assert "&lt;&amp;&gt;" in text and "[伪链接](" not in text
    assert "可买额度" not in text and "推荐买入" not in text
    assert "参考收盘" in text and "1手100份" in text
    assert "前复权" not in text
    assert "不构成交易建议" not in text
    for boilerplate in ("最多", "明确候选", "展示", "全清单", "人民币成交额"):
        assert boilerplate not in text


def test_missing_quote_card_does_not_invent_values():
    text = json.dumps(build_cards([Quote(PRODUCT)], candidate_count=1,
                                 now=NOW, expected=DAY), ensure_ascii=False)
    assert "暂无行情" in text
    assert "¥0" not in text and "收盘日 暂无行情日期" in text


@pytest.mark.parametrize("status", ["open", "suspended", "unknown"])
def test_retail_card_never_displays_large_primary_creation_caps(status):
    quota = CreationQuota(
        effective_date=DAY, status=status, creation_unit=Decimal("1300000"),
        fund_cumulative=Decimal("0"), fund_net=Decimal("36000000"),
        account_cumulative=Decimal("1300000"),
    )
    text = json.dumps(build_cards([Quote(PRODUCT, close=99, subscription=quota,
                                        market_close=1.678, market_date=DAY)],
                                 candidate_count=1, now=NOW, expected=DAY), ensure_ascii=False)
    assert "1手100份 · 收盘估算 ¥167.80" in text
    assert "参考收盘 ¥1.6780" in text
    assert "东方财富" in text and "无需美国证券账户" in text
    assert "持仓市值不等于可用资金" in text
    for word in ("一级申购", "130万", "3600万", "申购上限", "¥99", "不限"):
        assert word not in text


def test_weekend_close_never_claims_realtime_buyability():
    sunday = NOW.replace(day=20)
    text = json.dumps(build_cards([Quote(PRODUCT, market_date=DAY, market_close=1.5)], candidate_count=1,
                                 now=sunday, expected=DAY), ensure_ascii=False)
    assert "收盘日 2026-09-18（非实时）" in text
    assert "实时停牌状态、溢价及账户交易权限请在下单前核对" in text
    assert "今日可买" not in text and "开放" not in text


def test_fifty_retail_products_keep_every_row_within_wire_limit():
    quotes = [Quote(replace(PRODUCT, symbol=f"{513000+i}.SH", name="长" * 190),
                    market_date=DAY, market_close=1.2345) for i in range(50)]
    cards = build_cards(quotes, candidate_count=50, now=NOW, expected=DAY)
    assert len(cards) > 1
    assert all(len(json.dumps(c).encode()) <= MAX_CARD_BYTES for c in cards)
    rows = [e["text"]["content"] for c in cards for e in c["card"]["elements"]
            if e.get("text", {}).get("content", "").startswith("**")]
    assert len(rows) == 50
    assert all("1手100份 · 收盘估算 ¥123.45" in row for row in rows)


@pytest.fixture
def cli(monkeypatch):
    factory = MagicMock()
    factory.return_value.fetch.return_value = [Quote(PRODUCT, DAY, 11, 220)]
    notifier = MagicMock()
    notifier.return_value.send_card.return_value = True
    monkeypatch.setattr(us_etf_main, "USEtfSource", factory)
    monkeypatch.setattr(us_etf_main, "FeishuNotifier", notifier)
    monkeypatch.setattr(us_etf_main, "get_settings", settings)
    monkeypatch.setattr(us_etf_main, "shanghai_now", lambda: NOW)
    monkeypatch.setattr(us_etf_main, "fetch_screen", lambda now: SimpleNamespace(issues=()))
    monkeypatch.setattr(us_etf_main, "build_hk_cards", lambda screen, now: [])
    return factory, notifier


def test_dryrun_and_normal_delivery(cli, capsys):
    factory, notifier = cli
    assert us_etf_main.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output[output.index("{"):])["msg_type"] == "interactive"
    notifier.assert_not_called()
    assert us_etf_main.main([]) == 0
    assert notifier.return_value.send_card.call_args.kwargs["webhook_key"] == "us_etf"
    factory.return_value.close.assert_called()


def test_cli_does_not_fetch_primary_creation_quotas(cli, monkeypatch):
    fetch = MagicMock()
    monkeypatch.setattr("matrix_etf.us_etf.subscription.fetch_quotas", fetch)
    assert us_etf_main.main(["--dry-run"]) == 0
    fetch.assert_not_called()


@pytest.mark.parametrize("issues,expected_status", [((), 0), (("sse-unavailable",), 1)])
def test_hong_kong_route_is_distinct_and_incomplete_checks_are_reported(
    cli, monkeypatch, issues, expected_status,
):
    _, notifier = cli
    hk_card = {"msg_type": "interactive", "card": {"elements": []}}
    monkeypatch.setattr(us_etf_main, "fetch_screen", lambda now: SimpleNamespace(issues=issues))
    monkeypatch.setattr(us_etf_main, "build_hk_cards", lambda screen, now: [hk_card])
    assert us_etf_main.main([]) == expected_status
    assert [call.kwargs["webhook_key"] for call in notifier.return_value.send_card.call_args_list] == [
        "us_etf", "hk_etf",
    ]
    assert notifier.return_value.send_card.call_args.args[0] == hk_card


def test_morning_cli_uses_previous_unadjusted_close(cli, capsys, monkeypatch):
    factory, _ = cli
    previous = date(2026, 9, 17)
    factory.return_value.fetch.return_value = [
        Quote(PRODUCT, previous, 11, market_date=previous, market_close=1.5)
    ]
    monkeypatch.setattr(us_etf_main, "shanghai_now", lambda: NOW.replace(hour=9, minute=35))
    assert us_etf_main.main(["--dry-run"]) == 0
    factory.return_value.fetch.assert_called_once_with(previous)
    text = capsys.readouterr().out
    assert "收盘日 2026-09-17" in text and "收盘估算 ¥150.00" in text
    assert "不代表今天" not in text and "数据滞后" not in text


def test_failed_delivery_returns_nonzero(cli):
    _, notifier = cli
    notifier.return_value.send_card.return_value = False
    assert us_etf_main.main([]) == 1


def test_paginated_delivery_stops_on_failure(cli):
    factory, notifier = cli
    factory.return_value.fetch.return_value = [
        Quote(replace(PRODUCT, symbol=f"{513000+i}.SH", name="纳指" + "长" * 195),
              DAY, 10, 100)
        for i in range(50)
    ]
    notifier.return_value.send_card.side_effect = [True, False]
    assert us_etf_main.main([]) == 1
    assert notifier.return_value.send_card.call_count == 2


def test_total_time_budget_installs_and_clears_alarm(monkeypatch):
    alarms = []
    handlers = []
    monkeypatch.setattr(us_etf_main.signal, "signal",
                        lambda *args: handlers.append(args) or "previous")
    monkeypatch.setattr(us_etf_main.signal, "alarm", alarms.append)
    with pytest.raises(TimeoutError):
        with us_etf_main.time_budget():
            handlers[0][1](None, None)
    assert alarms == [720, 0]
    assert handlers[-1][1] == "previous"


@pytest.mark.parametrize("dry", [False, True])
def test_failed_fetch_returns_nonzero_and_only_live_alerts(cli, dry):
    factory, notifier = cli
    factory.return_value.fetch.side_effect = source.SourceError("元数据分类失败")
    assert us_etf_main.main(["--dry-run"] if dry else []) == 1
    if dry:
        notifier.assert_not_called()
    else:
        notifier.return_value.send_card.assert_not_called()
        notifier.return_value.send_alert.assert_called_once()


def test_unexpected_programming_error_is_not_swallowed(cli):
    factory, _ = cli
    factory.return_value.fetch.side_effect = TypeError("unexpected programming error")
    with pytest.raises(TypeError, match="unexpected programming error"):
        us_etf_main.main(["--dry-run"])
    factory.return_value.close.assert_called_once()


def test_weekend_skipped_and_manual_force(cli, monkeypatch):
    factory, notifier = cli
    monkeypatch.setattr(us_etf_main, "shanghai_now", lambda: NOW.replace(day=20))
    assert us_etf_main.main([]) == 0
    factory.assert_not_called()
    notifier.assert_not_called()
    assert us_etf_main.main(["--force"]) == 0
    factory.return_value.fetch.assert_called_once_with(DAY)


def test_runner_missing_flock_and_busy_lock(tmp_path):
    root = Path(us_etf_main.PROJECT_ROOT)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mkdir").symlink_to(shutil.which("mkdir"))
    env = {**os.environ, "PATH": str(bindir), "MATRIX_ETF_HOME": str(tmp_path),
           "MATRIX_ETF_LOCK_FILE": str(tmp_path / "shared.lock")}
    command = ["/bin/bash", str(root / "scripts/run_us_etf.sh"), "--dry-run", "--force"]
    missing = subprocess.run(command, env=env, capture_output=True, text=True)
    assert missing.returncode == 1 and "requires flock" in missing.stderr
    fake = bindir / "flock"
    fake.write_text("#!/bin/bash\n[[ \"$1\" = '-n' && \"$2\" = '9' ]] || exit 9\nexit 1\n")
    fake.chmod(0o755)
    busy = subprocess.run(command, env=env, capture_output=True, text=True)
    assert busy.returncode == 0 and "skip" in busy.stdout
    assert (tmp_path / "shared.lock").exists()
    fake.write_text("#!/bin/bash\nexit 9\n")
    broken = subprocess.run(command, env=env, capture_output=True, text=True)
    assert broken.returncode == 9 and "lock failed" in broken.stderr


def test_job_schedule_and_resource_bounds():
    root = Path(us_etf_main.PROJECT_ROOT)
    timer = (root / "deploy/systemd/matrix-us-etf.timer").read_text()
    service = (root / "deploy/systemd/matrix-us-etf.service").read_text()
    runner = (root / "scripts/run_us_etf.sh").read_text()
    assert "OnCalendar=Mon..Fri 09:35:00 Asia/Shanghai" in timer
    assert "Persistent=false" in timer
    for value in ("TimeoutStartSec=15min", "MemoryMax=256M", "MemorySwapMax=64M",
                  "Nice=15", "IOSchedulingClass=idle"):
        assert value in service
    assert ".matrix_etf.lock" in runner and "MATRIX_ETF_LOCK_FILE" in runner
    assert "us_etf_main.py" in runner
    assert "19:15:00" in (root / "deploy/systemd/matrix-etf.timer").read_text()
