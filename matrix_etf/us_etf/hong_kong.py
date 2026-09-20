"""Official Hong Kong listing metadata and conservative Southbound access checks."""

import io
import json
import logging
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from matrix_etf.us_etf.card import _div, _escape

CATALOGUE_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)
SSE_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_REFERENCE_URL = "https://www.sse.com.cn/services/hkexsc/disclo/eligible/"
SZ_REFERENCE_URL = "https://www.szse.cn/szhk/hkbussiness/underlylist/"
SZ_REPORT_URL = "https://www.szse.cn/api/report/ShowReport"
SZ_METADATA_URL = SZ_REPORT_URL + "/data"
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_CARD_BYTES = 20 * 1024
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_HEADERS = (
    "Stock Code", "Name of Securities", "Category", "Sub-Category",
    "Board Lot", "Trading Currency",
)
_US_INDEX = re.compile(r"(?:NASDAQ100|ND100|NDQ100|S&P500|MSCIUSA)(?![A-Z0-9])")
logger = logging.getLogger(__name__)


@dataclass
class HKProduct:
    code: str
    name: str
    currency: str
    board_lot: int


@dataclass
class HKScreen:
    products: list[HKProduct]
    catalogue_date: date | None
    sse_date: date | None
    sse_eligible: set[str] | None
    issues: tuple[str, ...] = ()
    szse_date: date | None = None
    szse_eligible: set[str] | None = None


class SourceError(ValueError):
    """Expected invalid, incomplete, or stale official source data."""


def _today(now: datetime) -> date:
    return now.astimezone(ZoneInfo("Asia/Hong_Kong")).date() if now.tzinfo else now.date()


def _valid_date(value: date, today: date, *, upcoming: bool = False) -> bool:
    return -7 <= (value - today).days <= (3 if upcoming else 0)


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise SourceError("invalid integer")
    number = int(str(value))
    if number <= 0:
        raise SourceError("nonpositive integer")
    return number


def _code(value: object, *, padded: bool = False) -> str:
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,5}" if padded else r"[0-9]{5}", text):
        raise SourceError("invalid security code")
    if int(text) == 0:
        raise SourceError("zero security code")
    return text.zfill(5)


def _download(url: str, *, params: dict | None = None) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0"}
    if url == SSE_URL:
        headers["Referer"] = SSE_REFERENCE_URL
    elif url in (SZ_REPORT_URL, SZ_METADATA_URL):
        headers["Referer"] = SZ_REFERENCE_URL
    with requests.get(url, params=params, headers=headers, stream=True,
                      timeout=(5, 20)) as response:
        response.raise_for_status()
        output = bytearray()
        for chunk in response.iter_content(64 * 1024):
            if len(output) + len(chunk) > MAX_DOWNLOAD_BYTES:
                raise SourceError("download size limit")
            output.extend(chunk)
        return bytes(output)


def _xml_elements(archive: zipfile.ZipFile, path: str, tag: str):
    # Check before handing bytes to Expat; NUL rejection also excludes UTF-16 DTDs.
    with archive.open(path) as stream:
        tail = b""
        while chunk := stream.read(64 * 1024):
            data = tail + chunk
            if b"\x00" in data or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                raise SourceError("unsafe XML")
            tail = data[-16:]
    with archive.open(path) as stream:
        parents = []
        for event, element in ElementTree.iterparse(stream, events=("start", "end")):
            if event == "start":
                parents.append(element)
            else:
                if element.tag == _NS + tag:
                    yield element
                    if len(parents) > 1:
                        parents[-2].remove(element)
                    element.clear()
                parents.pop()


def _cell_text(cell, strings: list[str]) -> str:
    kind = cell.get("t")
    if cell.find(_NS + "f") is not None:
        raise SourceError("formula in directory")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_NS + "t")).strip()
    value = cell.findtext(_NS + "v", "")
    if kind == "s":
        if not re.fullmatch(r"[0-9]+", value) or int(value) >= len(strings):
            raise SourceError("invalid shared string")
        value = strings[int(value)]
    return value.strip()


def _validate_archive(archive: zipfile.ZipFile) -> None:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(entries) > 256 or len(set(names)) != len(names):
        raise SourceError("invalid archive entries")
    if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
        raise SourceError("archive size limit")
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                or ":" in entry.filename or entry.flag_bits & 1
                or stat.S_ISLNK(entry.external_attr >> 16)):
            raise SourceError("unsafe archive entry")
    if "xl/worksheets/sheet1.xml" not in names:
        raise SourceError("missing worksheet")


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    strings = []
    if "xl/sharedStrings.xml" in archive.namelist():
        for element in _xml_elements(archive, "xl/sharedStrings.xml", "si"):
            text = "".join(node.text or "" for node in element.iter(_NS + "t"))
            if len(text) > 10000 or len(strings) >= 100000:
                raise SourceError("shared string limit")
            strings.append(text)
    return strings


def _row_cells(element, strings: list[str]) -> dict[str, str]:
    cells = {}
    for cell in element.findall(_NS + "c"):
        match = re.fullmatch(r"([A-Z]+)[1-9][0-9]*", cell.get("r", ""))
        if not match or match[1] in cells:
            raise SourceError("invalid worksheet cell")
        cells[match[1]] = _cell_text(cell, strings)
    return cells


def parse_catalogue(data: bytes, now: datetime) -> tuple[list[HKProduct], date]:
    """Read the official workbook without pandas, extraction, or a full XML tree."""
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise SourceError("download size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _validate_archive(archive)
            sheet = "xl/worksheets/sheet1.xml"
            strings = _shared_strings(archive)
            products = []
            seen = set()
            headers = None
            catalogue_date = None
            title_seen = False
            data_count = 0
            for element in _xml_elements(archive, sheet, "row"):
                cells = _row_cells(element, strings)
                values = list(cells.values())
                if headers is None:
                    title_seen |= "List of Securities" in values
                    for value in values:
                        if value.startswith("Updated as at"):
                            match = re.fullmatch(r"Updated as at\s+(\d{2}/\d{2}/\d{4})", value)
                            if not match or catalogue_date is not None:
                                raise SourceError("invalid catalogue date")
                            catalogue_date = datetime.strptime(match[1], "%d/%m/%Y").date()
                    if "Stock Code" in values:
                        if any(values.count(name) != 1 for name in _HEADERS):
                            raise SourceError("missing or duplicate headers")
                        headers = {name: column for column, name in cells.items()}
                    continue
                if not any(values):
                    continue
                record = {name: cells.get(headers[name], "") for name in _HEADERS}
                code = _code(record["Stock Code"], padded=True)
                if code in seen:
                    raise SourceError("duplicate security code")
                seen.add(code)
                data_count += 1
                if (record["Category"] != "Exchange Traded Products"
                        or record["Sub-Category"] != "Exchange Traded Funds"
                        or record["Trading Currency"] != "HKD"):
                    continue
                name = record["Name of Securities"]
                compact = re.sub(r"\s+", "", name.upper())
                if (not _US_INDEX.search(compact)
                        or re.match(r"^(?:A|CC)(?:\s|[-/])", name.upper())
                        or any(word in compact for word in (
                            "COVERED", "CALL", "ACTIVE", "BOND", "TREASURY",
                            "GOLD", "SILVER", "INVERSE", "LEVER", "DAILY", "2X", "3X",
                        ))):
                    continue
                if not name or len(name) > 200:
                    raise SourceError("invalid product name")
                lot = _positive_int(record["Board Lot"])
                products.append(HKProduct(code, name, "HKD", lot))
            if (not title_seen or headers is None or not data_count or catalogue_date is None
                    or not _valid_date(catalogue_date, _today(now), upcoming=True)):
                raise SourceError("missing or stale catalogue metadata")
            return sorted(products, key=lambda product: product.code), catalogue_date
    except (zipfile.BadZipFile, ElementTree.ParseError, UnicodeError, ValueError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("invalid workbook") from exc


def _fetch_sse(now: datetime) -> tuple[set[str], date]:
    eligible = set()
    seen = set()
    dates = set()
    expected_total = expected_pages = None
    etf_count = 0
    page = 1
    while True:
        params = {
            "sqlId": "COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L",
            "isPagination": "true", "pageHelp.pageSize": 1000,
            "pageHelp.pageNo": page, "pageHelp.beginPage": page,
            "pageHelp.cacheSize": 1, "pageHelp.endPage": page, "keyword": "",
        }
        try:
            payload = json.loads(_download(SSE_URL, params=params))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("invalid SSE JSON") from exc
        if not isinstance(payload, dict) or payload.get("SUCCESS") in (False, "false"):
            raise SourceError("invalid SSE response")
        metadata = payload.get("pageHelp")
        if not isinstance(metadata, dict):
            raise SourceError("missing SSE pagination")
        total = _positive_int(metadata.get("total"))
        pages = _positive_int(metadata.get("pageCount"))
        page_size = _positive_int(metadata.get("pageSize"))
        page_no = _positive_int(metadata.get("pageNo"))
        if (page_size != 1000 or total > 20000 or pages != (total + 999) // 1000
                or page_no != page):
            raise SourceError("invalid SSE pagination")
        if expected_total is None:
            expected_total, expected_pages = total, pages
        if (total, pages) != (expected_total, expected_pages):
            raise SourceError("changing SSE pagination")
        rows = metadata.get("data")
        if rows is None:
            rows = payload.get("result")
        if not isinstance(rows, list) or len(rows) != min(1000, total - (page - 1) * 1000):
            raise SourceError("incomplete SSE page")
        for row in rows:
            if not isinstance(row, dict):
                raise SourceError("invalid SSE row")
            code = _code(row.get("SECURITY_CODE"))
            if code in seen:
                raise SourceError("duplicate SSE code")
            seen.add(code)
            stamp = row.get("UPDATE_DATE")
            if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
                raise SourceError("invalid SSE date")
            try:
                as_of = date.fromisoformat(stamp)
            except ValueError as exc:
                raise SourceError("invalid SSE date") from exc
            if not _valid_date(as_of, _today(now)):
                raise SourceError("future or stale SSE list")
            dates.add(as_of)
            kind = row.get("SECURITY_TYPE")
            if kind not in ("股票", "ETF"):
                raise SourceError("unknown SSE security type")
            if kind == "ETF":
                etf_count += 1
                # Only the buy-allowed value observed on the official ETF list is trusted.
                if str(row.get("TRADE_FLAG")) != "1":
                    raise SourceError("unverified SSE ETF trade flag")
                eligible.add(code)
        if page == pages:
            break
        page += 1
    if len(seen) != expected_total or len(dates) != 1 or not etf_count:
        raise SourceError("inconsistent SSE snapshot")
    return eligible, dates.pop()


def _fetch_szse(now: datetime) -> tuple[set[str], date]:
    params = {"SHOWTYPE": "JSON", "CATALOGID": "SGT_GGTBDQD",
              "TABKEY": "tab1", "tab1PAGENO": 1}
    try:
        reports = json.loads(_download(SZ_METADATA_URL, params=params))
    except (ValueError, UnicodeError) as exc:
        raise SourceError("invalid SZSE JSON") from exc
    if not isinstance(reports, list) or len(reports) != 1:
        raise SourceError("invalid SZSE reports")
    report = reports[0]
    if not isinstance(report, dict) or report.get("error") is not None:
        raise SourceError("SZSE report error")
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        raise SourceError("missing SZSE metadata")
    columns = {"zqdm": "证券代码", "zqjc": "中文简称", "zqywjc": "英文简称"}
    if (metadata.get("catalogid") != "SGT_GGTBDQD"
            or metadata.get("tabkey") != "tab1"
            or metadata.get("name") != "港股通标的证券名单"
            or metadata.get("cols") != columns):
        raise SourceError("unexpected SZSE report schema")
    stamp = metadata.get("subname")
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
        raise SourceError("invalid SZSE date")
    try:
        as_of = date.fromisoformat(stamp)
    except ValueError as exc:
        raise SourceError("invalid SZSE date") from exc
    if not _valid_date(as_of, _today(now)):
        raise SourceError("future or stale SZSE list")
    total = _positive_int(metadata.get("recordcount"))
    page_size = _positive_int(metadata.get("pagesize"))
    pages = _positive_int(metadata.get("pagecount"))
    if (total > 20000 or page_size != 20
            or pages != (total + page_size - 1) // page_size
            or _positive_int(metadata.get("pageno")) != 1):
        raise SourceError("invalid SZSE pagination")
    sample = report.get("data")
    if not isinstance(sample, list) or len(sample) != min(total, page_size):
        raise SourceError("incomplete SZSE first page")
    expected = []
    for row in sample:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) for key in columns
        ) or not row["zqywjc"].strip():
            raise SourceError("invalid SZSE sample")
        expected.append((_code(row["zqdm"]), row["zqjc"].strip(), row["zqywjc"].strip()))
    if len({row[0] for row in expected}) != len(expected):
        raise SourceError("duplicate SZSE sample code")
    params = {"SHOWTYPE": "xlsx", "CATALOGID": "SGT_GGTBDQD", "TABKEY": "tab1"}
    data = _download(SZ_REPORT_URL, params=params)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise SourceError("download size limit")
    seen = set()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _validate_archive(archive)
            strings = _shared_strings(archive)
            rows = _xml_elements(archive, "xl/worksheets/sheet1.xml", "row")
            header = next(rows, None)
            if header is None or _row_cells(header, strings) != {
                "A": "证券代码", "B": "中文简称", "C": "英文简称",
            }:
                raise SourceError("invalid SZSE workbook headers")
            for index, element in enumerate(rows):
                cells = _row_cells(element, strings)
                if set(cells) != {"A", "B", "C"} or not cells["C"]:
                    raise SourceError("invalid SZSE workbook row")
                code = _code(cells["A"])
                if code in seen:
                    raise SourceError("duplicate SZSE workbook code")
                seen.add(code)
                if index < len(expected) and (code, cells["B"], cells["C"]) != expected[index]:
                    raise SourceError("SZSE workbook and JSON mismatch")
            if len(seen) != total:
                raise SourceError("incomplete SZSE workbook")
    except (zipfile.BadZipFile, ElementTree.ParseError, UnicodeError) as exc:
        raise SourceError("invalid SZSE workbook") from exc
    return seen, as_of


def fetch_screen(now: datetime) -> HKScreen:
    screen = HKScreen([], None, None, None)
    issues = []
    try:
        screen.products, screen.catalogue_date = parse_catalogue(_download(CATALOGUE_URL), now)
        if not screen.products:
            issues.append("港交所目录未筛得符合条件的美股指数ETF；不代表产品不存在。")
    except (requests.RequestException, SourceError) as exc:
        logger.warning("HKEX catalogue unavailable (%s)", type(exc).__name__)
        issues.append("港交所上市目录核验失败或日期超出有效窗口，上市资料未知。")
    try:
        screen.sse_eligible, screen.sse_date = _fetch_sse(now)
    except (requests.RequestException, SourceError) as exc:
        logger.warning("SSE Southbound list unavailable (%s)", type(exc).__name__)
        issues.append("沪港通可买名单核验失败或不完整，沪港通资格未知。")
    try:
        screen.szse_eligible, screen.szse_date = _fetch_szse(now)
    except (requests.RequestException, SourceError) as exc:
        logger.warning("SZSE Southbound list unavailable (%s)", type(exc).__name__)
        issues.append("深港通标的名单核验失败或不完整，深港通资格未知。")
    screen.issues = tuple(issues)
    return screen


def sse_status(screen: HKScreen, product: HKProduct, now: datetime) -> str:
    """This status is exclusively SSE; a negative result says nothing about SZSE."""
    today = _today(now)
    if (screen.sse_eligible is None or screen.sse_date is None
            or not _valid_date(screen.sse_date, today)
            or screen.catalogue_date is None
            or not _valid_date(screen.catalogue_date, today)):
        return "unknown"
    return "confirmed_eligible" if product.code in screen.sse_eligible else "confirmed_ineligible"


def szse_status(screen: HKScreen, product: HKProduct, now: datetime) -> str:
    today = _today(now)
    if (screen.szse_eligible is None or screen.szse_date is None
            or not _valid_date(screen.szse_date, today)
            or screen.catalogue_date is None
            or not _valid_date(screen.catalogue_date, today)):
        return "unknown"
    return "confirmed_eligible" if product.code in screen.szse_eligible else "confirmed_ineligible"


def build_cards(screen: HKScreen, now: datetime) -> list[dict]:
    """Show current Southbound candidates only, never HK-account-only reference rows."""
    today = _today(now)
    verified_sse = (
        screen.sse_eligible is not None and screen.sse_date is not None
        and _valid_date(screen.sse_date, today)
    )
    verified_szse = (
        screen.szse_eligible is not None and screen.szse_date is not None
        and _valid_date(screen.szse_date, today)
    )
    positive = [p for p in screen.products if "confirmed_eligible" in (
        sse_status(screen, p, now), szse_status(screen, p, now),
    )]
    summary = [
        ("已核验港股通标的名单中的美股ETF（仍需账户权限核验）" if positive else
         "暂未确认可通过港股通买入的美股ETF"),
        "港股通需单独开通；香港上市不等于内地证券账户可买。",
    ]
    if verified_sse:
        summary.append(f"沪港通名单：{screen.sse_date.isoformat()}")
    else:
        summary.append("沪港通名单未完成有效核验，资格未知。")
    if verified_szse:
        summary.append(f"深港通名单：{screen.szse_date.isoformat()}")
    else:
        summary.append("深港通名单未完成核验，资格未知。")
    if (verified_sse and verified_szse and screen.products
            and not any(p.code in screen.sse_eligible | screen.szse_eligible
                        for p in screen.products)):
        summary.append(f"本次核对的{len(screen.products)}只香港美股ETF，未列入上述两条买入名单。")
    if screen.catalogue_date:
        label = screen.catalogue_date.isoformat()
        if today < screen.catalogue_date:
            label += "（未来日期，仅作目录参考）"
        elif not _valid_date(screen.catalogue_date, today):
            label += "（资料过期，不能确认当前状态）"
        summary.append("港交所目录日期：" + label)
    else:
        summary.append("港交所目录日期未知。")
    if screen.issues:
        summary.append("部分来源核验未完成，未确认的产品不列入可买清单。")
    if positive:
        summary.append("以下仅列已核验港股通名单内产品，实际可买仍须券商核对账户权限。")
        summary.append("未取得已核验港股ETF行情，不提供价格、买入金额、停牌或溢价判断。")
    summary.append(f"[沪港通官方名单]({SSE_REFERENCE_URL}) · "
                   f"[深港通官方名单]({SZ_REFERENCE_URL}) · [港交所目录]({CATALOGUE_URL})")
    if len(positive) > 50:
        summary.append("以下为部分已核验标的。")
    rows = []
    for product in positive[:50]:
        status = sse_status(screen, product, now)
        label = {
            "confirmed_eligible": "沪港通名单：可买标记已核验；账户权限另核",
            "confirmed_ineligible": "沪港通：不在已核验可买名单",
            "unknown": "沪港通：当前资格未知",
        }[status]
        sz_label = {
            "confirmed_eligible": "深港通：标的名单已核验；账户权限另核",
            "confirmed_ineligible": "深港通：不在已核验标的名单",
            "unknown": "深港通：当前资格未知",
        }[szse_status(screen, product, now)]
        row = _div(
            f"[{_escape(product.code)}]({CATALOGUE_URL}) · {_escape(product.name)}"
            f"\n{_escape(product.currency)} · 每手{product.board_lot}份（目录资料）"
            f"\n{label}\n{sz_label}"
        )
        rows.append(row)

    def page(items):
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "港股市场 · 账户渠道核验"},
                           "template": "blue"},
                "elements": [_div("\n".join(summary))] + items,
            },
        }

    cards = []
    current = page([])
    if len(json.dumps(current).encode("utf-8")) > MAX_CARD_BYTES:
        raise ValueError("HK access summary exceeds 20 KiB")
    for row in rows:
        candidate = page(current["card"]["elements"][1:] + [row])
        if len(json.dumps(candidate).encode("utf-8")) <= MAX_CARD_BYTES:
            current = candidate
            continue
        if len(current["card"]["elements"]) == 1:
            raise ValueError("One HK product exceeds 20 KiB")
        cards.append(current)
        current = page([row])
        if len(json.dumps(current).encode("utf-8")) > MAX_CARD_BYTES:
            raise ValueError("One HK product exceeds 20 KiB")
    cards.append(current)
    return cards
