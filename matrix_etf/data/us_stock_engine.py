"""美股数据引擎：负责美股 SQLite 行情存储与 tickflow 数据同步。

与 ETF 引擎（``matrix_etf.data.engine.DataEngine``）和 A 股引擎
（``matrix_etf.data.stock_engine.StockDataEngine``）**完全隔离**：使用独立的
数据库文件（``Settings.us_db_path``，默认 ``data/matrix_us.db``）和独立的标的池
（``Settings.us_universe``，默认 ``US_Equity``，约 1.2 万只美股）。

实现上直接复用 ``StockDataEngine`` 的全部同步 / 读取逻辑（``stock_daily`` /
``stock_basic`` 两张表结构完全一致），仅切换到美股专属的数据库文件、标的池与
起始日期，从而在**不同的物理数据库文件**中隔离数据，互不影响。

⚠️ 数据源限制：免费档 tickflow 对美股**不返回成交额**（``amount`` 恒为 0），
因此美股策略的流动性过滤必须使用「美元成交额 = close × volume」估算，
不能依赖 ``amount`` 字段（见 ``matrix_etf/strategy/us``）。
"""

from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger
from matrix_etf.data.stock_engine import StockDataEngine

logger = get_logger(__name__)


class UsStockDataEngine(StockDataEngine):
    """美股行情数据引擎，负责 SQLite 存储和 tickflow 数据同步。

    继承自 ``StockDataEngine`` 以复用其增量同步、回填、缺口补拉与读取接口，
    但通过独立的数据库文件、标的池（``US_Equity``）与起始日期实现与 A 股、ETF
    数据的物理隔离。
    """

    def __init__(self, settings: Settings) -> None:
        # 不调用父类 __init__，避免其绑定到 A 股的 stock_db_path / stock_universe；
        # 手动装配美股专属字段后再执行同样的建表初始化。
        self.db_path: str = settings.us_db_path
        self.start_date: str = settings.us_start_date
        self.universe: str = settings.us_universe
        self.api_key: str = settings.tickflow_api_key
        self._tf = None
        self._init_db()
