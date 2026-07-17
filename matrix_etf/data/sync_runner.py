"""数据同步「持续拉取直至完成」执行器。

收盘后触发的日常任务不再采用「拉不到就直接用本地旧数据」的降级策略，而是
调用 :func:`sync_until_stable` 反复补拉，直到当日最新交易日数据基本拉全、或
覆盖率收敛达标为止，才让上层去跑策略、发飞书卡片。

设计要点（应对 tickflow 免费档 60/min 限流）：

* **第 1 轮**执行完整增量同步 :meth:`engine.sync_daily`。
* 若覆盖到「预期最新交易日」且覆盖率 ≥ ``target_coverage`` → 立即成功返回
  （健康日零额外延迟）。
* 否则每隔 ``round_interval`` 秒补拉一轮：
  - 若已拉到预期最新交易日（部分标的被限流缺口）→ :meth:`engine.repair_latest_gaps`
    只补缺口标的；
  - 若连预期最新交易日都没拉到（整体失败）→ 再跑一次完整 :meth:`engine.sync_daily`。
* 当覆盖率不再提升（收敛）且 ≥ ``min_coverage`` → 视为拉全（正常情况本就有少量
  新上市/停牌标的无法覆盖最新日）。
* 坚持 ``max_seconds`` 秒仍拉不全：覆盖率 ≥ ``min_coverage`` 视为成功，否则失败，
  由上层发送告警卡片并跳过策略推送。

引擎以 duck typing 方式使用，ETF / A 股 / 美股三个引擎方法签名一致，故无需共享基类。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SyncOutcome:
    """一次「持续拉取」的结果。"""

    success: bool
    covered: int
    total: int
    latest_date: str | None
    rounds: int
    elapsed_seconds: float
    reason: str

    @property
    def ratio(self) -> float:
        """最新交易日覆盖率（0~1）。"""
        return self.covered / self.total if self.total else 1.0

    def describe(self) -> str:
        """人类可读的一句话摘要（用于日志与告警卡片）。"""
        return (
            f"最新交易日 {self.latest_date}，覆盖 {self.covered}/{self.total}"
            f"（{self.ratio:.0%}），共 {self.rounds} 轮、约 "
            f"{self.elapsed_seconds / 60:.0f} 分钟"
        )


def sync_until_stable(
    engine,
    symbols: list[str],
    *,
    expected_latest_date: str | None = None,
    max_seconds: float = 10800.0,
    round_interval: float = 300.0,
    target_coverage: float = 0.9,
    min_coverage: float = 0.5,
    log=None,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> SyncOutcome:
    """反复补拉，直到当日数据拉全 / 覆盖率收敛达标 / 超时。

    Args:
        engine: 数据引擎，需实现 ``sync_daily`` / ``repair_latest_gaps`` /
            ``get_latest_daily_coverage_for_symbols``。
        symbols: 本次要覆盖的标的列表。
        expected_latest_date: 预期最新交易日（``YYYY-MM-DD``）。A 股 / ETF 传入
            ``date.today()``，用于识别「整体没拉到当日数据」的失败；美股因免费档为
            历史数据、预期日难以精确推断，传 ``None`` 时仅按覆盖率收敛判定。
        max_seconds: 最长坚持时长（秒）。
        round_interval: 相邻两轮补拉的间隔（秒）。
        target_coverage: 覆盖率达此比例即视为拉取完成（快速成功路径）。
        min_coverage: 覆盖率收敛 / 超时后仍可接受的最低下限。
        log: 日志器（缺省用模块 logger），便于各入口传入自身 logger。
        sleep / monotonic: 便于测试注入的时间函数。

    Returns:
        SyncOutcome: 是否成功、覆盖统计、轮次与耗时、结束原因。
    """
    log = log or logger
    total = len(symbols)
    if total == 0:
        return SyncOutcome(True, 0, 0, None, 0, 0.0, "no_symbols")

    start = monotonic()

    # ── 第 1 轮：完整增量同步 ──
    engine.sync_daily(symbols)
    rounds = 1
    prev_covered = -1

    while True:
        coverage = engine.get_latest_daily_coverage_for_symbols(symbols)
        covered = int(coverage.get("latest_symbols") or 0)
        latest_date = coverage.get("latest_date")
        elapsed = monotonic() - start
        ratio = covered / total

        # 是否已拉到「预期最新交易日」：整体失败时本地最新日仍停在昨日，
        # latest_date < expected → 未新鲜，必须继续完整补拉。
        fresh = expected_latest_date is None or (
            latest_date is not None and str(latest_date) >= str(expected_latest_date)
        )

        # 1) 快速成功：已到最新日且覆盖充分。
        if fresh and (covered >= total or ratio >= target_coverage):
            log.info(
                f"数据已拉全：{latest_date} 覆盖 {covered}/{total}"
                f"（{ratio:.0%}），共 {rounds} 轮、约 {elapsed / 60:.1f} 分钟"
            )
            return SyncOutcome(True, covered, total, latest_date, rounds, elapsed, "covered")

        # 2) 收敛成功：覆盖率不再提升且已达下限（正常也有少量标的永不覆盖最新日）。
        if fresh and covered <= prev_covered and ratio >= min_coverage:
            log.info(
                f"覆盖率已收敛：{latest_date} 覆盖 {covered}/{total}"
                f"（{ratio:.0%}），视为拉全，共 {rounds} 轮、约 {elapsed / 60:.1f} 分钟"
            )
            return SyncOutcome(True, covered, total, latest_date, rounds, elapsed, "converged")

        # 3) 超时：达标则成功收尾，否则判失败交由上层告警。
        if elapsed >= max_seconds:
            success = fresh and ratio >= min_coverage
            level = log.info if success else log.warning
            level(
                f"已坚持约 {elapsed / 60:.0f} 分钟（{rounds} 轮）："
                f"{latest_date} 覆盖 {covered}/{total}（{ratio:.0%}），"
                f"{'达标收尾' if success else '仍未拉全，将发送告警卡片'}"
            )
            return SyncOutcome(success, covered, total, latest_date, rounds, elapsed, "timeout")

        # ── 继续补拉 ──
        prev_covered = covered
        wait = min(round_interval, max_seconds - elapsed)
        log.info(
            f"当前 {latest_date} 覆盖 {covered}/{total}（{ratio:.0%}），"
            f"{wait:.0f}s 后进行第 {rounds + 1} 轮补拉（已用时 {elapsed / 60:.1f} 分钟）"
        )
        if wait > 0:
            sleep(wait)

        if fresh:
            engine.repair_latest_gaps(symbols)
        else:
            engine.sync_daily(symbols)
        rounds += 1
