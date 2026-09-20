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


def _row(quote: Quote, index: int, expected: date) -> dict:
    product = quote.product
    code = product.symbol[:6]
    url = f"https://fund.10jqka.com.cn/{code}/"
    exchange = "上交所" if product.symbol.endswith(".SH") else "深交所"
    lines = [
        f"**{index}. {_escape(product.name)}**",
        f"[{code}]({url}) · {exchange} · {product.category}",
    ]
    if quote.market_date and quote.market_date < expected:
        lines.append(f"**数据滞后：{quote.market_date.isoformat()} 收盘**")
    if quote.market_close is None:
        lines.append("**暂无行情（有效收盘价缺失）**")
    else:
        lines.append(f"参考收盘 ¥{quote.market_close:.4f}")
    if quote.market_change is None:
        lines[-1] += " · 涨跌幅暂无"
    elif quote.market_previous_is_market_day:
        lines[-1] += f" · {quote.market_change:+.2f}%"
    else:
        lines.append(f"较上次有效收盘 {quote.market_change:+.2f}%（非单日涨跌幅）")
    if quote.market_previous_date and not quote.market_previous_is_market_day:
        lines.append(f"对比日 {quote.market_previous_date.isoformat()}")
    if quote.market_close is not None:
        lot_cost = Decimal(str(quote.market_close)) * 100
        lines.append(f"**1手100份 · 收盘估算 ¥{lot_cost:,.2f}**（费用另计）")
    else:
        lines.append("1手100份 · 买入金额请查看实时行情")
    return _div("\n".join(lines))


def build_cards(quotes: list[Quote], *, candidate_count: int, now: datetime,
                expected: date) -> list[dict]:
    counts = Counter(q.product.category for q in quotes)
    dates = sorted({q.market_date.isoformat() for q in quotes if q.market_date})
    date_label = "暂无行情日期" if not dates else (
        dates[0] if len(dates) == 1 else f"{dates[0]} 至 {dates[-1]}（逐只见明细）"
    )
    summary = (
        f"{now:%m-%d %H:%M} 更新（北京时间） · 收盘日 {date_label}（非实时）\n"
        + " · ".join(f"{c} {counts[c]}只" for c in CATEGORIES)
        + "\n买入渠道：沪深证券账户（东方财富等），无需美国证券账户。"
        + "\n可买数量看账户可用资金及委托页；持仓市值不等于可用资金。"
        + "\n实时停牌状态、溢价及账户交易权限请在下单前核对。"
    )
    if candidate_count > len(quotes):
        summary += "\n以下为部分产品。"
    if any(q.market_date and q.market_date < expected for q in quotes):
        summary += f"\n应有收盘日 {expected.isoformat()}，滞后行情已逐只标注。"

    def page(start, stop):
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content":
                              "沪深市场 · 美股 ETF"},
                    "template": "blue",
                },
                "elements": [_div(summary)] + [
                    _row(q, i + 1, expected)
                    for i, q in enumerate(quotes[start:stop], start)
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
