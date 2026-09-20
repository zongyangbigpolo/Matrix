import json
import re
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import fund_main
from matrix_etf.core.config import Settings
from matrix_etf.funds.card import MAX_CARD_BYTES, _fund_name_and_class, build_card, build_cards
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


@pytest.mark.parametrize("count", [13, 50, 53])
def test_fifty_product_cap_ordering_and_no_share_class_limit_addition(count):
    catalog = []
    quotes = {}
    for i in range(count):
        codes = (f"{i * 2 + 1:06}", f"{i * 2 + 2:06}")
        catalog.append(codes)
        for code in codes:
            quotes[code] = quote(code, minimum=Decimal(1), daily_limit=Decimal(i + 1))
    result = select_funds(catalog, quotes)
    assert len(result.groups) == min(count, 50)
    assert result.eligible_groups == count
    assert result.eligible_shares == count * 2
    total = Decimal(count * (count + 1) // 2)
    assert result.single_share_total == total
    assert result.category_totals[0].products == count
    assert result.category_totals[0].daily_limit == total
    assert result.groups[0][0].daily_limit == count
    assert result.groups[-1][0].daily_limit == max(1, count - 49)
    assert all(len(group) == 2 for group in result.groups)
    assert select_funds(catalog[::-1], quotes) == result
    smaller = select_funds(catalog, quotes, 2)
    assert len(smaller.groups) == 2
    assert smaller.single_share_total == result.single_share_total
    assert smaller.category_totals == result.category_totals


@pytest.mark.parametrize("limit", [0, 51, -1, True, 1.5])
def test_invalid_recommendation_limit(limit):
    with pytest.raises(ValueError):
        select_funds([("270042",)], {"270042": quote()}, limit)


def test_card_links_summary_and_fund_name_escaping():
    q = quote(name="广发纳斯达克100[A]<at user_id=\"all\">")
    selected = select_funds([("270042",)], {"270042": q})
    card = build_card(
        selected, datetime(2026, 9, 19, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")), "周末"
    )
    text = json.dumps(card, ensure_ascii=False)
    assert "https://fund.10jqka.com.cn/270042/" in text
    assert "xueqiu.com" not in text
    assert "每日可申购 **¥2**" in text
    assert "美股总计：¥2 · 1只基金" in text
    assert "同一基金各份额取最高额度汇总" in text
    assert "场外申购" in text
    assert "标普合计：¥0 · 0只基金" in text
    assert "纳斯达克合计：¥2 · 1只基金" in text
    assert "周末" in text
    assert "<at " not in text
    for boilerplate in (
        "额度不是个人剩余额度", "不可相加", "公开入口无稳定性", "单笔限制",
        "不是买入建议", "汇率", "最终以实际交易页面", "非规则生效时间",
        "最多", "展示", "分条发送", "未展开",
    ):
        assert boilerplate not in text


@pytest.mark.parametrize("name,expected", [
    ("宝盈纳斯达克100指数发起(QDII)A人民币", ("宝盈纳斯达克100", "A")),
    ("天弘标普500发起(QDII-FOF)C", ("天弘标普500(FOF)", "C")),
    ("大成标普500等权重指数(QDII)C人民币", ("大成标普500等权重", "C")),
    ("国泰纳斯达克100指数", ("国泰纳斯达克100", "")),
    ("华泰柏瑞纳斯达克100ETF发起式联接(QDII)A", ("华泰柏瑞纳斯达克100", "A")),
    ("嘉实纳斯达克100ETF发起联接(QDII)I人民币", ("嘉实纳斯达克100", "I")),
    ("易方达纳斯达克100ETF联接（QDII-LOF）A（人民币）", ("易方达纳斯达克100(LOF)", "A")),
])
def test_short_names_keep_share_classes_and_distinct_product_types(name, expected):
    assert _fund_name_and_class(name) == expected


def test_summary_covers_hidden_products_and_category_totals():
    catalog = [("000001", "000002"), ("000003",), ("000004",), ("000005",), ("000006",)]
    quotes = {
        "000001": quote("000001", daily_limit=Decimal("100")),
        "000002": quote("000002", daily_limit=Decimal("100")),
        "000003": quote("000003", name="天弘标普500人民币A", daily_limit=Decimal("10")),
        "000004": quote("000004", name="大成标普500等权重人民币A", daily_limit=Decimal("20")),
        "000005": quote("000005", status="暂停申购", daily_limit=Decimal("9999")),
        "000006": quote("000006", daily_limit=None),
    }
    selected = select_funds(catalog, quotes, limit=1)
    assert selected.eligible_groups == 3
    assert selected.eligible_shares == 4
    assert selected.single_share_total == Decimal("130")
    assert [(c.category, c.products, c.daily_limit) for c in selected.category_totals] == [
        ("纳斯达克100", 1, Decimal("100")),
        ("标普500", 1, Decimal("10")),
        ("标普500等权", 1, Decimal("20")),
    ]
    card = build_card(selected, datetime.now(ZoneInfo("Asia/Shanghai")))
    text = json.dumps(card, ensure_ascii=False)
    assert "美股总计：¥130 · 3只基金" in text
    assert "标普合计：¥30 · 2只基金" in text
    assert "纳斯达克合计：¥100 · 1只基金" in text
    assert text.index("标普合计") < text.index("纳斯达克合计") < text.index("美股总计")
    assert "部分可申购基金，顶部合计包含全部可申购产品" in text
    assert "9999" not in text and "9,999" not in text
    assert "另有1个份额信息未确认" in text


def test_different_share_class_limits_are_not_hidden_or_added():
    quotes = {
        "000001": quote("000001", name="测试标普500A", minimum=Decimal("1"), daily_limit=Decimal("100")),
        "000002": quote("000002", name="测试标普500C", minimum=Decimal("10"), daily_limit=Decimal("200")),
    }
    selected = select_funds([("000001", "000002")], quotes)
    assert selected.single_share_total == Decimal("200")
    text = json.dumps(build_card(selected, datetime.now(ZoneInfo("Asia/Shanghai"))), ensure_ascii=False)
    assert "[A类 000001](https://fund.10jqka.com.cn/000001/)" in text
    assert "[C类 000002](https://fund.10jqka.com.cn/000002/)" in text
    assert "每日可申购 ¥100 · ¥1起" in text
    assert "每日可申购 ¥200 · ¥10起" in text
    assert "¥300" not in text


def test_empty_card_is_explicit_and_does_not_make_up_candidates():
    selected = select_funds([("270042",)], {"270042": quote(status="暂停申购")})
    card = build_card(selected, datetime.now(ZoneInfo("Asia/Shanghai")))
    text = json.dumps(card, ensure_ascii=False)
    assert "今天没有查到额度明确、可申购的基金" in text
    assert "美股总计：¥0 · 0只基金" in text
    assert "标普合计：¥0 · 0只基金" in text
    assert "纳斯达克合计：¥0 · 0只基金" in text
    assert selected.single_share_total == 0 and not selected.category_totals
    assert card["card"]["header"]["template"] == "orange"
    assert build_cards(selected, datetime.now(ZoneInfo("Asia/Shanghai")))


def test_thirteen_products_fit_one_card_without_hidden_funds():
    codes = [f"{i:06}" for i in range(1, 14)]
    selected = select_funds([(code,) for code in codes], {code: quote(code) for code in codes})
    cards = build_cards(selected, datetime.now(ZoneInfo("Asia/Shanghai")))
    assert len(cards) == 1
    text = json.dumps(cards, ensure_ascii=False)
    assert "13只基金" in text and "展示" not in text and "未展开" not in text
    for code in codes:
        assert f"https://fund.10jqka.com.cn/{code}/" in text


def test_fifty_large_products_are_paged_without_losing_groups_or_shares():
    catalog = []
    quotes = {}
    for i in range(50):
        codes = tuple(f"{i * 6 + j + 1:06}" for j in range(6))
        catalog.append(codes)
        for j, code in enumerate(codes):
            quotes[code] = quote(
                code, name="测试" * 80 + "纳斯达克100" + "ACDEFI"[j],
                daily_limit=Decimal(j + 2),
            )
    selected = select_funds(catalog, quotes)
    cards = build_cards(selected, datetime.now(ZoneInfo("Asia/Shanghai")), "周末")
    assert len(cards) > 1
    indices = []
    for card in cards:
        assert len(json.dumps(card).encode("utf-8")) <= MAX_CARD_BYTES
        text = json.dumps(card, ensure_ascii=False)
        assert "美股总计：¥350 · 50只基金" in text
        assert "展示" not in text and "分条发送" not in text
        assert "未展开" not in text and "周末" in text
        for element in card["card"]["elements"]:
            match = re.match(r"\*\*(\d+)\. \[", element.get("text", {}).get("content", ""))
            if match:
                indices.append(int(match[1]))
    assert indices == list(range(1, 51))
    text = json.dumps(cards, ensure_ascii=False)
    for code in quotes:
        assert f"https://fund.10jqka.com.cn/{code}/" in text


def test_one_oversized_product_fails_explicitly(monkeypatch):
    selected = select_funds([("270042",)], {"270042": quote()})
    monkeypatch.setattr("matrix_etf.funds.card.MAX_CARD_BYTES", 100)
    with pytest.raises(ValueError, match="One fund product exceeds"):
        build_cards(selected, datetime.now(ZoneInfo("Asia/Shanghai")))


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
    assert "美股总计" in capsys.readouterr().out
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


@pytest.mark.parametrize("count", [13, 50, 53])
def test_cli_funds_ignore_stock_limit_and_send_all_selected_products(cli, count):
    catalog, notifier, fetch = cli
    codes = [f"{i:06}" for i in range(1, count + 1)]
    catalog.write_text(json.dumps({"version": 1, "groups": [[code] for code in codes]}))
    fetch.return_value = {code: quote(code) for code in codes}
    fund_main.get_settings().recommendation_limit = 1
    assert fund_main.main(["--catalog", str(catalog)]) == 0
    cards = [call.args[0] for call in notifier.send_card.call_args_list]
    for card in cards:
        assert len(json.dumps(card).encode("utf-8")) <= MAX_CARD_BYTES
    text = json.dumps(cards, ensure_ascii=False)
    for code in codes[:50]:
        assert f"https://fund.10jqka.com.cn/{code}/" in text
    for code in codes[50:]:
        assert f"https://fund.10jqka.com.cn/{code}/" not in text
    assert fund_main.get_settings().recommendation_limit == 1
    assert Settings(_env_file=None, feishu_webhook_url="").recommendation_limit == 10
    fetch.assert_called_once()


def test_cli_stops_and_reports_partial_delivery_failure(cli, monkeypatch):
    catalog, notifier, fetch = cli
    codes = [f"{i:06}" for i in range(1, 51)]
    catalog.write_text(json.dumps({"version": 1, "groups": [[code] for code in codes]}))
    fetch.return_value = {code: quote(code) for code in codes}
    monkeypatch.setattr("matrix_etf.funds.card.MAX_CARD_BYTES", 3000)
    notifier.send_card.side_effect = [True, False]
    assert fund_main.main(["--catalog", str(catalog)]) == 1
    assert notifier.send_card.call_count == 2


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
