"""飞书通知模块：将 ETF 选股结果通过 Webhook 推送至飞书群。"""

import json
import time
from datetime import date

import requests

from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger

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
            engine: 可选 DataEngine，用于查询 ETF 名称（缺省时仅展示代码）。
        """
        self.settings = settings
        self.engine = engine

    @staticmethod
    def _to_xueqiu_code(symbol: str) -> str:
        """将 tickflow symbol（如 510300.SH）转为雪球格式（SH510300）。"""
        if "." in symbol:
            code, suffix = symbol.split(".", 1)
            return f"{suffix.upper()}{code}"
        # 无后缀时按 A 股 ETF 代码规则推断
        if symbol.startswith("5"):
            return f"SH{symbol}"
        return f"SZ{symbol}"

    def _get_names(self, symbols: list[str]) -> dict[str, str]:
        """返回 {symbol: name} 映射，优先使用本地 etf_basic。"""
        if self.engine is not None:
            try:
                return self.engine.get_etf_names(symbols)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"ETF 名称查询失败，回退为代码：{exc}")
        return {symbol: symbol for symbol in symbols}

    def _build_card(self, symbols: list[str], strategy_name: str) -> dict:
        today = date.today().strftime("%Y-%m-%d")
        names = self._get_names(symbols)

        links: list[str] = []
        for symbol in symbols:
            xq_code = self._to_xueqiu_code(symbol)
            name = names.get(symbol, symbol)
            links.append(f"[{name}](https://xueqiu.com/S/{xq_code})")

        symbol_text = " ".join(links) if links else "（无选股结果）"

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"Matrix ETF Signals | {strategy_name}",
                    },
                    "template": "turquoise",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": (
                                f"**日期：** {today}\n"
                                f"**策略：** {strategy_name}\n"
                                f"**候选数量：** {len(symbols)}"
                            ),
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**候选 ETF：**\n{symbol_text}",
                        },
                    },
                ],
            },
        }

    def send(
        self,
        symbols: list[str],
        strategy_name: str,
        webhook_key: str = "default",
    ) -> None:
        """
        将选股结果格式化为飞书卡片消息并 POST 至对应 Webhook。

        根据 webhook_key 从 Settings 中查找专属 URL；
        若未配置，则 fallback 到 feishu_webhook_url。

        Args:
            symbols: 选股结果代码列表。
            strategy_name: 策略名称，用于卡片标题。
            webhook_key: 策略标识，用于路由到对应飞书机器人。

        Raises:
            HTTP 请求异常、非 JSON 响应或飞书错误码会记录日志，不向主流程抛出。
        """
        url = self.settings.get_webhook_url(webhook_key)
        payload = self._build_card(symbols, strategy_name)
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
                        logger.info(f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只 ETF")
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
