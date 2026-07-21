"""绩效分析模块（analytics）测试。"""

import math
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from matrix_etf.analytics import metrics
from matrix_etf.analytics.benchmark import BenchmarkStore
from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.analytics.forward import ForwardEvaluator
from matrix_etf.analytics.integration import AnalyticsHook
from matrix_etf.analytics.report import build_perf_line, format_scorecard_line
from matrix_etf.analytics.scorecard import ScorecardBuilder
from matrix_etf.analytics.signals import SignalStore
from matrix_etf.core.config import Settings
from matrix_etf.strategy.hold_days import get_suggested_hold_days, resolve_hold_days

# ── 测试脚手架 ──


def make_settings(tmp_dir: str, **kwargs) -> Settings:
    return Settings(
        analytics_db_path=str(Path(tmp_dir) / "analytics.db"),
        feishu_webhook_url="https://example.com/hook",
        **kwargs,
    )


class FakeEngine:
    """按 symbol 返回预置 OHLCV 的最小行情引擎替身。"""

    def __init__(self, data: dict[str, pd.DataFrame]) -> None:
        self._data = data

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
        return self._data.get(symbol, pd.DataFrame(columns=cols))


def build_prices(symbol: str, start: str, closes: list[float]) -> pd.DataFrame:
    """构造连续工作日的日 K：open=前一日 close（首日=首 close），close 按给定序列。"""
    dates = pd.bdate_range(start=start, periods=len(closes)).strftime("%Y-%m-%d")
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": list(dates),
            "open": opens,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "amount": [0.0] * len(closes),
        }
    )


def seed_benchmark(engine: AnalyticsEngine, benchmark: str, start: str, closes: list[float]):
    dates = pd.bdate_range(start=start, periods=len(closes)).strftime("%Y-%m-%d")
    rows = [(benchmark, d, c) for d, c in zip(dates, closes)]
    with engine.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO benchmark_daily (benchmark, date, close) VALUES (?, ?, ?)",
            rows,
        )


# ── metrics 纯函数 ──


def test_compound_and_mean_and_winrate():
    rets = [0.1, -0.05, 0.2]
    assert metrics.compound_return(rets) == pytest.approx(1.1 * 0.95 * 1.2 - 1)
    assert metrics.mean_return(rets) == pytest.approx((0.1 - 0.05 + 0.2) / 3)
    assert metrics.win_rate(rets) == pytest.approx(2 / 3)


def test_max_drawdown_known():
    # 净值 1.1 → 0.88 → ... 最深回撤在第二笔：0.88/1.1 - 1 = -0.2
    rets = [0.1, -0.2, 0.05]
    assert metrics.max_drawdown(rets) == pytest.approx(-0.2)


def test_max_drawdown_all_positive_is_zero():
    assert metrics.max_drawdown([0.01, 0.02, 0.03]) == 0.0


def test_annualized_return_and_guard():
    assert metrics.annualized_return(0.01, 12.0) == pytest.approx(1.01**12 - 1)
    # 极端亏损保护：base<=0 → -1.0
    assert metrics.annualized_return(-1.5, 12.0) == -1.0


def test_sharpe_sortino_degenerate():
    # 少于 2 个样本或零波动 → 0.0
    assert metrics.sharpe_ratio([0.05], 12.0) == 0.0
    assert metrics.sharpe_ratio([0.05, 0.05, 0.05], 12.0) == 0.0
    assert metrics.sortino_ratio([0.02, 0.03], 12.0) >= 0.0  # 无下行波动 → 0.0
    assert metrics.sortino_ratio([0.02, 0.03], 12.0) == 0.0


def test_sharpe_matches_manual():
    import numpy as np
    rets = [0.05, -0.02, 0.03, 0.10, -0.01]
    ppy = 12.0
    arr = np.array(rets)
    expected = arr.mean() / arr.std(ddof=1) * math.sqrt(ppy)
    assert metrics.sharpe_ratio(rets, ppy) == pytest.approx(expected)


@given(
    rets=st.lists(
        st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
        min_size=1, max_size=30,
    )
)
@h_settings(max_examples=50)
def test_all_positive_returns_zero_drawdown(rets):
    assert metrics.max_drawdown(rets) == 0.0
    assert metrics.win_rate([r for r in rets if r > 0] or [0.0]) in (0.0, 1.0)


# ── SignalStore ──


def test_signal_store_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        engine = AnalyticsEngine(make_settings(tmp))
        store = SignalStore(engine)
        n1 = store.record("2026-07-20", "CN", "RpsBreakoutStrategy", ["600519.SH", "000001.SZ"], 20)
        n2 = store.record("2026-07-20", "CN", "RpsBreakoutStrategy", ["600519.SH", "000001.SZ"], 20)
        assert n1 == 2
        assert n2 == 0
        pending = store.signals_needing_entry("2026-07-21")
        assert len(pending) == 2
        assert all(p["run_date"] == "2026-07-20" for p in pending)


# ── ForwardEvaluator ──


def test_forward_backfill_entry_and_realized_return():
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(tmp, analytics_horizons="3,5")
        engine = AnalyticsEngine(settings)
        store = SignalStore(engine)

        # 10 个工作日，close 从 100 递增；run_date 选在首日，入场应为次日开盘。
        closes = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        prices = build_prices("AAA.SH", "2026-07-06", closes)
        run_date = prices["date"].iloc[0]  # 首日
        entry_date_expected = prices["date"].iloc[1]  # T+1
        entry_open_expected = prices["open"].iloc[1]  # = closes[0] = 100

        store.record(run_date, "CN", "RpsBreakoutStrategy", ["AAA.SH"], 5)
        seed_benchmark(engine, settings.benchmark_cn, "2026-07-06", [10.0] * 10)

        engines = {"CN": FakeEngine({"AAA.SH": prices})}
        bench = BenchmarkStore(engine, settings, client=object())
        evaluator = ForwardEvaluator(engine, store, bench, engines, settings)
        as_of = prices["date"].iloc[-1]
        evaluator.evaluate(as_of)

        # 入场已回填为 T+1 开盘
        rows = store.evaluable_signals()
        assert len(rows) == 1
        assert rows[0]["entry_date"] == entry_date_expected
        assert rows[0]["entry_price"] == pytest.approx(entry_open_expected)

        # horizon=5：入场位置 i=1，出场位置 6，close=112 → ret=112/100-1=0.12
        with engine.connect() as conn:
            ev = conn.execute(
                "SELECT horizon_days, ret, status, benchmark_ret, excess_ret "
                "FROM signal_evaluation ORDER BY horizon_days"
            ).fetchall()
        by_h = {r[0]: r for r in ev}
        assert by_h[5][1] == pytest.approx(0.12)
        assert by_h[5][2] == "closed"
        # 基准横盘 → benchmark_ret≈0，excess≈ret
        assert by_h[5][3] == pytest.approx(0.0, abs=1e-9)
        assert by_h[5][4] == pytest.approx(0.12)


def test_forward_open_status_when_horizon_not_reached():
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(tmp, analytics_horizons="20")
        engine = AnalyticsEngine(settings)
        store = SignalStore(engine)

        closes = [100, 101, 102, 103, 104]  # 只有 5 天，够不到 20 日
        prices = build_prices("BBB.SH", "2026-07-06", closes)
        run_date = prices["date"].iloc[0]
        store.record(run_date, "CN", "RpsBreakoutStrategy", ["BBB.SH"], 20)
        seed_benchmark(engine, settings.benchmark_cn, "2026-07-06", [10.0] * 5)

        engines = {"CN": FakeEngine({"BBB.SH": prices})}
        bench = BenchmarkStore(engine, settings, client=object())
        ForwardEvaluator(engine, store, bench, engines, settings).evaluate(prices["date"].iloc[-1])

        with engine.connect() as conn:
            row = conn.execute(
                "SELECT status FROM signal_evaluation WHERE horizon_days=20"
            ).fetchone()
        assert row[0] == "open"


# ── ScorecardBuilder ──


def _insert_closed_trade(engine, market, strategy, run_date, horizon, ret, excess, seq=0):
    with engine.connect() as conn:
        cur = conn.execute(
            """INSERT INTO strategy_signal
               (run_date, market, strategy, symbol, entry_date, entry_price,
                suggested_hold_days, webhook_key, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_date, market, strategy, f"{strategy}-{seq}", run_date, 100.0,
             horizon, "k", "now"),
        )
        sid = cur.lastrowid
        conn.execute(
            """INSERT INTO signal_evaluation
               (signal_id, horizon_days, as_of_date, exit_price, ret, benchmark_ret,
                excess_ret, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'closed')""",
            (sid, horizon, run_date, 100 * (1 + ret), ret, ret - excess, excess),
        )


def test_scorecard_aggregation_and_sample_protection():
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(tmp, analytics_min_samples=10)
        engine = AnalyticsEngine(settings)
        as_of = date.today().isoformat()
        run_date = (date.today() - timedelta(days=10)).isoformat()

        # RpsBreakoutStrategy 建议持有 20 → horizon=20；插入 12 笔正收益
        for i in range(12):
            _insert_closed_trade(
                engine, "CN", "RpsBreakoutStrategy", run_date, 20,
                ret=0.03 + 0.001 * i, excess=0.01, seq=i,
            )
        # 样本不足的策略：只有 3 笔
        for i in range(3):
            _insert_closed_trade(
                engine, "CN", "MaVolumeStrategy", run_date, 20,
                ret=0.02, excess=0.005, seq=i,
            )

        builder = ScorecardBuilder(engine, settings)
        builder.build_all(as_of)

        with engine.connect() as conn:
            rps = conn.execute(
                "SELECT sample_size, win_rate, composite_score, excess_alpha "
                "FROM strategy_scorecard WHERE strategy='RpsBreakoutStrategy' AND window_days=90"
            ).fetchone()
            mav = conn.execute(
                "SELECT sample_size, composite_score "
                "FROM strategy_scorecard WHERE strategy='MaVolumeStrategy' AND window_days=90"
            ).fetchone()

        assert rps[0] == 12
        assert rps[1] == pytest.approx(1.0)  # 全部正收益
        assert rps[2] is not None and 0 <= rps[2] <= 100  # 有综合评分
        assert rps[3] is not None  # 有超额
        # 样本不足 → 综合评分 NULL
        assert mav[0] == 3
        assert mav[1] is None


# ── report 文案 ──


def test_format_scorecard_line():
    card = {
        "window_days": 90, "sample_size": 46, "ann_return": 0.184,
        "excess_alpha": 0.061, "win_rate": 0.58, "sharpe": 1.32,
        "composite_score": 72.0,
    }
    line = format_scorecard_line(card)
    assert "年化 +18.4%" in line
    assert "超额 +6.1%" in line
    assert "胜率 58%" in line
    assert "夏普 1.32" in line
    assert "评分 72" in line
    assert "46 条" in line


def test_format_scorecard_line_none_cases():
    assert format_scorecard_line(None) is None
    assert format_scorecard_line({"sample_size": 0}) is None


def test_build_perf_line_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        engine = AnalyticsEngine(make_settings(tmp))
        assert build_perf_line(engine, "CN", "NoSuchStrategy", 90) is None


# ── hold_days ──


def test_resolve_hold_days():
    assert get_suggested_hold_days("RpsBreakoutStrategy") == 20
    assert get_suggested_hold_days("LimitUpShakeoutStrategy") == 5
    assert get_suggested_hold_days("UnknownStrategy") == 20  # 默认兜底

    class Dummy:
        suggested_hold_days = 60

    assert resolve_hold_days(Dummy()) == 60


# ── AnalyticsHook 容错 ──


class DummyStrategy:
    webhook_key = "k"
    suggested_hold_days = None

    def __init__(self, name):
        self.__class__.__name__ = name


def test_hook_disabled_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(tmp, analytics_enabled=False)
        hook = AnalyticsHook(settings, "CN")
        assert hook.record_and_perf_line(DummyStrategy("X"), ["600519.SH"]) is None


def test_hook_records_signal():
    with tempfile.TemporaryDirectory() as tmp:
        settings = make_settings(tmp)
        hook = AnalyticsHook(settings, "CN")

        class S:
            webhook_key = "stock_rps"
            suggested_hold_days = None

        # 首次落库返回 None（无历史评分卡），但信号应已入库
        line = hook.record_and_perf_line(S(), ["600519.SH"])
        assert line is None
        engine = AnalyticsEngine(settings)
        with engine.connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM strategy_signal").fetchone()[0]
        assert cnt == 1


# ── config 解析 ──


def test_settings_analytics_parsing():
    with tempfile.TemporaryDirectory() as tmp:
        s = make_settings(tmp, analytics_horizons="5, 10, x, 20, 5", analytics_windows="")
        assert s.get_analytics_horizons() == [5, 10, 20]  # 去重、跳过非法
        assert s.get_analytics_windows() == [90, 180]  # 空 → 默认
        assert abs(sum(s.get_score_weights().values()) - 1.0) < 1e-9
