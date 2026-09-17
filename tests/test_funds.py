import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import fund_main
from matrix_etf.core.config import Settings
from matrix_etf.funds.card import build_card
from matrix_etf.funds.catalog import load_catalog, parse_candidates, us_index_category
from matrix_etf.funds.monitor import select_funds
from matrix_etf.funds.source import FundQuote, FundSourceError
from matrix_etf.notify.feishu import FeishuNotifier


def quote(code="270042", **changes):
    item = FundQuote(
        code, "广发纳斯达克100ETF联接人民币(QDII)A", "指数型-海外股票",
        "限大额", Decimal("2"), Decimal("2"), Decimal("0.13"),
    )
    return replace(item, **changes)


def test_checked_in_catalog_has_only_unique_codes_grouped_by_product():
    groups = load_catalog(Path(fund_main.PROJECT_ROOT) / "config/us_funds.json")
    codes = [code for group in groups for code in group]
    assert len(groups) == 24
    assert len(codes) == len(set(codes))
    assert {"270042", "006479"} <= set(next(g for g in groups if "270042" in g))
    assert not {"017642", "017643", "513500"}.intersection(codes)


@pytest.mark.parametrize("data", [
    {}, [], {"version": True, "groups": [["270042"]]},
    {"version": 1, "groups": []}, {"version": 1, "groups": [[]]},
    {"version": 1, "groups": [["270042"], ["270042"]]},
    {"version": 1, "groups": [[270042]]}, {"version": 1, "groups": [["bad"]]},
    {"version": 1, "groups": [["２７００４２"]]},
])
def test_invalid_catalog_fails(tmp_path, data):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


@pytest.mark.parametrize("name", [
    "摩根标普500指数(QDII)美钞", "摩根标普500指数(QDII)美汇",
    "广发纳斯达克100ETF联接美元A", "标普500ETF博时",
    "恒生科技指数", "全球黄金QDII", "美国美元债",
])
def test_non_rmb_or_out_of_scope_products_are_not_admitted(name):
    assert us_index_category(name, "指数型-海外股票") is None


def test_sp500_equal_weight_is_labelled_distinctly():
    assert us_index_category(
        "大成标普500等权重指数(QDII)A人民币", "指数型-海外股票"
    ) == "标普500等权"


def test_suspended_status_wins_over_leftover_limit():
    result = select_funds(
        [("050025",)], {"050025": quote("050025", status="暂停申购", daily_limit=Decimal("100"))}
    )
    assert result.groups == ()
    assert result.unavailable_count == 1
    assert result.unknown == ()


@pytest.mark.parametrize("changes", [
    {"daily_limit": None}, {"daily_limit": Decimal(0)},
    {"daily_limit": Decimal("100000000000")}, {"daily_limit": Decimal("1")},
    {"minimum": None}, {"minimum": Decimal(0)}, {"minimum": Decimal("0.001")},
    {"daily_limit": Decimal("2.001")},
    {"status": ""}, {"status": "仅定投"}, {"status": "new-status"},
    {"issue": "Invalid daily_limit"}, {"fund_type": "债券型-长债"},
])
def test_unknown_or_ambiguous_data_never_looks_buyable(changes):
    result = select_funds([("270042",)], {"270042": quote(**changes)})
    assert result.groups == ()
    assert len(result.unknown) == 1


def test_missing_codes_are_surfaced_and_all_missing_is_failure():
    result = select_funds([("270042", "006479")], {"270042": quote()})
    assert result.unknown == (("006479", "数据源未返回"),)
    with pytest.raises(FundSourceError):
        select_funds([("270042",)], {})


def test_ten_product_cap_ordering_and_no_share_class_limit_addition():
    catalog = []
    quotes = {}
    for i in range(12):
        codes = (f"{i * 2 + 1:06}", f"{i * 2 + 2:06}")
        catalog.append(codes)
        for code in codes:
            quotes[code] = quote(code, minimum=Decimal(1), daily_limit=Decimal(i + 1))
    result = select_funds(catalog, quotes)
    assert len(result.groups) == 10
    assert result.eligible_groups == 12
    assert result.groups[0][0].daily_limit == 12
    assert result.groups[-1][0].daily_limit == 3
    assert all(len(group) == 2 for group in result.groups)
    assert select_funds(catalog[::-1], quotes) == result
    assert len(select_funds(catalog, quotes, 2).groups) == 2


@pytest.mark.parametrize("limit", [0, 11, -1, True, 1.5])
def test_invalid_recommendation_limit(limit):
    with pytest.raises(ValueError):
        select_funds([("270042",)], {"270042": quote()}, limit)


def test_card_links_labels_risk_and_fund_name_escaping():
    q = quote(name="广发纳斯达克100[A]<at user_id=\"all\">")
    selected = select_funds([("270042",)], {"270042": q})
    card = build_card(
        selected, datetime(2026, 9, 19, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")), "周末"
    )
    text = json.dumps(card, ensure_ascii=False)
    assert "https://fund.eastmoney.com/270042.html" in text
    assert "xueqiu.com" not in text
    assert "公布日累计上限 ¥2" in text
    assert "额度不是个人剩余额度" in text and "不可相加" in text
    assert "非规则生效时间" in text and "周末" in text
    assert "<at " not in text
    assert "不是买入建议" in text


def test_empty_card_is_explicit_and_does_not_make_up_candidates():
    selected = select_funds([("270042",)], {"270042": quote(status="暂停申购")})
    card = build_card(selected, datetime.now(ZoneInfo("Asia/Shanghai")))
    assert "本次没有可确认申购状态及额度" in json.dumps(card, ensure_ascii=False)
    assert card["card"]["header"]["template"] == "orange"


def test_discovery_proposes_only_new_eligible_names_without_auto_approval():
    rows = [
        ["270042", "GF", quote().name, "指数型-海外股票", "GF"],
        ["000123", "TH", "天弘标普500人民币A", "QDII-FOF", "TH"],
        ["017642", "MG", "摩根标普500美钞", "指数型-海外股票", "MG"],
        ["513500", "BS", "标普500ETF博时", "指数型-海外股票", "BS"],
    ]
    result = parse_candidates("var r = " + json.dumps(rows) + ";", {"270042"})
    assert result == [{"code": "000123", "name": "天弘标普500人民币A", "category": "标普500"}]
    with pytest.raises(ValueError):
        parse_candidates("var r = [];", set())
    with pytest.raises(ValueError):
        parse_candidates("var r = []; alert(1)", set())


@pytest.fixture
def cli(monkeypatch, tmp_path):
    catalog = tmp_path / "codes.json"
    catalog.write_text('{"version": 1, "groups": [["270042"]]}', encoding="utf-8")
    settings = Settings(_env_file=None, feishu_webhook_url="https://example.com/hook")
    notifier = MagicMock()
    notifier.send_card.return_value = True
    monkeypatch.setattr(fund_main, "get_settings", lambda: settings)
    monkeypatch.setattr(fund_main, "FeishuNotifier", lambda settings: notifier)
    fetch = MagicMock(return_value={"270042": quote()})
    monkeypatch.setattr(fund_main, "fetch_quotes", fetch)
    return catalog, notifier, fetch


def test_cli_dry_run_needs_no_webhook_and_creates_no_database(cli, capsys, monkeypatch):
    catalog, notifier, fetch = cli
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    before = set(catalog.parent.iterdir())
    assert fund_main.main(["--dry-run", "--catalog", str(catalog)]) == 0
    assert "公布日累计上限" in capsys.readouterr().out
    assert set(catalog.parent.iterdir()) == before
    notifier.send_card.assert_not_called()
    notifier.send_alert.assert_not_called()
    fetch.assert_called_once()


def test_cli_fetches_again_on_each_run_and_uses_fund_route(cli):
    catalog, notifier, fetch = cli
    args = ["--catalog", str(catalog)]
    assert fund_main.main(args) == fund_main.main(args) == 0
    assert fetch.call_count == 2
    assert notifier.send_card.call_args.kwargs == {"webhook_key": "fund_us"}


def test_cli_source_failure_alerts_and_exits_nonzero(cli):
    catalog, notifier, fetch = cli
    fetch.side_effect = FundSourceError("schema changed")
    assert fund_main.main(["--catalog", str(catalog)]) == 1
    notifier.send_card.assert_not_called()
    assert notifier.send_alert.call_args.kwargs["webhook_key"] == "fund_us"


def test_cli_delivery_failure_exits_nonzero(cli):
    catalog, notifier, fetch = cli
    notifier.send_card.return_value = False
    assert fund_main.main(["--catalog", str(catalog)]) == 1


def test_discovery_does_not_rewrite_catalog_or_send_an_empty_update(cli, monkeypatch):
    catalog, notifier, fetch = cli
    content = catalog.read_bytes()
    rows = [["270042", "GF", quote().name, "指数型-海外股票", "GF"]]
    monkeypatch.setattr(fund_main, "fetch_catalog_text", lambda **kw: "var r = " + json.dumps(rows))
    assert fund_main.main(["--discover", "--catalog", str(catalog)]) == 0
    assert catalog.read_bytes() == content
    notifier.send_card.assert_not_called()
    fetch.assert_not_called()


@pytest.mark.parametrize("success", [True, False])
def test_generic_card_sender_reports_actual_delivery(monkeypatch, success):
    settings = Settings(
        _env_file=None, feishu_webhook_url="https://example.com/hook",
        feishu_retry_attempts=1,
    )
    response = MagicMock(status_code=200, json=lambda: {"code": 0 if success else 1})
    monkeypatch.setattr("requests.post", lambda *a, **kw: response)
    assert FeishuNotifier(settings).send_card({"msg_type": "interactive"}, "fund_us") is success


@pytest.mark.parametrize("body", [[], None, "not an object"])
def test_generic_card_sender_rejects_nonobject_response(monkeypatch, body):
    settings = Settings(
        _env_file=None, feishu_webhook_url="https://example.com/hook",
        feishu_retry_attempts=1,
    )
    response = MagicMock(status_code=200, json=lambda: body)
    monkeypatch.setattr("requests.post", lambda *a, **kw: response)
    assert FeishuNotifier(settings).send_card({"msg_type": "interactive"}) is False
