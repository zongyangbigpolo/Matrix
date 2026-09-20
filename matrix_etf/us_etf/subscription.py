"""Current official primary-market ETF creation quotas, in fund shares (not CNY)."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree

import requests

from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)

MAX_SYMBOLS = 50
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WORKERS = 4
REQUEST_TIMEOUT = (3, 5)
REQUEST_DEADLINE = 20.0
SZ_NAMESPACE = "http://ts.szse.cn/Fund"
_SYMBOL = re.compile(r"[0-9]{6}\.(SH|SZ)")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True)
class CreationQuota:
    effective_date: date | None = None
    status: str = "unknown"
    creation_unit: Decimal | None = None
    fund_cumulative: Decimal | None = None
    fund_net: Decimal | None = None
    account_cumulative: Decimal | None = None
    account_net: Decimal | None = None
    source_url: str = ""
    issue: str | None = None


class _QuotaError(ValueError):
    pass


def _valid_symbol(symbol: str) -> bool:
    return isinstance(symbol, str) and _SYMBOL.fullmatch(symbol) is not None


def _unknown(symbol, source_url, issue, effective_date=None):
    # Never log provider payloads, exception messages, or unchecked caller input.
    safe_symbol = symbol if _valid_symbol(symbol) else "<invalid symbol>"
    logger.warning("ETF PCF unavailable for %s: %s", safe_symbol, issue)
    return CreationQuota(effective_date=effective_date, source_url=source_url, issue=issue)


def _source_url(symbol: str, expected: date) -> str:
    if not _valid_symbol(symbol):
        raise _QuotaError("invalid-symbol")
    code = symbol[:6]
    if symbol.endswith(".SH"):
        return f"https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={code}"
    return (
        "https://reportdocs.static.szse.cn/files/text/ETFDown/"
        f"pcf_{code}_{expected:%Y%m%d}.xml"
    )


def _quantity(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    # PCF decimal quantities are not monetary values, percentages, or exponents.
    if len(value) > 128 or _NUMBER.fullmatch(value) is None:
        raise _QuotaError("invalid-quantity")
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise _QuotaError("invalid-quantity")
    return result


def parse_quota(
    content: bytes, symbol: str, expected: date, source_url: str = ""
) -> CreationQuota:
    """Validate one exchange PCF; failures contain no usable status or quantities."""
    effective_date = None
    try:
        if not _valid_symbol(symbol):
            raise _QuotaError("invalid-symbol")
        if len(content) > MAX_RESPONSE_BYTES:
            raise _QuotaError("response-too-large")
        if b"\x00" in content:
            raise _QuotaError("unsupported-xml-encoding")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise _QuotaError("unsupported-xml-encoding") from None
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            raise _QuotaError("xml-directives-forbidden")
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            raise _QuotaError("invalid-xml") from None

        shanghai = symbol.endswith(".SH")
        prefix = "" if shanghai else f"{{{SZ_NAMESPACE}}}"
        root_name = "SSEPortfolioCompositionFile" if shanghai else prefix + "PCFFile"
        if root.tag != root_name:
            raise _QuotaError("invalid-root")
        fields = {}
        for child in root:
            if child.tag in fields:
                raise _QuotaError("duplicate-header")
            fields[child.tag] = child

        def header(name):
            # Only direct, correctly namespaced headers; never constituent fields.
            if any(tag.rsplit("}", 1)[-1] == name and tag != prefix + name for tag in fields):
                raise _QuotaError("invalid-header-namespace")
            element = fields.get(prefix + name)
            if element is None:
                return None
            if len(element):
                raise _QuotaError("invalid-header")
            return (element.text or "").strip()

        day = header("TradingDay")
        if not day or re.fullmatch(r"[0-9]{8}", day) is None:
            raise _QuotaError("invalid-effective-date")
        try:
            effective_date = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        except ValueError:
            raise _QuotaError("invalid-effective-date") from None
        if header("FundInstrumentID" if shanghai else "SecurityID") != symbol[:6]:
            raise _QuotaError("fund-code-mismatch")
        if effective_date != expected:
            raise _QuotaError("effective-date-mismatch")

        switch = header("CreationRedemptionSwitch" if shanghai else "Creation")
        # SSE value 1 is corroborated by official fund-manager PCFs. Do not
        # extrapolate other switch values from unofficial enumeration tables.
        status = {"1": "open"}.get(switch) if shanghai else {
            "Y": "open", "N": "suspended",
        }.get(switch)
        if status is None:
            raise _QuotaError("unverified-creation-status")
        account_suffix = "Acct" if shanghai else "User"
        return CreationQuota(
            effective_date=effective_date,
            status=status,
            creation_unit=_quantity(header("CreationRedemptionUnit")),
            fund_cumulative=_quantity(header("CreationLimit")),
            fund_net=_quantity(header("NetCreationLimit")),
            account_cumulative=_quantity(header("CreationLimitPer" + account_suffix)),
            account_net=_quantity(header("NetCreationLimitPer" + account_suffix)),
            source_url=source_url,
        )
    except _QuotaError as exc:
        return _unknown(symbol, source_url, str(exc), effective_date)


def _download(url: str) -> bytes:
    deadline = time.monotonic() + REQUEST_DEADLINE
    with requests.Session() as session:
        # Public endpoints only: no .netrc credentials or environment proxies.
        session.trust_env = False
        with session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
            headers={"Accept": "application/xml", "Referer": "https://www.sse.com.cn/"},
        ) as response:
            if response.status_code != 200:
                raise _QuotaError("http-status-not-200")
            length = response.headers.get("Content-Length")
            if length is not None:
                if re.fullmatch(r"[0-9]{1,12}", length) is None:
                    raise _QuotaError("invalid-content-length")
                if int(length) > MAX_RESPONSE_BYTES:
                    raise _QuotaError("response-too-large")
            content = bytearray()
            # Check each delivered byte so a slow body cannot keep resetting
            # the read timeout while filling one large iter_content chunk.
            for chunk in response.iter_content(chunk_size=1):
                if time.monotonic() >= deadline:
                    raise _QuotaError("request-deadline-exceeded")
                if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise _QuotaError("response-too-large")
                content.extend(chunk)
            if time.monotonic() >= deadline:
                raise _QuotaError("request-deadline-exceeded")
            return bytes(content)


def _fetch_one(symbol: str, expected: date) -> CreationQuota:
    url = ""
    try:
        url = _source_url(symbol, expected)
        content = _download(url)
    except _QuotaError as exc:
        return _unknown(symbol, url, str(exc))
    except requests.RequestException:
        return _unknown(symbol, url, "request-failed")
    return parse_quota(content, symbol, expected, url)


def fetch_quotas(symbols: list[str], expected: date) -> dict[str, CreationQuota]:
    """Fetch at most 50 selected symbols without retries, redirects, or a cache."""
    if len(symbols) > MAX_SYMBOLS:
        return {symbol: _unknown(symbol, "", "too-many-symbols") for symbol in symbols}
    if not symbols:
        return {}
    unique = list(dict.fromkeys(symbols))
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(unique))) as pool:
        results = pool.map(lambda symbol: _fetch_one(symbol, expected), unique)
        return dict(zip(unique, results))
