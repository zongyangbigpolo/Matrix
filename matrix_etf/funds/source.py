"""Fresh subscription quotes from Eastmoney's free, public fund listing."""

import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import requests

SOURCE_URL = "https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx"
CATALOG_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_RETRY_DELAY = 30.0
_PARAMS = {"t": "8", "page": "1,50000", "js": "reData", "sort": "fcode,asc"}
_CODE = re.compile(r"[0-9]{6}\Z")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_MISSING = {"", "-", "--", "---", "—", "N/A", "null", "None"}


class FundSourceError(RuntimeError):
    """The public source could not supply a complete, trustworthy batch."""


@dataclass(frozen=True)
class FundQuote:
    code: str
    name: str
    fund_type: str
    status: str
    minimum: Decimal | None
    daily_limit: Decimal | None
    fee_percent: Decimal | None
    issue: str | None = None


def _reject_constant(value: str):
    raise FundSourceError(f"Invalid JSON constant: {value}")


class _Reader:
    """Read the narrow JavaScript envelope; only JSON values are permitted."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.decoder = json.JSONDecoder(parse_constant=_reject_constant)

    def whitespace(self):
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def take(self, token: str) -> bool:
        self.whitespace()
        if self.text.startswith(token, self.pos):
            self.pos += len(token)
            return True
        return False

    def expect(self, token: str):
        if not self.take(token):
            raise FundSourceError("Malformed fund response envelope")

    def value(self):
        self.whitespace()
        value, self.pos = self.decoder.raw_decode(self.text, self.pos)
        return value

    def key(self) -> str:
        self.whitespace()
        match = re.match(r"[A-Za-z_][A-Za-z_0-9]*", self.text[self.pos:self.pos + 32])
        if match is None:
            raise FundSourceError("Malformed fund response key")
        self.pos += len(match[0])
        return match[0]


def _validate_codes(codes: set[str]):
    if any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in codes):
        raise ValueError("Fund codes must be six ASCII digits")


def _safe_text(value: str, *, empty: bool = False) -> bool:
    return (
        (empty or bool(value.strip()))
        and len(value) <= 200
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _amount(value: str, field: str, issues: list[str]) -> Decimal | None:
    value = value.strip()
    if value in _MISSING:
        return None
    try:
        if len(value) > 128 or not _NUMBER.fullmatch(value):
            raise InvalidOperation
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise InvalidOperation
        return result
    except InvalidOperation:
        issues.append(f"Invalid {field}")
        return None


def _quote(row: list[str]) -> FundQuote:
    issues: list[str] = []
    minimum = _amount(row[8], "minimum", issues)
    daily_limit = _amount(row[9], "daily_limit", issues)
    fee_raw = row[12].strip()
    if fee_raw.endswith("%"):
        fee_raw = fee_raw[:-1].strip()
    fee_percent = _amount(fee_raw, "fee_percent", issues)
    return FundQuote(
        code=row[0],
        name=row[1],
        fund_type=row[2],
        status=row[5],
        minimum=minimum,
        daily_limit=daily_limit,
        fee_percent=fee_percent,
        issue="; ".join(issues) or None,
    )


def parse_quotes(text: str, codes: set[str]) -> dict[str, FundQuote]:
    """Validate the entire batch, retaining quotes only for requested codes."""
    _validate_codes(codes)
    if not isinstance(text, str) or len(text) > MAX_RESPONSE_BYTES:
        raise FundSourceError("Fund response exceeds size limit or is not text")
    try:
        if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise FundSourceError("Fund response exceeds size limit")
        return _parse_quotes(text, codes)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise FundSourceError("Malformed fund response") from exc


def _parse_quotes(text: str, codes: set[str]) -> dict[str, FundQuote]:
    reader = _Reader(text)
    reader.expect("var reData")
    reader.expect("=")
    reader.expect("{")
    metadata = {}
    seen_keys: set[str] = set()
    seen_codes: set[str] = set()
    quotes = {}
    while True:
        key = reader.key()
        if key not in {"datas", "record", "pages", "curpage", "showday"} or key in seen_keys:
            raise FundSourceError("Unexpected or duplicate fund response key")
        seen_keys.add(key)
        reader.expect(":")
        if key == "datas":
            reader.expect("[")
            if not reader.take("]"):
                while True:
                    row = reader.value()
                    if (
                        not isinstance(row, list)
                        or len(row) != 13
                        or not all(isinstance(value, str) for value in row)
                        or not _CODE.fullmatch(row[0])
                        or not _safe_text(row[1])
                        or not _safe_text(row[2], empty=True)
                        or not _safe_text(row[5], empty=True)
                    ):
                        raise FundSourceError("Invalid fund row schema")
                    if row[0] in seen_codes:
                        raise FundSourceError("Duplicate fund code in response")
                    seen_codes.add(row[0])
                    if row[0] in codes:
                        quotes[row[0]] = _quote(row)
                    if reader.take("]"):
                        break
                    reader.expect(",")
        else:
            metadata[key] = reader.value()
        if reader.take("}"):
            break
        reader.expect(",")
    reader.take(";")
    reader.whitespace()
    if reader.pos != len(text):
        raise FundSourceError("Unexpected trailing fund response content")
    if seen_keys != {"datas", "record", "pages", "curpage", "showday"}:
        raise FundSourceError("Missing fund batch metadata")
    record = metadata["record"]
    if (
        not isinstance(record, str)
        or not re.fullmatch(r"[0-9]{1,8}", record)
        or int(record) != len(seen_codes)
        or not seen_codes
        or metadata["pages"] != "1"
        or metadata["curpage"] != "1"
    ):
        raise FundSourceError("Incomplete or empty fund batch")
    showday = metadata["showday"]
    if (
        not isinstance(showday, list)
        or len(showday) != 2
        or not all(
            isinstance(day, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day)
            for day in showday
        )
    ):
        raise FundSourceError("Invalid fund batch dates")
    return quotes


def _retry_delay(header: str | None, attempt: int) -> float:
    if header:
        try:
            delay = float(header)
            if math.isfinite(delay):
                return min(_MAX_RETRY_DELAY, max(0.0, delay))
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = (date - datetime.now(timezone.utc)).total_seconds()
                return min(_MAX_RETRY_DELAY, max(0.0, delay))
            except (ValueError, TypeError, OverflowError):
                pass
    return min(_MAX_RETRY_DELAY, 2.0 ** attempt)


def _read_body(response: requests.Response) -> str:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > MAX_RESPONSE_BYTES:
                raise FundSourceError("Fund response exceeds size limit")
        except ValueError as exc:
            raise FundSourceError("Invalid fund response length") from exc
    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            raise FundSourceError("Fund response exceeds size limit")
        body.extend(chunk)
    try:
        return body.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FundSourceError("Invalid fund response encoding") from exc


def fetch_quotes(
    codes: set[str], timeout: float = 20.0, attempts: int = 3
) -> dict[str, FundQuote]:
    """Fetch a fresh complete listing without credentials, redirects, or caching."""
    _validate_codes(codes)
    _validate_request_options(timeout, attempts)
    if not codes:
        return {}
    return parse_quotes(_fetch_text(SOURCE_URL, _PARAMS.copy(), timeout, attempts), codes)


def fetch_catalog_text(timeout: float = 20.0, attempts: int = 3) -> str:
    """Read the free name/type listing for unapproved-candidate discovery only."""
    _validate_request_options(timeout, attempts)
    return _fetch_text(CATALOG_URL, None, timeout, attempts)


def _validate_request_options(timeout: float, attempts: int) -> None:
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("timeout must be finite and between 0 and 60 seconds")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 5:
        raise ValueError("attempts must be between 1 and 5")


def _fetch_text(url: str, params: dict | None, timeout: float, attempts: int) -> str:
    for attempt in range(attempts):
        delay = _retry_delay(None, attempt)
        try:
            with requests.get(
                url,
                params=params,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            ) as response:
                if response.status_code == 200:
                    return _read_body(response)
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                if not retryable or attempt + 1 == attempts:
                    raise FundSourceError(f"Fund source HTTP {response.status_code}")
                delay = _retry_delay(response.headers.get("Retry-After"), attempt)
        except requests.exceptions.SSLError as exc:
            raise FundSourceError("Fund source TLS verification failed") from exc
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:
            if attempt + 1 == attempts:
                raise FundSourceError("Fund source connection failed") from exc
        except requests.RequestException as exc:
            raise FundSourceError("Fund source request failed") from exc
        time.sleep(delay)
    raise FundSourceError("Fund source attempts exhausted")
