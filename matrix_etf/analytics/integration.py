"""选股线 ↔ 绩效模块的极薄容错桥。

三个 main 只需构造一个 ``AnalyticsHook`` 并在每次推送前调用
``record_and_perf_line``：落库当次信号，并返回该策略的历史战绩文案（可挂到卡片）。
任何绩效侧异常都被吞掉，绝不影响选股与推送主流程。
"""

from datetime import date

from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.analytics.report import build_perf_line
from matrix_etf.analytics.signals import SignalStore
from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.hold_days import resolve_hold_days

logger = get_logger(__name__)


class AnalyticsHook:
    """把选股结果落库并生成历史战绩文案的轻量桥接。"""

    def __init__(self, settings: Settings, market: str) -> None:
        self.settings = settings
        self.market = market
        self.enabled = bool(settings.analytics_enabled)
        self._engine: AnalyticsEngine | None = None
        self._store: SignalStore | None = None
        windows = settings.get_analytics_windows()
        self.window_days = windows[0] if windows else 90

        if self.enabled:
            try:
                self._engine = AnalyticsEngine(settings)
                self._store = SignalStore(self._engine)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"绩效模块初始化失败，本次不落库/不展示战绩：{exc}")
                self.enabled = False

    def record_and_perf_line(self, strategy, symbols: list[str]) -> str | None:
        """落库本次信号并返回该策略历史战绩文案（失败返回 None，不抛出）。"""
        if not self.enabled or not symbols:
            return None

        strategy_name = type(strategy).__name__
        try:
            self._store.record(
                run_date=date.today().isoformat(),
                market=self.market,
                strategy=strategy_name,
                symbols=symbols,
                suggested_hold_days=resolve_hold_days(strategy),
                webhook_key=getattr(strategy, "webhook_key", None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"信号落库失败（不影响推送）：{exc}")

        try:
            return build_perf_line(
                self._engine, self.market, strategy_name, self.window_days
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"战绩文案生成失败（不影响推送）：{exc}")
            return None
