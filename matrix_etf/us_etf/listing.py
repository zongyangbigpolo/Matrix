"""Conservative name-based scope and bounded closing-price presentation."""

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from matrix_etf.core.config import Settings
from matrix_etf.core.trading_calendar import is_cn_trading_day

MAX_PRODUCTS = 50
CATEGORIES = ("纳斯达克100", "标普500", "其他美股指数/行业")
_SYMBOL = re.compile(r"(?:5[1268][0-9]{4}\.SH|15[0-9]{4}\.SZ)\Z")
_EXCLUDED = re.compile(
    r"联接|LOF|美元|美金|美圆|美汇|美钞|USD|外币|港币|港元|现汇|现钞|全球|环球|亚洲|亚太|"
    r"欧元|英镑|日元|澳元|加元|EUR|GBP|JPY|AUD|CAD|HKD|"
    r"香港|港股|恒生|A股|中国|中华|沪港|沪深|中证|日经|日本|欧洲|德国|新兴|"
    r"黄金|白银|贵金属|原油|期货|商品|大宗|债|货币|REIT"
)
# These abbreviated S&P sector names omit their US-equity scope. Require both
# the reviewed product code and its current name, never just the word “标普”.
_US_SECTOR_NAMES = {
    "513350.SH": "标普油气ETF富国",
    "159518.SZ": "标普油气ETF嘉实",
    "159529.SZ": "标普消费ETF景顺",
    "159502.SZ": "标普生物科技ETF嘉实",
}


@dataclass(frozen=True)
class Product:
    symbol: str
    name: str
    category: str


@dataclass(frozen=True)
class Quote:
    product: Product
    trade_date: date | None = None
    close: float | None = None
    amount: float | None = None
    change: float | None = None
    previous_date: date | None = None
    previous_is_market_day: bool = False
    stale: bool = False


def classify(info: dict) -> Product | None:
    symbol = info.get("symbol", "")
    name = info.get("name", "")
    kind = info.get("type") or info.get("instrument_type")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        return None
    if not isinstance(name, str) or not name.strip() or str(kind).lower() != "etf":
        return None
    if info.get("exchange") not in (None, "", symbol[-2:]):
        return None
    normalized = unicodedata.normalize("NFKC", name).upper().replace(" ", "")
    if "ETF" not in normalized or _EXCLUDED.search(normalized):
        return None
    if re.search(r"(?:纳指|纳斯达克|NASDAQ)(?:100)?(?:指数)?ETF", normalized):
        category = CATEGORIES[0]
    elif re.search(r"(?:标普|S&P|SP)500(?:等权(?:重)?)?(?:指数)?ETF", normalized):
        category = CATEGORIES[1]
    elif (
        re.search(
            r"纳指|纳斯达克|NASDAQ|(?:标普|S&P|SP)500|美股|道琼斯(?:工业|ETF)|"
            r"美国50ETF|美国.*(?:股票|科技|消费|医疗|红利)", normalized,
        )
        or _US_SECTOR_NAMES.get(symbol) == normalized
    ):
        category = CATEGORIES[2]
    else:
        return None
    return Product(symbol, name.strip(), category)


def finite_number(value, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        return None
    return number


def previous_trading_day(day: date, settings: Settings) -> date:
    for _ in range(370):
        day -= timedelta(days=1)
        if is_cn_trading_day(day, settings):
            return day
    raise ValueError("交易日配置无法确定上一交易日")


def expected_close_day(now: datetime, settings: Settings) -> date:
    day = now.date()
    if now.hour < 15 or not is_cn_trading_day(day, settings):
        return previous_trading_day(day, settings)
    return day


def make_quote(product: Product, rows: list[tuple], expected: date, settings: Settings) -> Quote:
    # Source passes only this request's dates; never turn a failed refresh into a cache hit.
    observations = []
    for raw_day, raw_close, raw_amount in rows:
        try:
            day = date.fromisoformat(raw_day)
        except (TypeError, ValueError):
            continue
        if day <= expected:
            observations.append((day, finite_number(raw_close, positive=True),
                                 finite_number(raw_amount)))
    observations.sort(reverse=True, key=lambda row: row[0])
    if not observations:
        return Quote(product)
    day, close, amount = observations[0]
    prior = next(((d, c) for d, c, _ in observations[1:] if c is not None), None)
    prior_day, prior_close = prior if prior else (None, None)
    change = (close / prior_close - 1) * 100 if close and prior_close else None
    if change is not None and not math.isfinite(change):
        change = None
    return Quote(product, day, close, amount, change, prior_day,
                 prior_day == previous_trading_day(day, settings), day < expected)


def select_quotes(quotes: list[Quote]) -> list[Quote]:
    if len({q.product.symbol for q in quotes}) != len(quotes):
        raise ValueError("ETF 清单出现重复代码")
    return sorted(quotes, key=lambda q: (
        q.amount is None, -(q.amount or 0), q.product.symbol,
    ))[:MAX_PRODUCTS]
