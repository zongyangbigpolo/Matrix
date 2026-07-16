"""飞书通知模块：将选股结果（ETF / 股票）通过 Webhook 推送至飞书群。"""

import json
import time
from datetime import date

import requests

from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.names import get_strategy_display_name

logger = get_logger(__name__)


class FeishuNotifier:
    """飞书 Webhook 推送器。

    根据策略的 webhook_key 路由到对应的飞书机器人。
    若 webhook_key 未在 Settings.strategy_webhooks 中配置，
    则 fallback 到 Settings.feishu_webhook_url。
    """

    def __init__(self, settings: Settings, engine=None) -> None:
        """
        初始化 FeishuNotifier。

        Args:
            settings: Settings 实例，提供 Webhook URL 配置。
            engine: 可选数据引擎（ETF 或股票），用于查询标的名称（缺省时仅展示代码）。
        """
        self.settings = settings
        self.engine = engine

    @staticmethod
    def _to_xueqiu_code(symbol: str) -> str:
        """将 tickflow symbol（如 510300.SH / 600519.SH）转为雪球格式（SH510300）。"""
        if "." in symbol:
            code, suffix = symbol.split(".", 1)
            return f"{suffix.upper()}{code}"
        # 无后缀时按 A 股代码规则推断
        if symbol.startswith("5") or symbol.startswith("6"):
            return f"SH{symbol}"
        if symbol.startswith("4") or symbol.startswith("8"):
            return f"BJ{symbol}"
        return f"SZ{symbol}"

    @staticmethod
    def build_stale_warning(reason: str | None) -> str:
        """根据数据更新失败原因构造卡片顶部提示文案（含日期，原因过长时截断）。"""
        today = date.today().strftime("%Y-%m-%d")
        short = " ".join(str(reason or "").split())
        if len(short) > 120:
            short = short[:120] + "…"
        tail = f"（原因：{short}）" if short else ""
        return f"{today} 数据更新失败，以下为基于本地历史数据的结果{tail}"

    def _get_names(self, symbols: list[str]) -> dict[str, str]:
        """返回 {symbol: name} 映射，优先使用数据引擎（stock_basic / etf_basic）。"""
        if self.engine is not None:
            lookup = getattr(self.engine, "get_names", None) or getattr(
                self.engine, "get_etf_names", None
            )
            if lookup is not None:
                try:
                    return lookup(symbols)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"标的名称查询失败，回退为代码：{exc}")
        return {symbol: symbol for symbol in symbols}

    def _build_card(
        self,
        symbols: list[str],
        strategy_name: str,
        category: str = "ETF",
        stale_warning: str | None = None,
    ) -> dict:
        today = date.today().strftime("%Y-%m-%d")
        names = self._get_names(symbols)
        noun = "个股" if category.lower() in ("stock", "个股", "股票") else category
        display_name = get_strategy_display_name(strategy_name)

        links: list[str] = []
        for symbol in symbols:
            xq_code = self._to_xueqiu_code(symbol)
            name = names.get(symbol, symbol)
            links.append(f"[{name}](https://xueqiu.com/S/{xq_code})")

        symbol_text = " ".join(links) if links else "（无选股结果）"

        elements: list[dict] = []
        if stale_warning:
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"⚠️ **{stale_warning}**"},
                }
            )
            elements.append({"tag": "hr"})
        elements.extend(
            [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**日期：** {today}\n"
                            f"**策略：** {display_name}\n"
                            f"**候选数量：** {len(symbols)}"
                        ),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**候选{noun}：**\n{symbol_text}",
                    },
                },
            ]
        )

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"Matrix {noun}信号 | {display_name}",
                    },
                    # 数据更新失败时用红色标题，突出提醒
                    "template": "red" if stale_warning else "turquoise",
                },
                "elements": elements,
            },
        }

    def send(
        self,
        symbols: list[str],
        strategy_name: str,
        webhook_key: str = "default",
        category: str = "ETF",
        stale_warning: str | None = None,
    ) -> None:
        """
        将选股结果格式化为飞书卡片消息并 POST 至对应 Webhook。

        根据 webhook_key 从 Settings 中查找专属 URL；
        若未配置，则 fallback 到 feishu_webhook_url。

        Args:
            symbols: 选股结果代码列表。
            strategy_name: 策略名称，用于卡片标题。
            webhook_key: 策略标识，用于路由到对应飞书机器人。
            category: 产品类别（如 'ETF'、'Stock'），用于卡片标题与文案。
            stale_warning: 数据更新失败时的提示文案；非空时卡片顶部会显示醒目
                的红色警示，表明本次结果基于本地历史数据而非最新行情。

        Raises:
            HTTP 请求异常、非 JSON 响应或飞书错误码会记录日志，不向主流程抛出。
        """
        url = self.settings.get_webhook_url(webhook_key)
        payload = self._build_card(symbols, strategy_name, category, stale_warning)
        attempts = max(1, int(self.settings.feishu_retry_attempts))

        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(
                    url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    timeout=self.settings.feishu_timeout_seconds,
                )
            except requests.RequestException as exc:
                retryable = True
                message = f"飞书推送请求异常 [{webhook_key}]：{exc}"
            else:
                try:
                    resp_json = resp.json()
                except ValueError:
                    retryable = resp.status_code == 200 or resp.status_code >= 500
                    message = (
                        f"飞书推送响应不是 JSON [{webhook_key}] "
                        f"HTTP状态={resp.status_code} 响应={resp.text}"
                    )
                else:
                    if resp.status_code == 200 and resp_json.get("code") == 0:
                        logger.info(
                            f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只标的"
                        )
                        return
                    retryable = resp.status_code in (429,) or resp.status_code >= 500
                    message = (
                        f"飞书推送失败 [{webhook_key}] "
                        f"HTTP状态={resp.status_code} 飞书响应={resp.text}"
                    )

            if not retryable or attempt == attempts:
                logger.error(message)
                return

            logger.warning(f"{message}；准备第 {attempt + 1}/{attempts} 次重试")
            backoff = self.settings.feishu_retry_backoff_seconds
            if backoff > 0:
                time.sleep(backoff * (2 ** (attempt - 1)))
