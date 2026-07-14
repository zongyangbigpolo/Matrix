"""数据引擎属性测试。"""

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from matrix_etf.core.config import Settings
from matrix_etf.data.engine import DataEngine


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return DataEngine(settings), settings


def _daily_row(symbol: str, d: str, close: float) -> dict:
    return {
        "symbol": symbol, "date": d,
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 1000.0, "amount": close * 1000.0,
    }


@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2020, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，记录数应保持为 1。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        full_symbol = f"{symbol}.SH"
        df = pd.DataFrame([_daily_row(full_symbol, str(trade_date), 10.0)])
        engine._upsert_daily(df)
        engine._upsert_daily(df)
        with sqlite3.connect(engine.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM etf_daily WHERE symbol=? AND date=?",
                (full_symbol, str(trade_date)),
            ).fetchone()[0]
        assert count == 1


def test_upsert_daily_does_not_delete_other_symbols_same_date() -> None:
    """按 (symbol, date) upsert 时，不应删除同一交易日的其他 ETF。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        engine._upsert_daily(pd.DataFrame([
            _daily_row("510300.SH", "2026-05-07", 10.0),
            _daily_row("159915.SZ", "2026-05-07", 20.0),
        ]))
        engine._upsert_daily(pd.DataFrame([_daily_row("510300.SH", "2026-05-07", 12.5)]))

        with sqlite3.connect(engine.db_path) as conn:
            rows_count = conn.execute(
                "SELECT COUNT(*) FROM etf_daily WHERE date = '2026-05-07'"
            ).fetchone()[0]
            updated = conn.execute(
                "SELECT close FROM etf_daily WHERE symbol='510300.SH' AND date='2026-05-07'"
            ).fetchone()[0]
            untouched = conn.execute(
                "SELECT close FROM etf_daily WHERE symbol='159915.SZ' AND date='2026-05-07'"
            ).fetchone()[0]

    assert rows_count == 2
    assert updated == pytest.approx(12.5)
    assert untouched == pytest.approx(20.0)


def test_latest_daily_coverage_counts_latest_date_symbols() -> None:
    """最新交易日覆盖统计应显示最新日期及该日 ETF 覆盖数。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        engine._upsert_daily(pd.DataFrame([
            _daily_row("510300.SH", "2026-05-06", 10.0),
            _daily_row("159915.SZ", "2026-05-06", 20.0),
            _daily_row("510300.SH", "2026-05-07", 12.5),
        ]))
        coverage = engine.get_latest_daily_coverage()

    assert coverage == {
        "latest_date": "2026-05-07",
        "latest_symbols": 1,
        "total_symbols": 2,
    }


def test_tables_are_initialized() -> None:
    """核心表应随 DataEngine 初始化自动创建。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        with sqlite3.connect(engine.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
    assert {"etf_daily", "etf_basic", "etf_metrics"} <= tables


def test_refresh_metrics_computes_row() -> None:
    """refresh_metrics 应为足够长的日线序列写入一行 etf_metrics。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        rows = [
            _daily_row("510300.SH", f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", 10.0 + i * 0.01)
            for i in range(60)
        ]
        engine._upsert_daily(pd.DataFrame(rows))
        n = engine.refresh_metrics(["510300.SH"])

        assert n == 1
        frame = engine.get_metrics_frame()
        assert len(frame) == 1
        assert frame.iloc[0]["symbol"] == "510300.SH"
        assert frame.iloc[0]["sample_days"] == 60
