"""sync_until_stable「持续拉取直至完成」执行器测试。"""

from matrix_etf.data.sync_runner import SyncOutcome, sync_until_stable


class _FakeEngine:
    """脚本化数据引擎：按预设序列返回覆盖情况，并记录各方法调用次数。"""

    def __init__(self, coverages: list[dict]) -> None:
        self._coverages = list(coverages)
        self._idx = 0
        self.sync_daily_calls = 0
        self.repair_calls = 0

    def sync_daily(self, symbols):  # noqa: ANN001
        self.sync_daily_calls += 1

    def repair_latest_gaps(self, symbols):  # noqa: ANN001
        self.repair_calls += 1
        return []

    def get_latest_daily_coverage_for_symbols(self, symbols):  # noqa: ANN001
        if self._idx < len(self._coverages):
            cov = self._coverages[self._idx]
            self._idx += 1
        else:
            cov = self._coverages[-1]
        return cov


def test_empty_symbols_returns_success() -> None:
    """标的为空时应直接成功返回，不触碰引擎。"""
    engine = _FakeEngine([])
    outcome = sync_until_stable(engine, [], sleep=lambda s: None, monotonic=lambda: 0.0)
    assert outcome.success is True
    assert outcome.reason == "no_symbols"
    assert engine.sync_daily_calls == 0


def test_fast_path_success_on_target_coverage() -> None:
    """第 1 轮即达到目标覆盖率应立即成功，不进入等待补拉。"""
    symbols = [f"S{i}" for i in range(100)]
    engine = _FakeEngine([{"latest_symbols": 95, "latest_date": "2026-07-16"}])
    waits: list[float] = []

    outcome = sync_until_stable(
        engine,
        symbols,
        expected_latest_date="2026-07-16",
        target_coverage=0.9,
        min_coverage=0.5,
        sleep=lambda s: waits.append(s),
        monotonic=lambda: 0.0,
    )

    assert outcome.success is True
    assert outcome.reason == "covered"
    assert outcome.rounds == 1
    assert engine.sync_daily_calls == 1
    assert engine.repair_calls == 0
    assert waits == []


def test_converged_success_below_target() -> None:
    """已到最新日但覆盖率停在目标以下、不再提升时应按收敛成功。"""
    symbols = [f"S{i}" for i in range(100)]
    engine = _FakeEngine(
        [
            {"latest_symbols": 60, "latest_date": "2026-07-16"},
            {"latest_symbols": 60, "latest_date": "2026-07-16"},
        ]
    )
    waits: list[float] = []

    outcome = sync_until_stable(
        engine,
        symbols,
        expected_latest_date="2026-07-16",
        target_coverage=0.9,
        min_coverage=0.5,
        round_interval=300.0,
        sleep=lambda s: waits.append(s),
        monotonic=lambda: 0.0,
    )

    assert outcome.success is True
    assert outcome.reason == "converged"
    assert outcome.rounds == 2
    assert engine.repair_calls == 1  # fresh 时补缺口而非重跑全量
    assert waits == [300.0]


def test_not_fresh_times_out_to_failure() -> None:
    """整体拉不到当日数据（本地最新日仍停在昨日）应坚持重试并最终失败告警。"""
    symbols = [f"S{i}" for i in range(100)]
    # 覆盖率虽高，但最新日 < 预期日 → 视为未新鲜，必须完整重拉。
    engine = _FakeEngine([{"latest_symbols": 96, "latest_date": "2026-07-15"}])
    times = iter([0.0, 0.0, 5.0, 10.0])

    outcome = sync_until_stable(
        engine,
        symbols,
        expected_latest_date="2026-07-16",
        max_seconds=10.0,
        round_interval=5.0,
        min_coverage=0.5,
        sleep=lambda s: None,
        monotonic=lambda: next(times),
    )

    assert outcome.success is False
    assert outcome.reason == "timeout"
    assert engine.repair_calls == 0  # 未新鲜时只重跑全量，不走缺口补拉
    assert engine.sync_daily_calls == 3  # 初始 1 轮 + 2 轮完整重拉


def test_partial_then_reaches_target() -> None:
    """先部分覆盖、补拉后达到目标覆盖率应成功。"""
    symbols = [f"S{i}" for i in range(100)]
    engine = _FakeEngine(
        [
            {"latest_symbols": 55, "latest_date": "2026-07-16"},
            {"latest_symbols": 92, "latest_date": "2026-07-16"},
        ]
    )
    waits: list[float] = []

    outcome = sync_until_stable(
        engine,
        symbols,
        expected_latest_date="2026-07-16",
        target_coverage=0.9,
        min_coverage=0.5,
        round_interval=120.0,
        sleep=lambda s: waits.append(s),
        monotonic=lambda: 0.0,
    )

    assert outcome.success is True
    assert outcome.reason == "covered"
    assert outcome.rounds == 2
    assert engine.repair_calls == 1
    assert waits == [120.0]


def test_outcome_ratio_and_describe() -> None:
    """SyncOutcome 的 ratio 与 describe 摘要应正确。"""
    outcome = SyncOutcome(
        success=False,
        covered=40,
        total=100,
        latest_date="2026-07-15",
        rounds=5,
        elapsed_seconds=600.0,
        reason="timeout",
    )
    assert outcome.ratio == 0.4
    text = outcome.describe()
    assert "40/100" in text
    assert "2026-07-15" in text
