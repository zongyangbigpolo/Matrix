import io
import json
import zipfile
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape

import pytest
import requests

from matrix_etf.us_etf import hong_kong as hk

NOW = datetime(2026, 9, 20, 9, 35)
PRODUCT = hk.HKProduct("03034", "CSOP NASDAQ100", "HKD", 100)
HEADERS = [
    "Stock Code", "Name of Securities", "Category", "Sub-Category",
    "Board Lot", "ISIN", "Trading Currency",
]


def etf(code="03034", name="CSOP NASDAQ100", lot="100", currency="HKD",
        category="Exchange Traded Products", subcategory="Exchange Traded Funds"):
    return [code, name, category, subcategory, lot, "HK0000825338", currency]


def workbook(rows=None, stamp="21/09/2026", headers=None, extra=None, inline=False,
             preamble=True):
    rows = [etf()] if rows is None else rows
    values = ([["List of Securities"], [f"Updated as at {stamp}"]] if preamble else [])
    values += [headers or HEADERS] + rows
    strings = []
    xml_rows = []
    for index, row in enumerate(values, 1):
        cells = []
        for column, value in enumerate(row):
            ref = f"{chr(65 + column)}{index}"
            if inline:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
            else:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        xml_rows.append(f'<row r="{index}">{"".join(cells)}</row>')
    buffer = io.BytesIO()
    ns = hk._NS[1:-1]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">' + "".join(
            f"<si><t>{escape(value)}</t></si>" for value in strings
        ) + "</sst>")
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>'
                         + "".join(xml_rows) + "</sheetData></worksheet>")
        for path, data in (extra or {}).items():
            archive.writestr(path, data)
    return buffer.getvalue()


def sse_row(code="03034", stamp="2026-09-18", flag="1", kind="ETF"):
    return {
        "SECURITY_CODE": code, "ABBR_CN": "测试", "ABBR_EN": "TEST",
        "SECURITY_TYPE": kind, "UPDATE_DATE": stamp, "TRADE_FLAG": flag,
    }


def sse_page(rows, total=None, page=1, result_only=False):
    total = len(rows) if total is None else total
    metadata = {"pageNo": page, "pageSize": 1000, "total": total,
                "pageCount": (total + 999) // 1000}
    if not result_only:
        metadata["data"] = rows
    return {"pageHelp": metadata, "result": rows}


def mock_pages(monkeypatch, *pages):
    responses = iter(pages)
    calls = []

    def download(url, *, params=None):
        assert url == hk.SSE_URL
        calls.append(params)
        return json.dumps(next(responses)).encode()

    monkeypatch.setattr(hk, "_download", download)
    return calls


def text(screen):
    return json.dumps(hk.build_cards(screen, NOW), ensure_ascii=False)


@pytest.mark.parametrize("inline", [False, True])
def test_catalogue_realistic_rows_and_no_guessed_codes(inline):
    rows = [
        etf("02834", "ISHARESND100", "10"),
        etf("03020", "X TRMSCIUSA", "15"),
        etf(),
        etf("03086", "CAM NASDAQ100", "200"),
        etf("03195", "HS S&P500", "100"),
        etf("03146", "CAM 20 UST", "1"),
        etf("09086", "CAM NASDAQ100-U", "200", "USD"),
        etf("83195", "HS S&P500-R", "100", "RMB"),
        etf("07000", "NASDAQ100", subcategory="Leveraged and Inverse Products"),
        etf("03001", "A NASDAQ100"),
        etf("03002", "CC NASDAQ100"),
        etf("03003", "NASDAQ100 COVERED CALL"),
        etf("03004", "MSCIUSA BOND"),
        etf("03005", "GOLD"),
    ]
    products, as_of = hk.parse_catalogue(workbook(rows, inline=inline), NOW)
    assert as_of == date(2026, 9, 21)
    assert [(p.code, p.board_lot) for p in products] == [
        ("02834", 10), ("03020", 15), ("03034", 100), ("03086", 200), ("03195", 100),
    ]
    assert all(p.currency == "HKD" for p in products)


@pytest.mark.parametrize("stamp,valid", [
    ("13/09/2026", True), ("12/09/2026", False), ("20/09/2026", True),
    ("23/09/2026", True), ("24/09/2026", False), ("31/09/2026", False),
])
def test_catalogue_date_window(stamp, valid):
    if valid:
        hk.parse_catalogue(workbook(stamp=stamp), NOW)
    else:
        with pytest.raises(hk.SourceError):
            hk.parse_catalogue(workbook(stamp=stamp), NOW)


@pytest.mark.parametrize("lot", ["0", "-1", "1.5", "100.0", "NaN", "", "True"])
def test_invalid_board_lots(lot):
    with pytest.raises(hk.SourceError):
        hk.parse_catalogue(workbook([etf(lot=lot)]), NOW)


@pytest.mark.parametrize("rows", [
    [etf(), etf()], [etf(code="003034")], [etf(code="0")],
    [etf(code="30.34")], [],
])
def test_invalid_codes_duplicates_empty(rows):
    with pytest.raises(hk.SourceError):
        hk.parse_catalogue(workbook(rows), NOW)


def test_numeric_code_is_padded_from_workbook():
    products, _ = hk.parse_catalogue(workbook([etf(code="3034")]), NOW)
    assert products[0].code == "03034"


@pytest.mark.parametrize("headers", [HEADERS[:-1], HEADERS + ["Stock Code"]])
def test_required_unique_headers(headers):
    with pytest.raises(hk.SourceError):
        hk.parse_catalogue(workbook(headers=headers), NOW)


def test_archive_limits_and_unsafe_paths(monkeypatch):
    for path in ("../bad.xml", "/bad.xml", "xl\\bad.xml"):
        with pytest.raises(hk.SourceError):
            hk.parse_catalogue(workbook(extra={path: "bad"}), NOW)
    monkeypatch.setattr(hk, "MAX_UNCOMPRESSED_BYTES", 20)
    with pytest.raises(hk.SourceError):
        hk.parse_catalogue(workbook(), NOW)
    monkeypatch.setattr(hk, "MAX_DOWNLOAD_BYTES", 20)
    with pytest.raises(hk.SourceError):
        hk.parse_catalogue(workbook(), NOW)


def test_dtd_entity_and_corrupt_zip_rejected():
    for declaration in ('<!DOCTYPE worksheet>', '<!ENTITY test "boom">'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/worksheets/sheet1.xml", declaration + "<worksheet/>")
        with pytest.raises(hk.SourceError):
            hk.parse_catalogue(buffer.getvalue(), NOW)
    with pytest.raises(hk.SourceError):
        hk.parse_catalogue(b"not a workbook", NOW)


def test_complete_sse_pagination_and_result_fallback(monkeypatch):
    rows = [sse_row(f"{i:05d}", kind="股票") for i in range(1, 1001)]
    calls = mock_pages(monkeypatch, sse_page(rows, total=1001),
                       sse_page([sse_row()], total=1001, page=2, result_only=True))
    eligible, as_of = hk._fetch_sse(NOW)
    assert eligible == {"03034"}
    assert as_of == date(2026, 9, 18)
    assert [p["pageHelp.pageNo"] for p in calls] == [1, 2]


@pytest.mark.parametrize("stamp", ["2026-09-21", "2026-09-12", "2026-02-30", "", "18/09/2026"])
def test_sse_invalid_dates(monkeypatch, stamp):
    mock_pages(monkeypatch, sse_page([sse_row(stamp=stamp)]))
    with pytest.raises(hk.SourceError):
        hk._fetch_sse(NOW)


@pytest.mark.parametrize("rows", [
    [sse_row(flag="0")], [sse_row(flag="2")], [sse_row(flag=None)],
    [sse_row(code="3034")], [sse_row(code="00000")],
    [sse_row(), sse_row()], [sse_row(kind="unknown")],
    [sse_row(), sse_row("03195", stamp="2026-09-17")],
    [sse_row(kind="股票")],
])
def test_sse_unverified_rows_are_unknown(monkeypatch, rows):
    mock_pages(monkeypatch, sse_page(rows))
    with pytest.raises(hk.SourceError):
        hk._fetch_sse(NOW)


@pytest.mark.parametrize("field,value", [
    ("total", 2), ("total", 0), ("total", True), ("total", "1.0"),
    ("pageCount", 2), ("pageNo", 2), ("pageSize", 50),
])
def test_sse_truncation_and_pagination_validation(monkeypatch, field, value):
    payload = sse_page([sse_row()])
    payload["pageHelp"][field] = value
    mock_pages(monkeypatch, payload)
    with pytest.raises(hk.SourceError):
        hk._fetch_sse(NOW)


def test_fetch_isolates_expected_failure_and_sanitizes_log(monkeypatch, caplog):
    def fail(url, **kwargs):
        raise requests.Timeout("secret credential in upstream URL")

    monkeypatch.setattr(hk, "_download", fail)
    screen = hk.fetch_screen(NOW)
    assert screen.products == []
    assert screen.catalogue_date is None
    assert screen.sse_eligible is None
    assert screen.szse_eligible is None
    assert len(screen.issues) == 3
    assert "secret" not in caplog.text
    assert "暂未确认可通过港股通买入" in text(screen)
    assert "未知" in text(screen)


def test_fetch_partial_success_and_unexpected_errors_propagate(monkeypatch):
    def download(url, **kwargs):
        if url == hk.CATALOGUE_URL:
            return workbook()
        raise requests.ConnectionError("unavailable")

    monkeypatch.setattr(hk, "_download", download)
    screen = hk.fetch_screen(NOW)
    assert screen.products == [PRODUCT]
    assert screen.sse_eligible is None
    assert len(screen.issues) == 2

    def bug(url, **kwargs):
        raise RuntimeError("programming error")

    monkeypatch.setattr(hk, "_download", bug)
    with pytest.raises(RuntimeError):
        hk.fetch_screen(NOW)


def test_future_directory_cannot_be_promoted_to_current_access():
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 21), date(2026, 9, 18), {"03034"})
    assert hk.sse_status(screen, PRODUCT, NOW) == "unknown"
    rendered = text(screen)
    assert "暂未确认可通过港股通买入的美股ETF" in rendered
    assert "未来日期" in rendered
    assert "2026-09-21" in rendered
    assert "可买标记已核验" not in rendered
    assert PRODUCT.code not in rendered and PRODUCT.name not in rendered
    assert len(hk.build_cards(screen, NOW)[0]["card"]["elements"]) == 1


def test_sse_positive_is_not_brokerage_confirmation():
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 20), date(2026, 9, 18), {"03034"})
    assert hk.sse_status(screen, PRODUCT, NOW) == "confirmed_eligible"
    rendered = text(screen)
    assert "可买标记已核验" in rendered
    assert "账户权限另核" in rendered
    assert "深港通官方名单" in rendered and "未完成核验" in rendered
    assert "港股通需单独开通" in rendered


def test_verified_sse_negative_does_not_exclude_shenzhen():
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 20), date(2026, 9, 18), {"02800"})
    assert hk.sse_status(screen, PRODUCT, NOW) == "confirmed_ineligible"
    rendered = text(screen)
    assert "深港通名单未完成核验，资格未知" in rendered
    assert "未列入上述两条买入名单" not in rendered
    assert PRODUCT.code not in rendered and PRODUCT.name not in rendered
    assert "HKD" not in rendered and "每手100份" not in rendered
    assert "香港证券账户" not in rendered
    assert "¥" not in rendered and "$" not in rendered
    assert "建仓" not in rendered and "申购" not in rendered and "美国证券账户" not in rendered
    assert hk.CATALOGUE_URL in rendered
    assert "2026-09-18" in rendered


@pytest.mark.parametrize("catalogue,sse", [
    (date(2026, 9, 12), date(2026, 9, 18)),
    (date(2026, 9, 20), date(2026, 9, 21)),
    (date(2026, 9, 20), date(2026, 9, 12)),
    (None, date(2026, 9, 18)),
])
def test_card_rechecks_dates(catalogue, sse):
    screen = hk.HKScreen([PRODUCT], catalogue, sse, {"03034"})
    assert hk.sse_status(screen, PRODUCT, NOW) == "unknown"
    assert "可买标记已核验" not in text(screen)


def test_card_wire_limits_and_names_escaped():
    products = [hk.HKProduct(f"{i:05d}", "[链接](" + "长" * 500, "HKD", 100)
                for i in range(1, 80)]
    screen = hk.HKScreen(products, date(2026, 9, 20), date(2026, 9, 18),
                         {product.code for product in products})
    cards = hk.build_cards(screen, NOW)
    assert len(cards) > 1
    assert all(len(json.dumps(card).encode("utf-8")) <= 20 * 1024 for card in cards)
    rendered = json.dumps(cards, ensure_ascii=False)
    assert "展示" not in rendered and "最多" not in rendered
    for product in products[:50]:
        assert rendered.count(f"[{product.code}]") == 1
    for product in products[50:]:
        assert f"[{product.code}]" not in rendered
    assert "\\[" in cards[0]["card"]["elements"][1]["text"]["content"]


def test_hong_kong_day_boundary():
    assert hk._today(datetime(2026, 9, 19, 17, tzinfo=timezone.utc)) == date(2026, 9, 20)


def test_download_is_bounded_and_uses_sse_headers(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield b"123"
            yield b"456"

    def get(url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(hk.requests, "get", get)
    monkeypatch.setattr(hk, "MAX_DOWNLOAD_BYTES", 5)
    with pytest.raises(hk.SourceError):
        hk._download(hk.SSE_URL)
    assert captured["headers"]["Referer"] == hk.SSE_REFERENCE_URL
    assert captured["headers"]["User-Agent"] == "Mozilla/5.0"
    assert captured["stream"] is True
    with pytest.raises(hk.SourceError):
        hk._download(hk.SZ_REPORT_URL)
    assert captured["headers"]["Referer"] == hk.SZ_REFERENCE_URL


def sz_report(rows=None, stamp="2026-09-18", total=None):
    rows = [{"zqdm": "03034", "zqjc": "南方纳指", "zqywjc": "CSOP NASDAQ100"}] \
        if rows is None else rows
    total = len(rows) if total is None else total
    return [{
        "metadata": {
            "catalogid": "SGT_GGTBDQD", "name": "港股通标的证券名单",
            "subname": stamp, "tabkey": "tab1", "pagesize": 20, "pageno": 1,
            "pagecount": (total + 19) // 20, "recordcount": total,
            "cols": {"zqdm": "证券代码", "zqjc": "中文简称", "zqywjc": "英文简称"},
        },
        "data": rows[:20], "error": None,
    }]


def sz_workbook(rows, *, inline=True, headers=None):
    return workbook(rows, headers=headers or ["证券代码", "中文简称", "英文简称"],
                    inline=inline, preamble=False)


def mock_sz(monkeypatch, report=None, data=None):
    report = sz_report() if report is None else report
    data = sz_workbook([["03034", "南方纳指", "CSOP NASDAQ100"]]) if data is None else data
    calls = []

    def download(url, *, params=None):
        calls.append((url, params))
        if url == hk.SZ_METADATA_URL:
            return json.dumps(report).encode()
        assert url == hk.SZ_REPORT_URL
        return data

    monkeypatch.setattr(hk, "_download", download)
    return calls


@pytest.mark.parametrize("inline", [True, False])
def test_szse_complete_workbook_matches_official_pagination(monkeypatch, inline):
    rows = [{"zqdm": f"{i:05d}", "zqjc": f"证券{i}", "zqywjc": f"TEST {i}"}
            for i in range(1, 692)]
    calls = mock_sz(monkeypatch, sz_report(rows),
                    sz_workbook([[r["zqdm"], r["zqjc"], r["zqywjc"]] for r in rows],
                                inline=inline))
    eligible, as_of = hk._fetch_szse(NOW)
    assert len(eligible) == 691
    assert as_of == date(2026, 9, 18)
    assert len(calls) == 2
    assert calls[0][1]["tab1PAGENO"] == 1
    assert calls[1][1] == {"SHOWTYPE": "xlsx", "CATALOGID": "SGT_GGTBDQD", "TABKEY": "tab1"}


def test_szse_allows_official_missing_chinese_name(monkeypatch):
    rows = [{"zqdm": "00068", "zqjc": "", "zqywjc": "MANYCORE TECH"}]
    mock_sz(monkeypatch, sz_report(rows), sz_workbook([["00068", "", "MANYCORE TECH"]]))
    assert hk._fetch_szse(NOW) == ({"00068"}, date(2026, 9, 18))


def test_szse_inline_workbook_without_shared_strings(monkeypatch):
    original = sz_workbook([["03034", "南方纳指", "CSOP NASDAQ100"]])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            if entry.filename != "xl/sharedStrings.xml":
                target.writestr(entry.filename, source.read(entry.filename))
    mock_sz(monkeypatch, data=output.getvalue())
    assert hk._fetch_szse(NOW)[0] == {"03034"}


@pytest.mark.parametrize("field,value", [
    ("catalogid", "OTHER"), ("name", "港股通调出证券名单"), ("tabkey", "tab2"),
    ("subname", "2026-09-21"), ("subname", "2026-09-12"),
    ("subname", "2026-02-30"), ("subname", ""),
    ("pagesize", 1000), ("pageno", 2), ("pagecount", 2),
    ("recordcount", 0), ("recordcount", True), ("recordcount", "1.0"),
    ("cols", {"zqdm": "证券代码"}),
])
def test_szse_invalid_metadata(monkeypatch, field, value):
    report = sz_report()
    report[0]["metadata"][field] = value
    mock_sz(monkeypatch, report)
    with pytest.raises(hk.SourceError):
        hk._fetch_szse(NOW)


@pytest.mark.parametrize("rows", [
    [["03034", "南方纳指", "CSOP NASDAQ100"], ["03034", "南方纳指", "CSOP NASDAQ100"]],
    [["3034", "南方纳指", "CSOP NASDAQ100"]],
    [["03195", "南方纳指", "CSOP NASDAQ100"]],
    [["03034", "不同名称", "CSOP NASDAQ100"]],
    [["03034", "南方纳指", "MISMATCH"]],
    [],
])
def test_szse_workbook_code_count_and_sample_mismatch(monkeypatch, rows):
    mock_sz(monkeypatch, data=sz_workbook(rows))
    with pytest.raises(hk.SourceError):
        hk._fetch_szse(NOW)


def test_szse_truncated_full_workbook_not_accepted(monkeypatch):
    rows = [{"zqdm": f"{i:05d}", "zqjc": "证券", "zqywjc": "TEST"}
            for i in range(1, 22)]
    mock_sz(monkeypatch, sz_report(rows),
            sz_workbook([[r["zqdm"], r["zqjc"], r["zqywjc"]] for r in rows[:20]]))
    with pytest.raises(hk.SourceError):
        hk._fetch_szse(NOW)


def test_szse_wrong_header_and_error_response(monkeypatch):
    mock_sz(monkeypatch, data=sz_workbook([["03034", "南方纳指", "CSOP NASDAQ100"]],
                                         headers=["代码", "中文简称", "英文简称"]))
    with pytest.raises(hk.SourceError):
        hk._fetch_szse(NOW)
    report = sz_report()
    report[0]["error"] = "failed"
    mock_sz(monkeypatch, report)
    with pytest.raises(hk.SourceError):
        hk._fetch_szse(NOW)


def test_fetch_uses_four_sources_and_preserves_existing_dataclass_arguments(monkeypatch):
    calls = []

    def download(url, *, params=None):
        calls.append(url)
        return {
            hk.CATALOGUE_URL: workbook(),
            hk.SSE_URL: json.dumps(sse_page([sse_row("02800")])).encode(),
            hk.SZ_METADATA_URL: json.dumps(sz_report()).encode(),
            hk.SZ_REPORT_URL: sz_workbook([["03034", "南方纳指", "CSOP NASDAQ100"]]),
        }[url]

    monkeypatch.setattr(hk, "_download", download)
    screen = hk.fetch_screen(NOW)
    assert len(calls) == 4
    assert screen.szse_date == date(2026, 9, 18)
    assert screen.szse_eligible == {"03034"}
    assert screen.issues == ()
    assert hk.HKScreen([], None, None, None, ("old positional",)).issues == ("old positional",)


def test_both_routes_verified_negative_is_scoped_to_catalogue_products():
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 20), date(2026, 9, 18), {"02800"},
                         szse_date=date(2026, 9, 18), szse_eligible={"02800"})
    rendered = text(screen)
    assert "暂未确认可通过港股通买入" in rendered
    assert "本次核对的1只香港美股ETF，未列入上述两条买入名单" in rendered
    assert PRODUCT.code not in rendered and PRODUCT.name not in rendered
    assert len(hk.build_cards(screen, NOW)[0]["card"]["elements"]) == 1
    assert "深港通官方名单" in rendered and "2026-09-18" in rendered
    assert "未完成核验" not in rendered
    assert "所有美股ETF" not in rendered and "永久不可买" not in rendered


def test_szse_only_positive_requires_current_catalogue():
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 20), None, None,
                         szse_date=date(2026, 9, 18), szse_eligible={"03034"})
    assert hk.szse_status(screen, PRODUCT, NOW) == "confirmed_eligible"
    assert "深港通：标的名单已核验；账户权限另核" in text(screen)
    assert "暂未确认可通过港股通买入" not in text(screen)
    assert PRODUCT.code in text(screen) and PRODUCT.name in text(screen)
    screen.catalogue_date = date(2026, 9, 21)
    assert hk.szse_status(screen, PRODUCT, NOW) == "unknown"
    assert "暂未确认可通过港股通买入" in text(screen)
    assert PRODUCT.code not in text(screen) and PRODUCT.name not in text(screen)


def test_default_card_only_lists_confirmed_southbound_products():
    sse_product = PRODUCT
    sz_product = hk.HKProduct("03195", "HS S&P500", "HKD", 100)
    unconfirmed = hk.HKProduct("02834", "ISHARESND100", "HKD", 10)
    screen = hk.HKScreen([sse_product, sz_product, unconfirmed],
                         date(2026, 9, 20), date(2026, 9, 18), {sse_product.code},
                         szse_date=date(2026, 9, 18), szse_eligible={sz_product.code})
    rendered = text(screen)
    assert sse_product.code in rendered and sz_product.code in rendered
    assert unconfirmed.code not in rendered and unconfirmed.name not in rendered
    assert "香港证券账户" not in rendered
    assert len(hk.build_cards(screen, NOW)[0]["card"]["elements"]) == 3


def test_unknown_routes_never_show_listing_reference_rows():
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 20), None, None)
    rendered = text(screen)
    assert PRODUCT.code not in rendered and PRODUCT.name not in rendered
    assert "暂未确认可通过港股通买入" in rendered
    assert hk.SSE_REFERENCE_URL in rendered and hk.SZ_REFERENCE_URL in rendered
    assert len(hk.build_cards(screen, NOW)[0]["card"]["elements"]) == 1


@pytest.mark.parametrize("stamp", [date(2026, 9, 21), date(2026, 9, 12), None])
def test_szse_card_rechecks_date_and_keeps_unknown(stamp):
    screen = hk.HKScreen([PRODUCT], date(2026, 9, 20), date(2026, 9, 18), set(),
                         szse_date=stamp, szse_eligible={"03034"})
    assert hk.szse_status(screen, PRODUCT, NOW) == "unknown"
    assert "未完成核验" in text(screen)
    assert "深港通名单未完成核验，资格未知" in text(screen)
    assert "未列入上述两条买入名单" not in text(screen)
