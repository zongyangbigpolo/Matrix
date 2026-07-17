"""配置管理模块：通过 pydantic-settings 从环境变量或 .env 文件加载系统配置。"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Matrix 全局配置。

    通过环境变量或 .env 文件加载。除 ``feishu_webhook_url`` 为必填外，
    其余字段均有默认值。策略专属 webhook 通过 ``STRATEGY_WEBHOOK_<KEY>``
    前缀的环境变量自动收集到 ``strategy_webhooks``。
    """

    # 数据存储
    db_path: str = "data/matrix_etf.db"
    start_date: str = "2020-01-01"

    # tickflow 数据源
    tickflow_api_key: str = ""
    etf_universe: str = "CN_ETF"

    # 股票（A 股）数据源与存储（与 ETF 完全解耦：独立数据库文件与标的池）
    stock_db_path: str = "data/matrix_stock.db"
    stock_universe: str = "CN_Equity_A"

    # 美股数据源与存储（与 ETF / A 股完全解耦：独立数据库文件与标的池）
    us_db_path: str = "data/matrix_us.db"
    us_universe: str = "US_Equity"
    us_start_date: str = "2020-01-01"

    # 策略参数
    liquidity_min_amount: float = 50_000_000.0
    rps_period: int = 120
    rps_threshold: float = 90.0
    breakout_period: int = 60
    volume_surge: float = 1.5

    # 股票策略参数（日线交易日口径）
    stock_liquidity_min_amount: float = 100_000_000.0
    stock_ma_volume_surge: float = 1.5
    stock_rps_period: int = 120
    stock_rps_threshold: float = 90.0

    # 美股策略参数（日线交易日口径）
    # 免费档美股不提供成交额（amount 恒为 0），流动性改用「美元成交额 = close × volume」估算。
    us_liquidity_min_dollar_volume: float = 20_000_000.0
    us_ma_volume_surge: float = 1.5
    us_rps_period: int = 120
    us_rps_threshold: float = 90.0
    us_breakout_period: int = 60
    us_volume_surge: float = 1.5

    # Mega7 风格轮动策略参数（日线交易日口径）
    mega7_momentum_periods: str = "21,63,126"
    mega7_volatility_days: int = 21
    mega7_top_n: int = 10
    mega7_downside_lookback_days: int = 63
    mega7_downside_threshold: float = 0.5
    mega7_volume_short_days: int = 21
    mega7_volume_long_days: int = 63
    mega7_volume_multiplier_floor: float = 0.5
    mega7_volume_multiplier_cap: float = 2.0

    # 飞书推送
    feishu_webhook_url: str  # 必填字段，缺失时抛出 ValidationError
    strategy_webhooks: dict[str, str] = Field(default_factory=dict)
    feishu_timeout_seconds: float = 10.0
    feishu_retry_attempts: int = 3
    feishu_retry_backoff_seconds: float = 1.0

    # 数据同步重试（应对 tickflow 免费档 60/min 限流）：被限流不直接放弃，
    # 而是按指数退避多次重试，尽力把当日数据拉全，仅在多次仍失败时才降级。
    sync_retry_attempts: int = 6
    sync_retry_base_delay: float = 2.0
    sync_retry_max_delay: float = 60.0

    # 数据同步「持续拉取直至完成」机制：收盘后不再「拉不到就用旧数据」，而是每隔
    # sync_persist_round_interval 秒补拉一轮，直到覆盖到当日最新交易日（或覆盖率
    # 收敛且达标）才发送策略卡片；坚持 sync_persist_max_seconds 秒仍拉不全，则发送
    # 告警卡片并跳过本次策略推送。默认可在观察实际覆盖率后按需调整。
    sync_persist_max_seconds: float = 10800.0  # 最长坚持 3 小时
    sync_persist_round_interval: float = 300.0  # 每轮补拉间隔 5 分钟
    sync_persist_target_coverage: float = 0.9  # 覆盖率达此比例即视为拉取完成
    sync_persist_min_coverage: float = 0.5  # 覆盖率收敛后仍可接受的最低下限

    # 交易日保护：日常模式默认跳过周末和配置的 A 股休市日
    skip_non_trading_day: bool = True
    cn_market_holidays: str = ""
    # 美股休市日（YYYY-MM-DD，逗号分隔）；未配置时仅按周末过滤
    us_market_holidays: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def model_post_init(self, __context: object) -> None:
        """初始化后合并 STRATEGY_WEBHOOK_ 前缀的环境变量到 strategy_webhooks。"""
        import os

        prefix = "STRATEGY_WEBHOOK_"
        webhooks: dict[str, str] = dict(self.strategy_webhooks)
        for key, value in os.environ.items():
            if key.upper().startswith(prefix):
                strategy_key = key[len(prefix):].lower()
                webhooks[strategy_key] = value

        object.__setattr__(self, "strategy_webhooks", webhooks)

    def get_webhook_url(self, webhook_key: str) -> str:
        """
        根据 webhook_key 返回对应的 Webhook URL。

        优先从 strategy_webhooks 查找，找不到则 fallback 到 feishu_webhook_url。

        Args:
            webhook_key: 策略标识，如 'rps'、'trend'。

        Returns:
            对应的 Webhook URL 字符串。
        """
        return self.strategy_webhooks.get(webhook_key.lower(), self.feishu_webhook_url)


_settings: Settings | None = None


def get_settings() -> Settings:
    """返回全局 Settings 单例。

    首次调用时从环境变量或 .env 文件加载配置。
    若必填字段（feishu_webhook_url）缺失，抛出 pydantic_core.ValidationError。

    Returns:
        Settings: 全局唯一的配置实例。

    Raises:
        pydantic_core.ValidationError: 当必填字段缺失或字段类型不匹配时抛出。
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
