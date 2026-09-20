"""Paginated Feishu closing-price information with actual serialized byte bounds."""

import html
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from matrix_etf.us_etf.listing import CATEGORIES, Quote

MAX_CARD_BYTES = 20 * 1024


def _escape(value: str) -> str:
    text = html.escape(" ".join(value.split())[:200])
    for character in ("\\", "[", "]", "*", "`", "(", ")", "_", "~"):
        text = text.replace(character, "\\" + character)
    return text


def _div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _shares(value: Decimal) -> str:
    unit = "份"
    if value >= Decimal("100000000"):
        value, unit = value / Decimal("100000000"), "亿份"
    elif value >= Decimal("10000"):
        value, unit = value / Decimal("10000"), "万份"
    number = format(value, "f")
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    return number + unit


def _subscription_lines(quote: Quote, expected: date) -> list[str]:
    quota = quote.subscription
    if (quota is None or quota.status not in {"open", "suspended"}
            or quota.effective_date != expected):
        return ["**一级申购额度：未确认**"]
    if quota.status == "suspended":
        return ["**一级申购：暂停申购**"]
    lines = ["**一级申购：开放**"]
    for label, cumulative, net in (
        ("当日单户上限", quota.account_cumulative, quota.account_net),
        ("当日基金整体上限", quota.fund_cumulative, quota.fund_net),
    ):
        limits = [
            f"{kind}{_shares(value)}"
            for kind, value in (("累计申购", cumulative), ("净申购", net))
            if value is not None and value > 0
        ]
        lines.append(f"{label}：" + (" · ".join(limits) if limits else "未确认"))
    if quota.creation_unit is not None and quota.creation_unit > 0:
        lines.append(f"最小申购单位：{_shares(quota.creation_unit)}")
    else:
        lines.append("最小申购单位：未确认")
    lines.append("实时剩余可申购份额：未确认")
    return lines


def _row(quote: Quote, index: int, expected: date) -> dict:
    product = quote.product
    code = product.symbol[:6]
    url = f"https://fund.10jqka.com.cn/{code}/"
    lines = [
        f"**{index}. {_escape(product.name)}**",
        f"[{code}]({url}) · {product.category}",
    ]
    lines.extend(_subscription_lines(quote, expected))
    if quote.trade_date and quote.stale:
        lines.append(f"**数据滞后：{quote.trade_date.isoformat()} 收盘**")
    if quote.close is None:
        lines.append("**暂无行情（有效收盘价缺失）**")
    else:
        lines.append(f"前复权收盘 ¥{quote.close:.4f}")
    if quote.change is None:
        lines[-1] += " · 涨跌幅暂无"
    elif quote.previous_is_market_day:
        lines[-1] += f" · {quote.change:+.2f}%"
    else:
        lines.append(f"较上次有效收盘 {quote.change:+.2f}%（非单日涨跌幅）")
    if quote.previous_date and not quote.previous_is_market_day:
        lines.append(f"对比日 {quote.previous_date.isoformat()}")
    return _div("\n".join(lines))


def build_cards(quotes: list[Quote], *, candidate_count: int, now: datetime,
                expected: date) -> list[dict]:
    counts = Counter(q.product.category for q in quotes)
    dates = sorted({q.trade_date.isoformat() for q in quotes if q.trade_date})
    date_label = "暂无行情日期" if not dates else (
        dates[0] if len(dates) == 1 else f"{dates[0]} 至 {dates[-1]}（逐只见明细）"
    )
    has_verified_quota = any(
        q.subscription is not None
        and q.subscription.effective_date == expected
        and q.subscription.status in {"open", "suspended"}
        for q in quotes
    )
    subscription_summary = (
        f"申购额度对应 {expected:%m-%d}：当日公布上限，非实时剩余。"
        if has_verified_quota else f"申购额度：未取得 {expected:%m-%d} 有效清单。"
    )
    summary = (
        f"{now:%m-%d %H:%M} 更新（北京时间） · 收盘日 {date_label}\n"
        + " · ".join(f"{c} {counts[c]}只" for c in CATEGORIES)
        + "\n" + subscription_summary
        + "\n证券账户买卖与下列一级申购分开，暂停申购不等于停牌。"
    )
    if expected != now.date():
        summary += f"\n以下为 {expected:%m-%d} 额度，不代表今天可申购份额。"
    if candidate_count > len(quotes):
        summary += "\n以下为部分产品。"
    if any(q.stale for q in quotes):
        summary += f"\n应有收盘日 {expected.isoformat()}，滞后行情已逐只标注。"

    def page(start, stop):
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content":
                              "美股 ETF · 场内交易"},
                    "template": "blue",
                },
                "elements": [_div(summary)] + [
                    _row(q, i + 1, expected) for i, q in enumerate(quotes[start:stop], start)
                ],
            },
        }

    if not quotes:
        raise ValueError("不能发送空 ETF 清单")
    cards = []
    start = 0
    while start < len(quotes):
        accepted = None
        stop = start
        for end in range(start + 1, len(quotes) + 1):
            candidate = page(start, end)
            if len(json.dumps(candidate).encode("utf-8")) > MAX_CARD_BYTES:
                break
            accepted, stop = candidate, end
        if accepted is None:
            raise ValueError("单只 ETF 卡片超过飞书20 KiB限制")
        cards.append(accepted)
        start = stop
    return cards
