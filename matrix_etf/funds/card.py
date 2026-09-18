"""Fund-specific Feishu links and clearly labelled public channel limits."""

import html
import re
import unicodedata
from datetime import datetime
from decimal import Decimal

from matrix_etf.funds.monitor import FundSelection


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
    selection: FundSelection, fetched_at: datetime, calendar_note: str | None = None
) -> dict:
    summary = [
        f"天天基金 · {fetched_at:%m-%d %H:%M} 更新（北京时间）",
        "**今日可买 · 单日额度 / 基金数量**",
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
        "每只基金只选一个份额，按最高额度统计；标普包含等权基金。",
    ])
    elements = [_div("\n".join(summary))]
    if calendar_note:
        elements.append(_div(
            f"**{_escape(calendar_note)}**：下单确认可能顺延。"
        ))
    if not selection.groups:
        elements.append(_div("今天没有查到额度明确、可申购的基金。"))
    else:
        hidden = selection.eligible_groups - len(selection.groups)
        listing = f"**额度从高到低 · 展示{len(selection.groups)}只**"
        if hidden:
            listing += f"\n另{hidden}只未展开，已计入顶部总数和额度。"
        elements.append(_div(listing))
    for index, group in enumerate(selection.groups, 1):
        name, _ = _fund_name_and_class(group[0].name)
        url = f"https://fund.eastmoney.com/{group[0].code}.html"
        lines = [f"**{index}. [{_escape(name)}]({url})**"]
        same_terms = len({(quote.minimum, quote.daily_limit) for quote in group}) == 1
        if same_terms:
            lines.append(
                f"每日上限 **¥{_amount(group[0].daily_limit)}**"
                f" · ¥{_amount(group[0].minimum)}起购"
            )
        options = []
        for quote in group:
            url = f"https://fund.eastmoney.com/{quote.code}.html"
            _, share_class = _fund_name_and_class(quote.name)
            label = f"{share_class}类 {quote.code}" if share_class else quote.code
            option = f"[{label}]({url})"
            if not same_terms:
                option += (
                    f"：每日 ¥{_amount(quote.daily_limit)}"
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
                "title": {"tag": "plain_text", "content": "Matrix 美股基金申购清单 | 天天基金"},
                "template": "turquoise" if selection.groups else "orange",
            },
            "elements": elements,
        },
    }


def build_discovery_card(candidates: list[dict[str, str]], fetched_at: datetime) -> dict:
    lines = [
        f"查询时间：{fetched_at:%Y-%m-%d %H:%M:%S %Z}",
        f"发现 {len(candidates)} 类未纳入目录的候选份额，最多展示10类。",
        "**仅为目录待审核候选，未确认申购状态、币种条款和限额，不是可买清单。**",
    ]
    for candidate in candidates[:10]:
        lines.append(
            f"[{_escape(candidate['name'])}]"
            f"(https://fund.eastmoney.com/{candidate['code']}.html)（{candidate['code']}）"
        )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "Matrix 美股基金目录 | 待核验新增"},
                "template": "orange",
            },
            "elements": [_div("\n".join(lines))],
        },
    }
