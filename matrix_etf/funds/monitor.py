"""Filter a fresh subscription response without keeping a historical fund database."""

from dataclasses import dataclass
from decimal import Decimal

from matrix_etf.funds.catalog import us_index_category
from matrix_etf.funds.source import FundQuote, FundSourceError

OPEN_STATUSES = {"开放申购", "限大额"}
CLOSED_STATUSES = {"暂停申购", "场内交易", "封闭期", "认购期"}
# The source uses very large placeholder values; their unlimited meaning is undocumented.
UNCERTAIN_LIMIT = Decimal("100000000000")


@dataclass(frozen=True)
class CategoryTotal:
    category: str
    products: int
    daily_limit: Decimal


@dataclass(frozen=True)
class FundSelection:
    groups: tuple[tuple[FundQuote, ...], ...]
    catalog_count: int
    eligible_groups: int
    unavailable_count: int
    unknown: tuple[tuple[str, str], ...]
    eligible_shares: int
    single_share_total: Decimal
    category_totals: tuple[CategoryTotal, ...]


def select_funds(
    catalog: list[tuple[str, ...]], quotes: dict[str, FundQuote], limit: int = 10
) -> FundSelection:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError("Fund recommendation limit must be between 1 and 10")
    codes = {code for group in catalog for code in group}
    if not codes or not codes.intersection(quotes):
        raise FundSourceError("The subscription response contains none of the approved fund codes")
    eligible = []
    unknown = []
    unavailable = 0
    for group in catalog:
        accepted = []
        for code in group:
            quote = quotes.get(code)
            if quote is None:
                unknown.append((code, "数据源未返回"))
                continue
            # A leftover numeric limit must never make a suspended product look buyable.
            if quote.status in CLOSED_STATUSES:
                unavailable += 1
                continue
            reason = None
            if not us_index_category(quote.name, quote.fund_type):
                reason = "产品范围或币种需重新核验"
            elif quote.status not in OPEN_STATUSES:
                reason = "未识别的申购状态"
            elif quote.issue:
                reason = "额度字段异常"
            elif quote.minimum is None or quote.minimum <= 0:
                reason = "起购金额未确认"
            elif quote.daily_limit is None or quote.daily_limit <= 0:
                reason = "日累计上限未确认"
            elif quote.daily_limit >= UNCERTAIN_LIMIT:
                reason = "超大额度字段含义未确认"
            elif quote.daily_limit < quote.minimum:
                reason = "上限低于起购金额"
            elif any(value.quantize(Decimal("0.01")) != value for value in (
                quote.minimum, quote.daily_limit
            )):
                reason = "人民币金额精度需核实"
            if reason:
                unknown.append((code, reason))
            else:
                accepted.append(quote)
        if accepted:
            eligible.append(tuple(sorted(accepted, key=lambda quote: quote.code)))
    # Never add A/C limits: rank a product by its largest individually reported limit.
    eligible.sort(key=lambda group: (-max(q.daily_limit for q in group), group[0].code))
    totals: dict[str, tuple[int, Decimal]] = {}
    for group in eligible:
        category = us_index_category(group[0].name, group[0].fund_type)
        assert category is not None  # Every accepted quote passed the scope check above.
        count, amount = totals.get(category, (0, Decimal(0)))
        totals[category] = (count + 1, amount + max(q.daily_limit for q in group))
    category_totals = tuple(
        CategoryTotal(category, *totals[category])
        for category in ("纳斯达克100", "标普500", "标普500等权")
        if category in totals
    )
    return FundSelection(
        groups=tuple(eligible[:limit]),
        catalog_count=len(codes),
        eligible_groups=len(eligible),
        unavailable_count=unavailable,
        unknown=tuple(unknown),
        eligible_shares=sum(len(group) for group in eligible),
        single_share_total=sum((item.daily_limit for item in category_totals), Decimal(0)),
        category_totals=category_totals,
    )
