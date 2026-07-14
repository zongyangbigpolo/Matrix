"""策略基类模块：定义所有 ETF 选股策略的抽象接口。"""

from abc import ABC, abstractmethod

import pandas as pd

from matrix_etf.core.config import Settings
from matrix_etf.data.engine import DataEngine


class BaseStrategy(ABC):
    """ETF 选股策略抽象基类。

    所有具体策略必须继承此类并实现 run() 方法。

    Attributes:
        webhook_key: 策略对应的飞书 webhook 标识，用于路由到不同机器人。
            默认为 'default'，将使用 Settings.feishu_webhook_url。
            子类可覆盖此属性以路由到专属机器人，例如 'rps'。
    """

    webhook_key: str = "default"

    def __init__(self, engine: DataEngine, settings: Settings) -> None:
        """
        初始化策略。

        Args:
            engine: DataEngine 实例，用于读取行情数据。
            settings: Settings 实例，用于读取配置。
        """
        self.engine = engine
        self.settings = settings

    def _passes_liquidity(self, amount: pd.Series) -> bool:
        """近 20 日平均成交额是否达到流动性门槛。"""
        if len(amount) < 20:
            return False
        avg_amount = amount.tail(20).mean()
        return bool(avg_amount >= self.settings.liquidity_min_amount)

    @abstractmethod
    def run(self) -> list[str]:
        """
        执行选股逻辑，返回选中的 ETF 代码列表。

        Returns:
            满足策略条件的 ETF 代码列表，如 ['510300.SH', '159915.SZ']。
            无选股结果时返回空列表。
        """
        ...
