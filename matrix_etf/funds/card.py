"""Fund-specific Feishu links and clearly labelled public channel limits."""

import html
from datetime import datetime
from decimal import Decimal

from matrix_etf.funds.catalog import us_index_category
from matrix_etf.funds.monitor import FundSelection
from matrix_etf.funds.source import SOURCE_URL


def _escape(text: str) -> str:
    text = html.escape(" ".join(text.split())[:200])
    for char in ("\\", "[", "]", "*", "`"):
        text = text.replace(char, "\\" + char)
    return text


def _amount(value: Decimal) -> str:
    return format(value, ",.2f").rstrip("0").rstrip(".")


def _div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def build_card(
    selection: FundSelection, fetched_at: datetime, calendar_note: str | None = None
) -> dict:
    elements = [_div(
        f"**渠道：天天基金公开数据｜人民币场外申购**\n"
        f"**查询完成：{fetched_at:%Y-%m-%d %H:%M:%S %Z}**（非规则生效时间）\n"
        f"目录 {selection.catalog_count} 类份额；符合条件 {selection.eligible_groups} 个产品；"
        f"展示 {len(selection.groups)} 个（同产品份额合并）"
    )]
    if calendar_note:
        elements.append(_div(
            f"**日历提示：{_escape(calendar_note)}**。委托受理与份额确认日期以销售平台为准。"
        ))
    if not selection.groups:
        elements.append(_div("**本次没有可确认申购状态及额度的产品，不补用旧数据。**"))
    for index, group in enumerate(selection.groups, 1):
        lines = [f"**{index}. {us_index_category(group[0].name, group[0].fund_type)}**"]
        for quote in group:
            url = f"https://fund.eastmoney.com/{quote.code}.html"
            lines.append(
                f"[{_escape(quote.name)}]({url})（{quote.code}）\n"
                f"{quote.status}｜起购 ¥{_amount(quote.minimum)}｜"
                f"公布日累计上限 ¥{_amount(quote.daily_limit)}"
            )
        elements.extend([{"tag": "hr"}, _div("\n".join(lines))])
    if selection.unknown:
        details = "；".join(f"{code}：{reason}" for code, reason in selection.unknown[:8])
        elements.append(_div(f"**待核实 {len(selection.unknown)} 类份额，不纳入清单：**\n{details}"))
    elements.extend([{"tag": "hr"}, _div(
        f"暂停/封闭/场内等未纳入：{selection.unavailable_count} 类份额。\n"
        "**额度不是个人剩余额度；A/C等份额可能共用上限，不可相加。**\n"
        "公开入口无稳定性和实时性保证；单笔限制、定投例外及渠道合并规则未核实。"
        "零值、缺失值及超大占位值不解释为不限额；支付宝/银行/直销限额不能由此推断。\n"
        "这是申购信息，不是买入建议；仍有美股波动、汇率及QDII确认延迟风险。"
        "最终以实际交易页面和基金公告为准。\n"
        f"[公开数据来源]({SOURCE_URL}?t=8&page=1,50000&js=reData&sort=fcode,asc)"
    )])
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "Matrix 美股基金申购清单 | 天天基金"},
                "template": "orange" if selection.unknown or not selection.groups else "turquoise",
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
