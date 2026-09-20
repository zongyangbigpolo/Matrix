"""Fund-specific Feishu links and clearly labelled public channel limits."""

import html
import json
import re
import unicodedata
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from matrix_etf.funds.monitor import FundSelection

MAX_CARD_BYTES = 20 * 1024


def _escape(text: str) -> str:
    text = html.escape(" ".join(text.split())[:200])
    for char in ("\\", "[", "]", "*", "`"):
        text = text.replace(char, "\\" + char)
    return text


def _amount(value: Decimal) -> str:
    return format(value, ",.2f").rstrip("0").rstrip(".")


def _div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _fund_name_and_class(name: str) -> tuple[str, str]:
    name = unicodedata.normalize("NFKC", name)
    for old, new in (
        ("(QDII-FOF)", "(FOF)"), ("(QDII-LOF)", "(LOF)"), ("(QDII)", ""),
        ("(人民币)", ""), ("人民币", ""),
    ):
        name = name.replace(old, new)
    share = re.search(r"([ACDEFI])$", name)
    share_class = share[1] if share else ""
    if share:
        name = name[:share.start()]
    for word in ("ETF联接", "ETF发起式联接", "ETF发起联接", "发起式", "发起", "指数"):
        name = name.replace(word, "")
    return name.strip(), share_class


def build_card(
    selection: FundSelection, fetched_at: datetime, calendar_note: str | None = None,
    *, offset: int = 0, displayed_count: int | None = None,
) -> dict:
    if displayed_count is None:
        displayed_count = len(selection.groups)
    summary = [
        f"天天基金 · {fetched_at:%m-%d %H:%M} 更新（北京时间）",
        "**场外申购 · 每日额度 / 基金数量**",
    ]
    for label, categories in (
        ("标普合计", {"标普500", "标普500等权"}),
        ("纳斯达克合计", {"纳斯达克100"}),
    ):
        items = [item for item in selection.category_totals if item.category in categories]
        amount = sum((item.daily_limit for item in items), Decimal(0))
        count = sum(item.products for item in items)
        summary.append(f"**{label}：¥{_amount(amount)} · {count}只基金**")
    summary.extend([
        f"**美股总计：¥{_amount(selection.single_share_total)}"
        f" · {selection.eligible_groups}只基金**",
        "同一基金各份额取最高额度汇总；标普含等权。",
    ])
    elements = [_div("\n".join(summary))]
    if calendar_note:
        elements.append(_div(
            f"**{_escape(calendar_note)}**：下单确认可能顺延。"
        ))
    if not selection.groups:
        elements.append(_div("今天没有查到额度明确、可申购的基金。"))
    elif selection.eligible_groups > displayed_count:
        elements.append(_div("以下为部分可申购基金，顶部合计包含全部可申购产品。"))
    for index, group in enumerate(selection.groups, offset + 1):
        name, _ = _fund_name_and_class(group[0].name)
        url = f"https://fund.10jqka.com.cn/{group[0].code}/"
        lines = [f"**{index}. [{_escape(name)}]({url})**"]
        same_terms = len({(quote.minimum, quote.daily_limit) for quote in group}) == 1
        if same_terms:
            lines.append(
                f"每日可申购 **¥{_amount(group[0].daily_limit)}**"
                f" · ¥{_amount(group[0].minimum)}起购"
            )
        options = []
        for quote in group:
            url = f"https://fund.10jqka.com.cn/{quote.code}/"
            _, share_class = _fund_name_and_class(quote.name)
            label = f"{share_class}类 {quote.code}" if share_class else quote.code
            option = f"[{label}]({url})"
            if not same_terms:
                option += (
                    f"：每日可申购 ¥{_amount(quote.daily_limit)}"
                    f" · ¥{_amount(quote.minimum)}起"
                )
            options.append(option)
        lines.append((" · " if same_terms else "\n").join(options))
        elements.extend([{"tag": "hr"}, _div("\n".join(lines))])
    if selection.unknown:
        elements.append(_div(f"另有{len(selection.unknown)}个份额信息未确认，未计入统计。"))
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "美股基金 · 场外申购 | 天天基金"},
                "template": "turquoise" if selection.groups else "orange",
            },
            "elements": elements,
        },
    }


def build_cards(
    selection: FundSelection, fetched_at: datetime, calendar_note: str | None = None,
) -> list[dict]:
    card = build_card(selection, fetched_at, calendar_note)
    if len(json.dumps(card).encode("utf-8")) <= MAX_CARD_BYTES:
        return [card]
    cards = []
    start = 0
    while start < len(selection.groups):
        page = None
        end = start
        for stop in range(start + 1, len(selection.groups) + 1):
            candidate = build_card(
                replace(selection, groups=selection.groups[start:stop]),
                fetched_at, calendar_note, offset=start,
                displayed_count=len(selection.groups),
            )
            if len(json.dumps(candidate).encode("utf-8")) > MAX_CARD_BYTES:
                break
            page, end = candidate, stop
        if page is None:
            raise ValueError("One fund product exceeds Feishu's 20 KiB body limit")
        cards.append(page)
        start = end
    if not cards:
        raise ValueError("Fund summary exceeds Feishu's 20 KiB body limit")
    return cards


def build_discovery_card(candidates: list[dict[str, str]], fetched_at: datetime) -> dict:
    lines = [
        f"{fetched_at:%m-%d %H:%M} 更新（北京时间）",
        "**待核验新增基金：申购状态及额度尚未确认，不列入可申购清单。**",
    ]
    for candidate in candidates[:10]:
        lines.append(
            f"[{candidate['code']}](https://fund.10jqka.com.cn/{candidate['code']}/)"
            f" · {_escape(candidate['name'])}"
        )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "美股基金 · 待核验新增"},
                "template": "orange",
            },
            "elements": [_div("\n".join(lines))],
        },
    }
