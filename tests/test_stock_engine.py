"""股票数据引擎属性测试。"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from matrix_etf.core.config import Settings
from matrix_etf.data.stock_engine import StockDataEngine


def make_engine_in(tmp_dir: str) -> tuple[StockDataEngine, Settings]:
    settings = Settings(
        stock_db_path=str(Path(tmp_dir) / "stock.db"),
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return StockDataEngine(settings), settings


def _daily_row(symbol: str, d: str, close: float) -> dict:
    return {
        "symbol": symbol, "date": d,
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 1000.0, "amount": close * 1000.0,
    }


def test_tables_are_initialized() -> None:
    """核心表应随 StockDataEngine 初始化自动创建。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        with sqlite3.connect(engine.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
    assert {"stock_daily", "stock_basic"} <= tables


def test_upsert_daily_does_not_delete_other_symbols_same_date() -> None:
    """按 (symbol, date) upsert 时，不应删除同一交易日的其他股票。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        engine._upsert_daily(pd.DataFrame([
            _daily_row("600519.SH", "2026-05-07", 10.0),
            _daily_row("000001.SZ", "2026-05-07", 20.0),
        ]))
        engine._upsert_daily(pd.DataFrame([_daily_row("600519.SH", "2026-05-07", 12.5)]))

        with sqlite3.connect(engine.db_path) as conn:
            rows_count = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE date = '2026-05-07'"
            ).fetchone()[0]
            updated = conn.execute(
                "SELECT close FROM stock_daily WHERE symbol='600519.SH' AND date='2026-05-07'"
            ).fetchone()[0]
            untouched = conn.execute(
                "SELECT close FROM stock_daily WHERE symbol='000001.SZ' AND date='2026-05-07'"
            ).fetchone()[0]

    assert rows_count == 2
    assert updated == pytest.approx(12.5)
    assert untouched == pytest.approx(20.0)


def test_backfill_filters_start_date() -> None:
    """backfill 应只入库 START_DATE 之后（含当日）的日 K。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = Settings(
            stock_db_path=str(Path(tmp_dir) / "stock.db"),
            start_date="2026-01-02",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = StockDataEngine(settings)
        raw = pd.DataFrame([
            {
                "trade_date": "2026-01-01", "open": 10.0, "high": 10.1,
                "low": 9.9, "close": 10.0, "volume": 1000.0, "amount": 1e8,
                "name": "贵州茅台",
            },
            {
                "trade_date": "2026-01-02", "open": 11.0, "high": 11.1,
                "low": 10.9, "close": 11.0, "volume": 1000.0, "amount": 1e8,
                "name": "贵州茅台",
            },
        ])

        with patch.object(engine, "_fetch_batch", return_value={"600519.SH": raw}):
            engine.backfill(["600519.SH"])

        df = engine.get_ohlcv("600519.SH")
        assert df["date"].tolist() == ["2026-01-02"]
        # 名称随日 K 自动入库
        assert engine.get_names(["600519.SH"]) == {"600519.SH": "贵州茅台"}


def test_sync_basic_info_upserts_requested_symbols() -> None:
    """sync_basic_info 应能为指定 symbol 写入基础信息，不依赖完整标的池。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)

        class _Instruments:
            @staticmethod
            def get(symbol: str) -> dict:
                return {
                    "symbol": symbol, "code": "600519", "exchange": "SH",
                    "name": "贵州茅台", "type": "stock",
                    "ext": {"listing_date": "2001-08-27"},
                }

        class _Client:
            instruments = _Instruments()

        with patch.object(engine, "_client", return_value=_Client()):
            assert engine.sync_basic_info(["600519.SH"]) == 1

        assert engine.get_names(["600519.SH"]) == {"600519.SH": "贵州茅台"}


def test_repair_latest_gaps_refetches_missing_symbol() -> None:
    """增量后缺最新交易日的股票应被扩大窗口补拉一次。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        engine._upsert_daily(pd.DataFrame([
            _daily_row("600519.SH", "2026-05-07", 10.0),
            _daily_row("000001.SZ", "2026-05-06", 20.0),
        ]))
        raw = pd.DataFrame([
            {
                "trade_date": "2026-05-07", "open": 21.0, "high": 21.1,
                "low": 20.9, "close": 21.0, "volume": 1000.0, "amount": 1e8,
            }
        ])

        with patch.object(engine, "_fetch_batch", return_value={"000001.SZ": raw}):
            remaining = engine.repair_latest_gaps(["600519.SH", "000001.SZ"])

        assert remaining == []
        coverage = engine.get_latest_daily_coverage_for_symbols(["600519.SH", "000001.SZ"])
        assert coverage["latest_symbols"] == 2
