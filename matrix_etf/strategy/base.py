"""策略基类模块：定义各类金融产品选股策略的抽象接口。"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import pandas as pd

from matrix_etf.core.config import Settings

if TYPE_CHECKING:
    from matrix_etf.data.engine import DataEngine
    from matrix_etf.data.stock_engine import StockDataEngine
    from matrix_etf.data.us_stock_engine import UsStockDataEngine


class BaseStrategy(ABC):
    """选股策略抽象基类。

    所有具体策略必须继承此类并实现 run() 方法。ETF 与股票策略共享该基类，
    通过传入不同的数据引擎（``DataEngine`` / ``StockDataEngine``）复用相同的
    ``get_ohlcv`` / ``get_local_symbols`` 读取接口。

    Attributes:
        webhook_key: 策略对应的飞书 webhook 标识，用于路由到不同机器人。
            默认为 'default'，将使用 Settings.feishu_webhook_url。
            子类可覆盖此属性以路由到专属机器人，例如 'rps'、'stock_turtle'。
    """

    webhook_key: str = "default"

    def __init__(
        self,
        engine: "DataEngine | StockDataEngine | UsStockDataEngine",
        settings: Settings,
    ) -> None:
        """
        初始化策略。

        Args:
            engine: 数据引擎实例（ETF 或股票），用于读取行情数据。
            settings: Settings 实例，用于读取配置。
        """
        self.engine = engine
        self.settings = settings

    def _passes_liquidity(self, amount: pd.Series) -> bool:
        """近 20 日平均成交额是否达到流动性门槛（ETF 策略使用）。"""
        if len(amount) < 20:
            return False
        avg_amount = amount.tail(20).mean()
        return bool(avg_amount >= self.settings.liquidity_min_amount)

    @staticmethod
    def _passes_dollar_volume(
        close: pd.Series,
        volume: pd.Series,
        min_dollar_volume: float,
    ) -> bool:
        """近 20 日平均美元成交额（close×volume）是否达标（美股策略使用）。

        免费档美股不提供成交额（``amount`` 恒为 0），故用 ``close × volume``
        估算美元成交额作为流动性代理。
        """
        if len(close) < 20 or len(volume) < 20:
            return False
        dollar_volume = (close.tail(20) * volume.tail(20)).mean()
        return bool(dollar_volume >= min_dollar_volume)

    @abstractmethod
    def run(self) -> list[str]:
        """
        执行选股逻辑，返回选中的标的代码列表。

        Returns:
            满足策略条件的标的代码列表，如 ['510300.SH', '600519.SH']。
            无选股结果时返回空列表。
        """
        ...
