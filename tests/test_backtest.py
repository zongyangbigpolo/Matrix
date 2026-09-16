import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import tracemalloc
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from matrix_etf.analytics.backtest import (
    MARKET_PORTFOLIO,
    BacktestConfig,
    backtest_market,
    execution_price_store,
    format_backtest_report,
    simulate_portfolio,
)
from matrix_etf.analytics.db import AnalyticsEngine
from matrix_etf.analytics.history import (
    historical_reads,
    history_window,
    snapshot_window,
    trim_snapshot_history,
)
from matrix_etf.analytics.replay import _shrink_to, capped_engine_db
from matrix_etf.analytics.report import build_perf_line
from matrix_etf.analytics.signals import SignalStore
from matrix_etf.core.config import Settings
from matrix_etf.strategy.hold_days import SUGGESTED_HOLD_DAYS

FREE = BacktestConfig(initial_capital=100, max_positions=1, commission_bps=0, slippage_bps=0)
DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


class Engine:
    def __init__(self, path):
        self.db_path = str(path)

    def get_local_symbols(self):
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM etf_daily")]

    def get_ohlcv(self, symbol):
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(
                "SELECT * FROM etf_daily WHERE symbol=? ORDER BY date", conn, params=(symbol,)
            )


def market_db(path, closes=None):
    closes = closes or [100] * 30
    days = pd.bdate_range("2026-01-01", periods=len(closes)).strftime("%Y-%m-%d").tolist()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE etf_daily (symbol TEXT, date TEXT, open REAL, high REAL, "
            "low REAL, close REAL, volume REAL, amount REAL)"
        )
        conn.executemany(
            "INSERT INTO etf_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [("AAA", day, close, close + 1, close - 1, close, 100000, 10000000)
             for day, close in zip(days, closes)],
        )
    return Engine(path), days


def analytics_db(path):
    return AnalyticsEngine(Settings(
        analytics_db_path=str(path), feishu_webhook_url="", _env_file=None
    ))


class Picks:
    suggested_hold_days = 2

    def __init__(self, engine):
        self.engine = engine

    def run(self):
        return ["AAA"]


class Empty(Picks):
    def run(self):
        return []


class Broken(Picks):
    def run(self):
        raise RuntimeError("broken strategy")


def test_known_funded_equity_curve_and_annualization():
    prices = {(DAYS[1], "A"): (100, 110), (DAYS[2], "A"): (110, 90)}
    result = simulate_portfolio(DAYS, prices, {DAYS[0]: [("A", 2)]}, FREE)
    assert [p["equity"] for p in result["equity"]] == pytest.approx([100, 110, 90, 90])
    assert result["total_return"] == pytest.approx(-0.1)
    assert result["ann_return"] == pytest.approx(0.9 ** (252 / 3) - 1)
    assert result["max_drawdown"] == pytest.approx(90 / 110 - 1)
    assert result["closed_trades"] == 1
    assert result["trades"][0]["entry_date"] == DAYS[1]
    assert result["trades"][0]["exit_date"] == DAYS[2]


def test_both_sides_costs_and_slippage():
    config = replace(FREE, commission_bps=100, slippage_bps=100)
    result = simulate_portfolio(
        DAYS[:2], {(DAYS[1], "A"): (100, 100)}, {DAYS[0]: [("A", 1)]}, config
    )
    expected = 100 * (0.99 * 0.99) / (1.01 * 1.01)
    assert result["final_equity"] == pytest.approx(expected)
    assert result["costs"] == pytest.approx(100 - expected)
    assert result["trades"][0]["entry_price"] == 101
    assert result["trades"][0]["exit_price"] == 99
    assert result["trades"][0]["pnl"] == pytest.approx(expected - 100)


def test_overlap_duplicate_and_no_same_day_close_reinvestment():
    prices = {(day, symbol): (100, 100) for day in DAYS for symbol in ("A", "B")}
    signals = {
        DAYS[0]: [("A", 2)],
        DAYS[1]: [("A", 2), ("B", 1)],
        DAYS[2]: [("B", 1)],
    }
    result = simulate_portfolio(DAYS, prices, signals, FREE)
    assert result["trade_count"] == 2
    assert result["skipped"]["already_held"] == 1
    assert result["skipped"]["capacity"] == 1
    assert result["trades"][1]["entry_date"] == DAYS[3]
    assert all(p["cash"] >= 0 for p in result["equity"])
    assert result["total_return"] == pytest.approx(0)


def test_fixed_slot_sizing_and_cash_conservation():
    result = simulate_portfolio(
        DAYS[:2], {(DAYS[1], "A"): (100, 200)},
        {DAYS[0]: [("A", 10)]}, replace(FREE, max_positions=10),
    )
    assert result["final_equity"] == pytest.approx(110)
    assert result["equity"][-1]["cash"] == pytest.approx(90)
    assert result["total_return"] == pytest.approx(0.1)  # not the +100% signal return


def test_missing_entry_cancelled_not_forward_filled_and_end_signal_unfilled():
    result = simulate_portfolio(
        DAYS, {(DAYS[2], "A"): (50, 100)},
        {DAYS[0]: [("A", 1)], DAYS[-1]: [("A", 1)]}, FREE,
    )
    assert result["trade_count"] == 0
    assert result["status"] == "partial"
    assert result["skipped"]["missing_open"] == 1
    assert result["skipped"]["end_of_sample"] == 1
    assert result["total_return"] == 0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), "corrupt"])
def test_invalid_prices_are_missing_not_executable(bad):
    result = simulate_portfolio(
        DAYS[:2], {(DAYS[1], "A"): (bad, 100)}, {DAYS[0]: [("A", 1)]}, FREE
    )
    assert result["trade_count"] == 0
    assert result["skipped"]["missing_open"] == 1
    assert result["status"] == "partial"


def test_missing_exit_deferred_and_stale_end_mark_disclosed():
    prices = {(DAYS[1], "A"): (100, None), (DAYS[2], "A"): (None, 90)}
    result = simulate_portfolio(DAYS, prices, {DAYS[0]: [("A", 1)]}, FREE)
    assert result["trades"][0]["exit_date"] == DAYS[2]
    assert result["status"] == "partial"
    held = simulate_portfolio(DAYS, prices, {DAYS[0]: [("A", 10)]}, FREE)
    assert held["open_positions"] == 1
    assert held["final_equity"] == 90
    assert any("Stale terminal" in w for w in held["warnings"])
    assert held["trades"][0]["exit_price"] is None


def test_snapshot_includes_committed_wal_clears_metrics_and_restores_on_error(tmp_path):
    engine, dates = market_db(tmp_path / "source.db")
    original = engine.db_path
    conn = sqlite3.connect(original)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE etf_metrics (symbol TEXT, close REAL)")
        conn.execute("INSERT INTO etf_metrics VALUES ('AAA', 9999)")
        conn.execute("UPDATE etf_daily SET close=123 WHERE date=?", (dates[-1],))
        conn.commit()
        with pytest.raises(RuntimeError, match="restore"):
            with capped_engine_db(engine, "etf_daily") as snapshot:
                assert engine.get_ohlcv("AAA").iloc[-1]["close"] == 123
                _shrink_to(snapshot, "etf_daily", dates[-2])
                with sqlite3.connect(snapshot) as capped:
                    assert capped.execute("SELECT COUNT(*) FROM etf_metrics").fetchone()[0] == 0
                raise RuntimeError("restore")
        assert engine.db_path == original
        assert not Path(snapshot).exists()
        assert conn.execute("SELECT close FROM etf_metrics").fetchone()[0] == 9999
        assert engine.get_ohlcv("AAA").iloc[-1]["close"] == 123
    finally:
        conn.close()


def test_no_future_data_in_strategy_ranking_and_execution(tmp_path, monkeypatch):
    from matrix_etf.strategy import ranking

    engine, dates = market_db(tmp_path / "source.db", [100] * 28 + [200, 50])
    analytics = analytics_db(tmp_path / "analytics.db")
    seen = []
    real_select = ranking.select_recommendations

    def select(capped, candidates, limit=10):
        with sqlite3.connect(capped.db_path) as conn:
            seen.append(conn.execute("SELECT MAX(date) FROM etf_daily").fetchone()[0])
            assert conn.execute(
                "SELECT name FROM sqlite_master WHERE name='prices'"
            ).fetchone() is None
        return real_select(capped, candidates, limit)

    class Below150(Picks):
        def run(self):
            return ["AAA"] if self.engine.get_ohlcv("AAA").iloc[-1]["close"] < 150 else []

    monkeypatch.setattr(ranking, "select_recommendations", select)
    run = backtest_market(engine, [Below150(engine)], "ETF", analytics, 3, FREE)
    result = run["results"]["Below150"]
    assert seen == list(reversed(dates[-3:]))
    assert result["signals"][dates[-3]] == [("AAA", 2)]
    assert result["signals"][dates[-2]] == []
    assert result["trades"][0]["entry_date"] == dates[-2]
    assert result["trades"][0]["entry_price"] == 200
    assert result["total_return"] == pytest.approx(-0.75)
    assert engine.get_ohlcv("AAA").shape[0] == 30


def test_disk_execution_prices_match_dictionary_and_cleanup_on_error(tmp_path):
    engine, dates = market_db(tmp_path / "market.db", [100] * 28 + [200, 50])
    prices = {(day, "AAA"): (price, price)
              for day, price in zip(dates[-3:], [100, 200, 50])}
    signals = {dates[-3]: [("AAA", 2)]}
    expected = simulate_portfolio(dates[-3:], prices, signals, FREE)
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("CREATE INDEX symbol_date ON etf_daily(symbol,date)")
    with pytest.raises(RuntimeError, match="cleanup"):
        with execution_price_store(engine.db_path, "etf_daily", dates[-3], ["AAA"]) as store:
            assert simulate_portfolio(dates[-3:], store, signals, FREE) == expected
            assert store.get(("missing", "AAA"), (None, None)) == (None, None)
            raise RuntimeError("cleanup")
    assert not list(tmp_path.glob("backtest_prices_*"))


def test_12000_symbols_60_days_execution_store_has_bounded_python_memory(tmp_path):
    path = tmp_path / "large.db"
    dates = pd.bdate_range("2026-01-01", periods=60).strftime("%Y-%m-%d").tolist()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stock_daily (date TEXT, symbol TEXT, open REAL, close REAL)")
        conn.executemany(
            "INSERT INTO stock_daily VALUES (?, ?, 100, 101)",
            ((day, f"S{index:05}") for day in dates for index in range(12000)),
        )
    tracemalloc.start()
    try:
        with execution_price_store(path, "stock_daily", dates[0]) as store:
            assert store.connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 720000
            assert store.has_usable_prices()
            for index in range(12000):
                assert store.get((dates[-1], f"S{index:05}")) == (100, 101)
            assert len(store.cache) == 2048
            _, peak = tracemalloc.get_traced_memory()
            assert peak < 4 * 1024 * 1024
    finally:
        tracemalloc.stop()
    assert not list(tmp_path.glob("backtest_prices_*"))


def test_idempotent_persistence_isolated_live_and_card_without_forward_samples(tmp_path):
    engine, dates = market_db(tmp_path / "market.db")
    analytics = analytics_db(tmp_path / "analytics.db")
    SignalStore(analytics).record(dates[-1], "ETF", "Picks", ["LIVE"], 2)
    runs = [
        backtest_market(engine, [Picks(engine), Empty(engine)], "ETF", analytics, 4, FREE)
        for _ in range(2)
    ]
    assert runs[0]["run_id"] == runs[1]["run_id"]
    with analytics.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backtest_run").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM backtest_result").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM backtest_equity").fetchone()[0] == 12
        assert conn.execute("SELECT COUNT(*) FROM strategy_signal").fetchone()[0] == 1
        assert conn.execute("SELECT symbol FROM strategy_signal").fetchone()[0] == "LIVE"
        assert conn.execute("SELECT COUNT(*) FROM signal_evaluation").fetchone()[0] == 0
        saved = json.loads(conn.execute(
            "SELECT result_json FROM backtest_result WHERE strategy='Empty'"
        ).fetchone()[0])
        assert saved["status"] == "no_signals"
        assert saved["total_return"] == 0
    assert "模拟，非实盘" in build_perf_line(analytics, "ETF", "Empty")
    assert "总收益 +0.0%" in build_perf_line(analytics, "ETF", "Empty")
    filtered = format_backtest_report(runs[0]["results"], "ETF", strategy_filter="empty")
    assert "| Empty |" in filtered and "| Picks |" not in filtered
    assert MARKET_PORTFOLIO in runs[0]["report"]


def test_broken_strategy_invalidates_consensus_explicitly_and_no_data_is_not_zero(tmp_path):
    engine, _ = market_db(tmp_path / "market.db")
    analytics = analytics_db(tmp_path / "analytics.db")
    run = backtest_market(engine, [Picks(engine), Broken(engine)], "ETF", analytics, 3, FREE)
    assert all(result["status"] == "error" for result in run["results"].values())
    assert "broken strategy" in run["report"]
    assert run["results"]["Picks"]["total_return"] is None
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("DELETE FROM etf_daily")
    run = backtest_market(engine, [Empty(engine)], "ETF", analytics, 3, FREE)
    assert run["results"]["Empty"]["status"] == "no_data"
    assert run["results"]["Empty"]["total_return"] is None
    assert "N/A" in run["report"]


def test_internally_swallowed_strategy_errors_are_not_zero_signal_success(tmp_path):
    class SwallowsFailure(Picks):
        def run(self):
            logging.getLogger(__name__).warning("calculation failed: bad price column")
            return []

    engine, _ = market_db(tmp_path / "market.db")
    analytics = analytics_db(tmp_path / "analytics.db")
    run = backtest_market(engine, [SwallowsFailure(engine)], "ETF", analytics, 2, FREE)
    assert run["results"]["SwallowsFailure"]["status"] == "error"
    assert "bad price column" in run["report"]


def test_cli_filter_still_ranks_and_persists_every_market_strategy(tmp_path, monkeypatch, capsys):
    import analytics_main
    from matrix_etf.strategy import ranking

    class Other(Picks):
        pass

    settings = Settings(
        feishu_webhook_url="", analytics_db_path=str(tmp_path / "analytics.db"),
        _env_file=None,
    )
    engine, _ = market_db(tmp_path / "market.db")
    strategies = [Picks(engine), Other(engine)]
    monkeypatch.setattr(
        analytics_main, "_build_market_engines", lambda settings, offline=False: {"ETF": engine}
    )
    monkeypatch.setattr(
        analytics_main, "_build_market_strategies", lambda engines, settings: {"ETF": strategies}
    )
    seen = []
    original = ranking.select_recommendations

    def select(capped, candidates, limit=10):
        seen.append(candidates)
        return original(capped, candidates, limit)

    monkeypatch.setattr(ranking, "select_recommendations", select)
    analytics_main._run_backtest(
        settings, logging.getLogger(__name__), 3, "ETF", "other", FREE
    )
    assert len(seen) == 3
    assert all(candidates == [["AAA"], ["AAA"]] for candidates in seen)
    output = capsys.readouterr().out
    assert "| Other |" in output and "| Picks |" not in output
    reports = list((tmp_path / "backtests").glob("*.md"))
    assert len(reports) == 1
    assert "| Picks |" in reports[0].read_text()
    assert "| Other |" in reports[0].read_text()
    with pytest.raises(ValueError, match="No strategy matches"):
        analytics_main._run_backtest(
            settings, logging.getLogger(__name__), 3, "ETF", "typo", FREE
        )


def test_offline_cli_no_data_persists_all_17_errors_without_source_writes(tmp_path):
    from analytics_main import _run_backtest

    settings = Settings(
        feishu_webhook_url="", db_path=str(tmp_path / "missing-etf.db"),
        stock_db_path=str(tmp_path / "missing-cn.db"), us_db_path=str(tmp_path / "missing-us.db"),
        analytics_db_path=str(tmp_path / "analytics.db"), _env_file=None,
    )
    with pytest.raises(RuntimeError, match="Backtest failed for ETF, CN, US"):
        _run_backtest(settings, logging.getLogger(__name__), 3, config=FREE)
    assert not Path(settings.db_path).exists()
    assert not Path(settings.stock_db_path).exists()
    assert not Path(settings.us_db_path).exists()
    with AnalyticsEngine(settings).connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM backtest_result WHERE status='no_data' AND total_return IS NULL"
        ).fetchone()[0] == 20
    assert len(list((tmp_path / "backtests").glob("*.md"))) == 3


def test_cli_backtest_needs_no_webhook_credentials(monkeypatch):
    import analytics_main

    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["analytics_main.py", "--backtest"])
    captured = []
    monkeypatch.setattr(
        analytics_main, "_run_backtest",
        lambda settings, logger, days, **kwargs: captured.append((settings, days)),
    )
    analytics_main.main()
    assert captured[0][0].feishu_webhook_url == ""
    assert captured[0][1] == 60


def test_real_offline_cli_writes_report_without_modifying_prices(tmp_path):
    engine, _ = market_db(tmp_path / "market.db")
    before = Path(engine.db_path).read_bytes()
    env = {
        **os.environ, "DB_PATH": engine.db_path,
        "ANALYTICS_DB_PATH": str(tmp_path / "analytics.db"), "FEISHU_WEBHOOK_URL": "",
    }
    completed = subprocess.run(
        [sys.executable, "analytics_main.py", "--backtest", "--market", "ETF", "--days", "3"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SIMULATION" in completed.stdout
    assert "总收益" in completed.stdout
    assert Path(engine.db_path).read_bytes() == before
    with sqlite3.connect(tmp_path / "analytics.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM backtest_result").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM strategy_signal").fetchone()[0] == 0
    assert len(list((tmp_path / "backtests").glob("ETF-*.md"))) == 1
    report = subprocess.run(
        [sys.executable, "analytics_main.py", "--report"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,
        text=True, timeout=30,
    )
    assert report.returncode == 0, report.stdout + report.stderr
    assert "SIMULATION" in report.stdout
    assert "| RpsMomentumStrategy |" in report.stdout
    assert "| __market_portfolio__ |" in report.stdout


def test_service_sigterm_cleans_working_databases_and_restores_handler(tmp_path):
    from analytics_main import _backtest_termination

    class Interrupted(Picks):
        def run(self):
            signal.raise_signal(signal.SIGTERM)

    engine, _ = market_db(tmp_path / "market.db")
    original_path = engine.db_path
    original_handler = signal.getsignal(signal.SIGTERM)
    analytics = analytics_db(tmp_path / "analytics.db")
    with pytest.raises(SystemExit) as stopped:
        with _backtest_termination():
            backtest_market(engine, [Interrupted(engine)], "ETF", analytics, 3, FREE)
    assert stopped.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == original_handler
    assert engine.db_path == original_path
    assert not list(tmp_path.glob("replay_*"))
    assert not list(tmp_path.glob("backtest_prices_*"))


def test_all_17_production_strategies_registered_and_zero_signals_reported(tmp_path):
    from analytics_main import _build_market_engines, _build_market_strategies

    settings = Settings(
        feishu_webhook_url="",
        db_path=str(tmp_path / "etf.db"),
        stock_db_path=str(tmp_path / "cn.db"),
        us_db_path=str(tmp_path / "us.db"),
        analytics_db_path=str(tmp_path / "analytics.db"),
        _env_file=None,
    )
    engines = _build_market_engines(settings)
    strategies = _build_market_strategies(engines, settings)
    names = [type(s).__name__ for group in strategies.values() for s in group]
    assert len(names) == 17
    assert set(names) == set(SUGGESTED_HOLD_DAYS)
    analytics = AnalyticsEngine(settings)
    for market, engine in engines.items():
        table = "etf_daily" if market == "ETF" else "stock_daily"
        with sqlite3.connect(engine.db_path) as conn:
            # One valid bar: no production strategy has completed its warm-up.
            conn.execute(
                f"INSERT INTO {table} (symbol,date,open,high,low,close,volume) "
                "VALUES ('AAA', '2026-01-05', 100, 101, 99, 100, 100000)"
            )
        run = backtest_market(engine, strategies[market], market, analytics, 1, FREE)
        for strategy in strategies[market]:
            result = run["results"][type(strategy).__name__]
            assert result["status"] == "no_signals"
            assert result["total_return"] == 0
            assert result["ann_return"] is None
    with analytics.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backtest_result").fetchone()[0] == 20


@pytest.mark.parametrize("market,expected_window", [("ETF", 201), ("CN", 121), ("US", 201)])
def test_registered_bounded_history_matches_full_strategy_picks(tmp_path, market, expected_window):
    from analytics_main import _build_market_engines, _build_market_strategies

    settings = Settings(
        feishu_webhook_url="", db_path=str(tmp_path / "etf.db"),
        stock_db_path=str(tmp_path / "cn.db"), us_db_path=str(tmp_path / "us.db"),
        _env_file=None,
    )
    engines = _build_market_engines(settings)
    strategies = _build_market_strategies(engines, settings)[market]
    engine = engines[market]
    table = "etf_daily" if market == "ETF" else "stock_daily"
    dates = pd.bdate_range("2024-01-01", periods=450).strftime("%Y-%m-%d").tolist()
    random = np.random.default_rng(87)
    with sqlite3.connect(engine.db_path) as conn:
        for index in range(8):
            closes = 100 * np.exp(np.cumsum(random.normal(0.002, 0.018, 450)))
            volumes = random.uniform(1_000_000, 5_000_000, 450)
            conn.executemany(
                f"INSERT INTO {table} (symbol,date,open,high,low,close,volume,amount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(f"S{index}", day, close * 0.99, close * 1.02, close * 0.98,
                  close, volume, close * volume)
                 for day, close, volume in zip(dates, closes, volumes)],
            )
    assert history_window(strategies)[0] == expected_window
    expected_by_date = {}
    frames_by_date = {}
    for cutoff in (dates[-1], dates[-76]):
        with capped_engine_db(engine, table) as snapshot:
            _shrink_to(snapshot, table, cutoff)
            expected_by_date[cutoff] = [strategy.run() for strategy in strategies]
            frames_by_date[cutoff] = engine.get_ohlcv("S0")
    with capped_engine_db(engine, table) as snapshot:
        trim_snapshot_history(snapshot, table, strategies, 76, engine.get_local_symbols())
        assert len(engine.get_ohlcv("S0")) == snapshot_window(strategies, 76)[0]
        for cutoff in (dates[-1], dates[-76]):
            _shrink_to(snapshot, table, cutoff)
            with historical_reads(engine, strategies, market):
                actual = [strategy.run() for strategy in strategies]
                assert actual == expected_by_date[cutoff]
                bounded = engine.get_ohlcv("S0")
                assert len(bounded) == expected_window
                pd.testing.assert_frame_equal(
                    bounded, frames_by_date[cutoff].tail(expected_window).reset_index(drop=True)
                )
                bounded.loc[0, "close"] = -9999
                assert engine.get_ohlcv("S0").iloc[0]["close"] != -9999
    assert len(engine.get_ohlcv("S0")) == 450


def test_history_padding_preserves_mega7_valid_windows_and_unknowns_fall_back(tmp_path):
    from matrix_etf.strategy.etf.mega7_rotation import RiskAdjustedMomentumStrategy

    engine, dates = market_db(tmp_path / "market.db", [100] * 500)
    settings = Settings(feishu_webhook_url="", _env_file=None)
    strategies = [RiskAdjustedMomentumStrategy(engine, settings)]
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("UPDATE etf_daily SET amount=NULL WHERE date>=?", (dates[-100],))
    with capped_engine_db(engine, "etf_daily") as snapshot:
        trim_snapshot_history(snapshot, "etf_daily", strategies, 20, ["AAA"])
        assert len(engine.get_ohlcv("AAA")) == 500
    with historical_reads(engine, strategies, "ETF"):
        frame = engine.get_ohlcv("AAA")
        assert len(frame) > 201
        assert frame["amount"].notna().sum() >= 201
    assert len(engine.get_ohlcv("AAA")) == 500
    with historical_reads(engine, [Picks(engine)], "ETF"):
        assert len(engine.get_ohlcv("AAA")) == 500
    with capped_engine_db(engine, "etf_daily") as snapshot:
        trim_snapshot_history(snapshot, "etf_daily", [Picks(engine)], 20, ["AAA"])
        assert len(engine.get_ohlcv("AAA")) == 500


def test_snapshot_retention_respects_all_configured_lookbacks():
    from analytics_main import _build_market_engines, _build_market_strategies

    settings = Settings(
        feishu_webhook_url="", rps_period=500, breakout_period=450,
        stock_rps_period=600, us_rps_period=500, us_breakout_period=450,
        mega7_momentum_periods="21,63,700", mega7_volatility_days=800,
        mega7_downside_lookback_days=900, mega7_volume_long_days=1100,
        mega7_volume_short_days=1200, _env_file=None,
    )
    engines = _build_market_engines(settings, offline=True)
    strategies = _build_market_strategies(engines, settings)
    assert history_window(strategies["CN"])[0] >= 601
    assert history_window(strategies["US"])[0] >= 501
    assert snapshot_window(strategies["ETF"], 20)[0] == 1220
    assert snapshot_window(strategies["CN"], 20)[0] == int((600 + 60) * 1.6) + 1 + 20
    assert snapshot_window(strategies["US"], 20)[0] == int((500 + 60) * 1.6) + 1 + 20


def test_indexed_shrink_removes_new_listing_and_all_future_rows(tmp_path):
    engine, dates = market_db(tmp_path / "market.db")
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("CREATE INDEX symbol_date ON etf_daily(symbol,date)")
        conn.execute(
            "INSERT INTO etf_daily SELECT 'NEW',date,open,high,low,close,volume,amount "
            "FROM etf_daily WHERE date=?", (dates[-1],),
        )
        plan = conn.execute(
            "EXPLAIN QUERY PLAN DELETE FROM etf_daily WHERE symbol=? AND date>?",
            ("AAA", dates[-2]),
        ).fetchall()
        assert any("symbol_date" in row[-1] for row in plan)
    with execution_price_store(engine.db_path, "etf_daily", dates[-3]) as store:
        _shrink_to(
            engine.db_path, "etf_daily", dates[-2],
            store.symbols_between(dates[-2], dates[-1]),
        )
    assert engine.get_local_symbols() == ["AAA"]
    assert engine.get_ohlcv("AAA").iloc[-1]["date"] == dates[-2]


@pytest.mark.parametrize("kwargs", [
    {"initial_capital": -1}, {"initial_capital": float("nan")},
    {"max_positions": 0}, {"max_positions": 11},
    {"commission_bps": -1}, {"slippage_bps": 10000}, {"recommendation_limit": 11},
])
def test_invalid_capital_constraints(kwargs):
    with pytest.raises(ValueError):
        BacktestConfig(**kwargs)
