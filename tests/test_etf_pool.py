"""ETF 四梯队报告测试。"""

import tempfile
from pathlib import Path

import pandas as pd

from matrix_etf.core.config import Settings
from matrix_etf.data.engine import DataEngine
from matrix_etf.strategy.etf.etf_pool import EtfPoolReport


def make_engine(tmp_dir: str) -> DataEngine:
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2020-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return DataEngine(settings)


def test_report_handles_empty_metrics() -> None:
    """无指标数据时报告应给出占位文本而非报错。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine(tmp_dir)
        content = EtfPoolReport(engine).build()
    assert "Matrix ETF 四梯队报告" in content
    assert "暂无指标数据" in content


def test_report_writes_file_with_metrics() -> None:
    """有指标数据时应生成分梯队报告文件。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine(tmp_dir)
        # 直接写入几行日线并刷新指标
        rows = []
        for sym, base in [("510300.SH", 10.0), ("159915.SZ", 20.0)]:
            for i in range(60):
                c = base + i * 0.02
                rows.append({
                    "symbol": sym,
                    "date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                    "open": c, "high": c + 0.1, "low": c - 0.1,
                    "close": c, "volume": 1_000_000.0, "amount": 2e8,
                })
        engine._upsert_daily(pd.DataFrame(rows))
        engine.refresh_metrics(["510300.SH", "159915.SZ"])

        out_dir = str(Path(tmp_dir) / "reports")
        path = EtfPoolReport(engine).write_report(limit=10, out_dir=out_dir)

        assert Path(path).exists()
        text = Path(path).read_text(encoding="utf-8")
        assert "动量领先" in text
        assert "趋势健康" in text
