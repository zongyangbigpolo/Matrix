"""ETF 四梯队综合报告：基于 etf_metrics 生成动量/趋势/防御/观察四池 Markdown。"""

from datetime import date
from pathlib import Path

import pandas as pd

from matrix_etf.core.logger import get_logger
from matrix_etf.data.engine import DataEngine

logger = get_logger(__name__)


class EtfPoolReport:
    """基于 etf_metrics 生成四梯队 ETF 报告。

    四梯队：
    - 动量领先：RPS 高 + 站上 MA200 + 流动性达标
    - 趋势健康：close > MA50 > MA200 + 回撤可控
    - 防御稳健：站上 MA200 + 低波动（ATR%）+ 高流动性
    - 观察池：接近多头排列或动量回升，暂不达标
    """

    def __init__(self, engine: DataEngine) -> None:
        self.engine = engine

    def _load(self) -> pd.DataFrame:
        df = self.engine.get_metrics_frame()
        if df.empty:
            return df
        # 横截面 RPS：120 日收益百分位
        df = df.copy()
        df["rps"] = df["ret_120d"].rank(pct=True) * 100.0
        return df

    @staticmethod
    def _fmt_pct(value) -> str:
        return "-" if pd.isna(value) else f"{value * 100:.1f}%"

    @staticmethod
    def _fmt_num(value) -> str:
        return "-" if pd.isna(value) else f"{value:.3f}"

    @staticmethod
    def _fmt_amount(value) -> str:
        if pd.isna(value):
            return "-"
        return f"{value / 1e8:.2f}亿"

    def _tier_table(self, df: pd.DataFrame, cols: list[tuple[str, str]], limit: int) -> str:
        if df.empty:
            return "_（本梯队暂无标的）_\n"
        header = "| " + " | ".join(title for _, title in cols) + " |\n"
        sep = "| " + " | ".join("---" for _ in cols) + " |\n"
        lines = [header, sep]
        for _, row in df.head(limit).iterrows():
            cells = []
            for key, _ in cols:
                if key in ("ret_120d", "ret_60d", "drawdown_60d"):
                    cells.append(self._fmt_pct(row.get(key)))
                elif key == "vol_amount_20":
                    cells.append(self._fmt_amount(row.get(key)))
                elif key in ("rps", "rsi_14", "atr_pct_14"):
                    val = row.get(key)
                    cells.append("-" if pd.isna(val) else f"{val:.1f}")
                elif key == "close":
                    cells.append(self._fmt_num(row.get(key)))
                else:
                    cells.append(str(row.get(key, "-")))
            lines.append("| " + " | ".join(cells) + " |\n")
        return "".join(lines)

    def build(self, limit: int = 30) -> str:
        """生成四梯队 Markdown 报告文本。"""
        df = self._load()
        today = date.today().strftime("%Y-%m-%d")
        if df.empty:
            return (
                f"# Matrix ETF 四梯队报告\n\n生成日期：{today}\n\n"
                "_暂无指标数据，请先运行数据同步。_\n"
            )

        liq = df["vol_amount_20"].fillna(0)
        # 流动性门槛取自全局配置
        try:
            from matrix_etf.core.config import get_settings
            threshold = get_settings().liquidity_min_amount
        except Exception:  # noqa: BLE001
            threshold = 5e7
        liquid = liq >= threshold

        momentum = df[liquid & (df["above_ma200"] == 1) & (df["rps"] >= 80)].sort_values(
            "rps", ascending=False
        )
        picked = set(momentum["symbol"])

        trend = df[
            liquid
            & (df["close"] > df["ma50"])
            & (df["ma50"] > df["ma200"])
            & (df["drawdown_60d"] >= -0.15)
            & (~df["symbol"].isin(picked))
        ].sort_values("ret_60d", ascending=False)
        picked |= set(trend["symbol"])

        defensive = df[
            liquid
            & (df["above_ma200"] == 1)
            & (df["atr_pct_14"].notna())
            & (df["atr_pct_14"] <= 2.0)
            & (~df["symbol"].isin(picked))
        ].sort_values("atr_pct_14")
        picked |= set(defensive["symbol"])

        watch = df[
            liquid
            & (df["rps"] >= 60)
            & (~df["symbol"].isin(picked))
        ].sort_values("rps", ascending=False)

        momentum_cols = [
            ("symbol", "代码"), ("name", "名称"), ("close", "收盘"),
            ("rps", "RPS"), ("ret_120d", "120日"), ("vol_amount_20", "20日额"),
        ]
        trend_cols = [
            ("symbol", "代码"), ("name", "名称"), ("close", "收盘"),
            ("ret_60d", "60日"), ("drawdown_60d", "60日回撤"), ("vol_amount_20", "20日额"),
        ]
        defensive_cols = [
            ("symbol", "代码"), ("name", "名称"), ("close", "收盘"),
            ("atr_pct_14", "ATR%"), ("ret_60d", "60日"), ("vol_amount_20", "20日额"),
        ]
        watch_cols = [
            ("symbol", "代码"), ("name", "名称"), ("close", "收盘"),
            ("rps", "RPS"), ("ret_120d", "120日"), ("rsi_14", "RSI"),
        ]

        parts = [
            "# Matrix ETF 四梯队报告\n\n",
            f"生成日期：{today}　|　样本 ETF：{len(df)}　|　"
            f"流动性门槛：{threshold / 1e8:.2f}亿\n\n",
            "> 本报告仅用于量化研究与学习，不构成投资建议。\n\n",
            f"## 一、动量领先（{len(momentum)}）\n\n",
            self._tier_table(momentum, momentum_cols, limit),
            f"\n## 二、趋势健康（{len(trend)}）\n\n",
            self._tier_table(trend, trend_cols, limit),
            f"\n## 三、防御稳健（{len(defensive)}）\n\n",
            self._tier_table(defensive, defensive_cols, limit),
            f"\n## 四、观察池（{len(watch)}）\n\n",
            self._tier_table(watch, watch_cols, limit),
        ]
        return "".join(parts)

    def write_report(self, limit: int = 30, out_dir: str = "reports") -> str:
        """生成报告并写入 reports 目录，返回文件路径。"""
        content = self.build(limit=limit)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        path = Path(out_dir) / f"etf_pool_{today}.md"
        path.write_text(content, encoding="utf-8")
        logger.info(f"ETF 四梯队报告已生成：{path}")
        return str(path)
