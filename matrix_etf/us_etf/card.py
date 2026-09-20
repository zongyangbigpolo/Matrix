"""Paginated Feishu closing-price information with actual serialized byte bounds."""

import html
import json
from collections import Counter
from datetime import date, datetime

from matrix_etf.us_etf.listing import CATEGORIES, Quote

MAX_CARD_BYTES = 20 * 1024


def _escape(value: str) -> str:
    text = html.escape(" ".join(value.split())[:200])
    for character in ("\\", "[", "]", "*", "`", "(", ")", "_", "~"):
        text = text.replace(character, "\\" + character)
    return text


def _div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _row(quote: Quote, index: int) -> dict:
    product = quote.product
    url = f"https://quote.eastmoney.com/{product.symbol[-2:].lower()}{product.symbol[:6]}.html"
    lines = [
        f"**{index}. [{_escape(product.name)}]({url})**",
        f"{product.symbol} · {product.category}",
    ]
    if quote.trade_date:
        lines.append(f"行情日 {quote.trade_date.isoformat()}" + (
            " · **数据滞后，非本次应有收盘日**" if quote.stale else ""
        ))
    if quote.close is None:
        lines.append("**暂无行情（有效收盘价缺失）**")
    else:
        lines.append(f"收盘 ¥{quote.close:.4f}")
    if quote.change is None:
        lines.append("涨跌幅：暂无（缺少有效对比收盘价）")
    elif quote.previous_is_market_day:
        lines.append(f"日涨跌幅 {quote.change:+.2f}%")
    else:
        lines.append(f"较上次有效收盘 {quote.change:+.2f}%（非单日涨跌幅）")
    if quote.previous_date:
        lines.append(f"对比日 {quote.previous_date.isoformat()}")
    lines.append(
        f"人民币成交额 ¥{quote.amount:,.2f}" if quote.amount is not None
        else "人民币成交额：暂无"
    )
    return _div("\n".join(lines))


def build_cards(quotes: list[Quote], *, candidate_count: int, now: datetime,
                expected: date) -> list[dict]:
    counts = Counter(q.product.category for q in quotes)
    dates = sorted({q.trade_date.isoformat() for q in quotes if q.trade_date})
    date_label = "暂无行情日期" if not dates else (
        dates[0] if len(dates) == 1 else f"{dates[0]} 至 {dates[-1]}（逐只见明细）"
    )
    summary = (
        f"TickFlow 历史日线 · 查询 {now:%Y-%m-%d %H:%M}（北京时间）\n"
        f"应有收盘日 {expected.isoformat()} · 实际行情日 {date_label}\n"
        f"明确候选 {candidate_count} 只 · 展示 {len(quotes)} 只（最多50只）\n"
        + " · ".join(f"{c} {counts[c]}只" for c in CATEGORIES)
        + "\n按人民币成交额从高到低；前复权收盘行情，非实时。"
    )

    def page(start, stop):
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content":
                              f"Matrix 境内美股 ETF 收盘行情 | {date_label}"},
                    "template": "blue",
                },
                "elements": [_div(summary), _div(
                    f"第 {start + 1}–{stop} 只 / 共 {len(quotes)} 只（分类数量为全清单）"
                )] + [_row(q, i + 1) for i, q in enumerate(quotes[start:stop], start)],
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
