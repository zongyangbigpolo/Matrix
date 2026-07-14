"""数据引擎模块：负责 SQLite 行情存储与 tickflow ETF 数据同步。

数据源为 tickflow（https://github.com/tickflow-org/tickflow）。
免费服务提供历史日 K、标的信息和标的池；配置 TICKFLOW_API_KEY 后可升级完整服务。
"""

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pandas as pd

from matrix_etf.core.config import Settings
from matrix_etf.core.logger import get_logger

logger = get_logger(__name__)


_CREATE_DAILY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS etf_daily (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    amount  REAL,
    UNIQUE (symbol, date)
);
"""

_CREATE_DAILY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_etf_symbol_date ON etf_daily (symbol, date);
"""

_CREATE_BASIC_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS etf_basic (
    symbol       TEXT PRIMARY KEY,
    code         TEXT,
    exchange     TEXT,
    name         TEXT,
    type         TEXT,
    listing_date TEXT,
    total_shares REAL,
    float_shares REAL,
    updated_at   TEXT
);
"""

_CREATE_METRICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS etf_metrics (
    symbol        TEXT PRIMARY KEY,
    update_date   TEXT,
    close         REAL,
    ma20          REAL,
    ma50          REAL,
    ma200         REAL,
    ret_20d       REAL,
    ret_60d       REAL,
    ret_120d      REAL,
    vol_amount_20 REAL,
    amount_last   REAL,
    atr_pct_14    REAL,
    rsi_14        REAL,
    high_120      REAL,
    drawdown_60d  REAL,
    above_ma200   INTEGER,
    sample_days   INTEGER
);
"""

_KLINE_DB_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]

_UPSERT_DAILY_SQL = """
INSERT INTO etf_daily (symbol, date, open, high, low, close, volume, amount)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, date) DO UPDATE SET
    open   = excluded.open,
    high   = excluded.high,
    low    = excluded.low,
    close  = excluded.close,
    volume = excluded.volume,
    amount = excluded.amount;
"""

_METRICS_COLS = [
    "symbol", "update_date", "close", "ma20", "ma50", "ma200",
    "ret_20d", "ret_60d", "ret_120d", "vol_amount_20", "amount_last",
    "atr_pct_14", "rsi_14", "high_120", "drawdown_60d", "above_ma200", "sample_days",
]

_UPSERT_METRICS_SQL = f"""
INSERT INTO etf_metrics ({", ".join(_METRICS_COLS)})
VALUES ({", ".join(["?"] * len(_METRICS_COLS))})
ON CONFLICT(symbol) DO UPDATE SET
    {", ".join(f"{c} = excluded.{c}" for c in _METRICS_COLS if c != "symbol")};
"""

# 增量同步时回看的交易日窗口（覆盖节假日/停牌空档，upsert 自动去重）
_INCREMENTAL_COUNT = 30
# 全量回填单只 ETF 拉取的日 K 根数（tickflow 单次上限 10000）
_BACKFILL_COUNT = 2000
_SQLITE_IN_CHUNK_SIZE = 900


class DataEngine:
    """ETF 行情数据引擎，负责 SQLite 存储和 tickflow 数据同步。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self.universe: str = settings.etf_universe
        self.api_key: str = settings.tickflow_api_key
        self._tf = None
        self._init_db()

    # ── 数据库 ──

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_DAILY_TABLE_SQL)
            conn.execute(_CREATE_DAILY_INDEX_SQL)
            conn.execute(_CREATE_BASIC_TABLE_SQL)
            conn.execute(_CREATE_METRICS_TABLE_SQL)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    # ── tickflow 客户端 ──

    def _client(self):
        """惰性创建并缓存 tickflow 客户端。

        当 ``TICKFLOW_API_KEY`` 为空时使用免费服务，否则使用完整服务。
        """
        if self._tf is not None:
            return self._tf

        from tickflow import TickFlow

        if self.api_key:
            logger.info("使用 tickflow 完整服务（API Key 已配置）")
            self._tf = TickFlow(api_key=self.api_key)
        else:
            logger.info("使用 tickflow 免费服务")
            self._tf = TickFlow.free()
        return self._tf

    # ── 读取 ──

    def _get_last_date(self, symbol: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM etf_daily WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        """读取单只 ETF 的日线数据，按日期升序返回。"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM etf_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
        return df

    def get_local_symbols(self) -> list[str]:
        """返回本地 etf_daily 中已有数据的 ETF 代码。"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM etf_daily ORDER BY symbol"
            ).fetchall()
        return [row[0] for row in rows]

    def get_latest_daily_coverage(self) -> dict[str, int | str | None]:
        """返回最新交易日覆盖情况，用于发现增量同步是否只写入了部分 ETF。"""
        with sqlite3.connect(self.db_path) as conn:
            latest_date = conn.execute(
                "SELECT MAX(date) FROM etf_daily"
            ).fetchone()[0]
            total_symbols = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM etf_daily"
            ).fetchone()[0]
            latest_symbols = 0
            if latest_date:
                latest_symbols = conn.execute(
                    "SELECT COUNT(DISTINCT symbol) FROM etf_daily WHERE date = ?",
                    (latest_date,),
                ).fetchone()[0]

        return {
            "latest_date": latest_date,
            "latest_symbols": latest_symbols,
            "total_symbols": total_symbols,
        }

    @staticmethod
    def _chunks(values: list[str], size: int = _SQLITE_IN_CHUNK_SIZE) -> Iterable[list[str]]:
        for start in range(0, len(values), size):
            yield values[start:start + size]

    def _get_symbol_max_dates(self, symbols: list[str]) -> dict[str, str | None]:
        if not symbols:
            return {}

        result: dict[str, str | None] = {symbol: None for symbol in symbols}
        with sqlite3.connect(self.db_path) as conn:
            for chunk in self._chunks(symbols):
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"""
                    SELECT symbol, MAX(date)
                    FROM etf_daily
                    WHERE symbol IN ({placeholders})
                    GROUP BY symbol
                    """,
                    chunk,
                ).fetchall()
                for symbol, max_date in rows:
                    result[symbol] = max_date
        return result

    def get_latest_daily_coverage_for_symbols(
        self,
        symbols: list[str],
    ) -> dict[str, int | str | None]:
        """返回指定 ETF 集合的最新交易日覆盖情况。"""
        max_dates = self._get_symbol_max_dates(symbols)
        latest_date = max((d for d in max_dates.values() if d), default=None)
        latest_symbols = (
            sum(1 for d in max_dates.values() if d == latest_date)
            if latest_date is not None else 0
        )
        return {
            "latest_date": latest_date,
            "latest_symbols": latest_symbols,
            "total_symbols": len(symbols),
        }

    def get_symbols_missing_latest(self, symbols: list[str]) -> list[str]:
        """返回没有覆盖到本批最新交易日的 ETF，用于增量补拉。"""
        max_dates = self._get_symbol_max_dates(symbols)
        latest_date = max((d for d in max_dates.values() if d), default=None)
        if latest_date is None:
            return []
        return [symbol for symbol, max_date in max_dates.items() if max_date != latest_date]

    # ── 工具 ──

    @staticmethod
    def _to_float(value) -> float | None:
        converted = pd.to_numeric(value, errors="coerce")
        if pd.isna(converted):
            return None
        return float(converted)

    @staticmethod
    def _normalize_kline(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        """将 tickflow 返回的日 K DataFrame 规整为入库列。"""
        if df is None or df.empty:
            return pd.DataFrame(columns=_KLINE_DB_COLS)

        out = pd.DataFrame(
            {
                "symbol": symbol,
                "date": df["trade_date"].astype(str),
                "open": pd.to_numeric(df["open"], errors="coerce"),
                "high": pd.to_numeric(df["high"], errors="coerce"),
                "low": pd.to_numeric(df["low"], errors="coerce"),
                "close": pd.to_numeric(df["close"], errors="coerce"),
                "volume": pd.to_numeric(df["volume"], errors="coerce"),
                "amount": pd.to_numeric(df["amount"], errors="coerce"),
            }
        )
        out = out.dropna(subset=["date", "close"])
        return out[_KLINE_DB_COLS]

    @staticmethod
    def _filter_from_date(df: pd.DataFrame, from_date: str | None) -> pd.DataFrame:
        """按 YYYY-MM-DD 起始日期过滤日 K；None 或空字符串表示不过滤。"""
        if df.empty or not from_date:
            return df
        return df[df["date"] >= from_date].copy()

    def _upsert_daily(self, df: pd.DataFrame) -> int:
        """按 (symbol, date) 写入或更新日 K，避免误删同日其他 ETF。"""
        if df is None or df.empty:
            return 0

        df = df[_KLINE_DB_COLS].copy()
        rows = [tuple(row) for row in df.itertuples(index=False, name=None)]
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(_UPSERT_DAILY_SQL, rows)
            conn.commit()
        return len(rows)

    # ── 标的池 / 基础信息 ──

    def get_universe_symbols(self) -> list[str]:
        """从 tickflow 拉取 ETF 标的池的全部 symbol。"""
        tf = self._client()
        detail = tf.universes.get(self.universe)
        symbols = list(detail.get("symbols", [])) if isinstance(detail, dict) else []
        logger.info(f"标的池 {self.universe} 含 {len(symbols)} 只 ETF")
        return symbols

    def sync_basic_info(self, symbols: list[str]) -> int:
        """同步指定 ETF 的基础信息到 etf_basic。"""
        if not symbols:
            logger.info("etf_basic 同步跳过：无标的")
            return 0

        tf = self._client()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows: list[tuple] = []

        for symbol in symbols:
            try:
                ins = tf.instruments.get(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] 基础信息获取失败：{exc}")
                ins = None

            if not isinstance(ins, dict):
                rows.append((symbol, symbol.split(".")[0], None, None, "etf",
                             None, None, None, now))
                continue

            ext = ins.get("ext") or {}
            rows.append((
                symbol,
                ins.get("code") or symbol.split(".")[0],
                ins.get("exchange"),
                ins.get("name"),
                ins.get("type", "etf"),
                ext.get("listing_date"),
                self._to_float(ext.get("total_shares")),
                self._to_float(ext.get("float_shares")),
                now,
            ))

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO etf_basic
                    (symbol, code, exchange, name, type, listing_date,
                     total_shares, float_shares, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    code=excluded.code, exchange=excluded.exchange,
                    name=excluded.name, type=excluded.type,
                    listing_date=excluded.listing_date,
                    total_shares=excluded.total_shares,
                    float_shares=excluded.float_shares,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.commit()
        logger.info(f"etf_basic 同步完成，共 {len(rows)} 只 ETF")
        return len(rows)

    def sync_universe(self) -> int:
        """同步 ETF 标的池及基础信息到 etf_basic。"""
        return self.sync_basic_info(self.get_universe_symbols())

    def sync_universe_and_get_symbols(self) -> list[str]:
        """同步 ETF 标的池基础信息，并返回本次标的池 symbols。"""
        symbols = self.get_universe_symbols()
        self.sync_basic_info(symbols)
        return symbols

    def get_etf_names(self, symbols: list[str]) -> dict[str, str]:
        """从 etf_basic 返回 {symbol: name} 映射（缺失时回退为 symbol）。"""
        if not symbols:
            return {}
        placeholders = ",".join("?" * len(symbols))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT symbol, name FROM etf_basic WHERE symbol IN ({placeholders})",
                symbols,
            ).fetchall()
        mapping = {row[0]: (row[1] or row[0]) for row in rows}
        return {s: mapping.get(s, s) for s in symbols}

    # ── 日 K 同步 ──

    def _fetch_batch(self, symbols: list[str], count: int) -> dict[str, pd.DataFrame]:
        """批量拉取日 K，返回 {symbol: DataFrame}。"""
        tf = self._client()
        try:
            result = tf.klines.batch(
                symbols,
                period="1d",
                count=count,
                as_dataframe=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"批量拉取日 K 失败，回退单只拉取：{exc}")
            result = {}
            for symbol in symbols:
                try:
                    result[symbol] = tf.klines.get(
                        symbol, period="1d", count=count, as_dataframe=True
                    )
                except Exception as inner:  # noqa: BLE001
                    logger.warning(f"[{symbol}] 日 K 获取失败：{inner}")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _extract_name(df: pd.DataFrame) -> str | None:
        """从日 K DataFrame 中提取标的名称。"""
        if df is None or df.empty or "name" not in df.columns:
            return None
        series = df["name"].dropna()
        if series.empty:
            return None
        name = str(series.iloc[-1]).strip()
        return name or None

    def _upsert_basic_names(self, mapping: dict[str, str]) -> None:
        """将 {symbol: name} 轻量写入 etf_basic（仅补全名称，不覆盖已有元数据）。"""
        if not mapping:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (symbol, symbol.split(".")[0], name, "etf", now)
            for symbol, name in mapping.items()
        ]
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO etf_basic (symbol, code, name, type, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=excluded.name, updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.commit()

    def _sync_symbols(
        self,
        symbols: list[str],
        count: int,
        label: str,
        from_date: str | None = None,
    ) -> int:
        """按批拉取并 upsert 日 K，返回写入行数。"""
        if not symbols:
            logger.info(f"{label}：无标的可同步")
            return 0

        total = 0
        batch_size = 100
        for start in range(0, len(symbols), batch_size):
            chunk = symbols[start:start + batch_size]
            data = self._fetch_batch(chunk, count)
            names: dict[str, str] = {}
            for symbol in chunk:
                df = data.get(symbol)
                name = self._extract_name(df)
                if name:
                    names[symbol] = name
                normalized = self._normalize_kline(symbol, df)
                normalized = self._filter_from_date(normalized, from_date)
                total += self._upsert_daily(normalized)
            self._upsert_basic_names(names)
            logger.info(
                f"{label}：{min(start + batch_size, len(symbols))}/{len(symbols)} "
                f"已处理，累计写入 {total} 行"
            )
        return total

    def backfill(self, symbols: list[str]) -> int:
        """全量回填指定 ETF 的历史日 K。"""
        logger.info(f"开始全量回填 {len(symbols)} 只 ETF，起始日期 {self.start_date}...")
        return self._sync_symbols(symbols, _BACKFILL_COUNT, "回填", from_date=self.start_date)

    def repair_latest_gaps(self, symbols: list[str]) -> list[str]:
        """对未覆盖本批最新交易日的 ETF 进行一次扩大窗口补拉，返回仍缺失的标的。"""
        missing = self.get_symbols_missing_latest(symbols)
        if not missing:
            return []

        logger.warning(
            f"发现 {len(missing)} 只 ETF 未覆盖本批最新交易日，开始扩大窗口补拉..."
        )
        self._sync_symbols(missing, _INCREMENTAL_COUNT * 2, "缺口补拉")
        remaining = self.get_symbols_missing_latest(symbols)
        if remaining:
            logger.warning(
                f"缺口补拉后仍有 {len(remaining)} 只 ETF 未覆盖最新交易日："
                f"{', '.join(remaining[:20])}{'...' if len(remaining) > 20 else ''}"
            )
        return remaining

    def sync_daily(self, symbols: list[str]) -> int:
        """增量同步指定 ETF 的最近日 K。"""
        logger.info(f"开始增量同步 {len(symbols)} 只 ETF...")
        count = self._sync_symbols(symbols, _INCREMENTAL_COUNT, "增量同步")
        self.repair_latest_gaps(symbols)
        coverage = self.get_latest_daily_coverage_for_symbols(symbols)
        logger.info(
            "最新交易日覆盖："
            f"{coverage['latest_date']} | "
            f"{coverage['latest_symbols']}/{coverage['total_symbols']} 只"
        )
        return count

    # ── 指标聚合 ──

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float | None:
        if len(close) < period + 1:
            return None
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.rolling(period).mean().iloc[-1]
        avg_loss = loss.rolling(period).mean().iloc[-1]
        if pd.isna(avg_gain) or pd.isna(avg_loss):
            return None
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100.0 - 100.0 / (1.0 + rs))

    @staticmethod
    def _atr_pct(df: pd.DataFrame, period: int = 14) -> float | None:
        if len(df) < period + 1:
            return None
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        last_close = df["close"].iloc[-1]
        if pd.isna(atr) or not last_close:
            return None
        return float(atr / last_close * 100.0)

    def _compute_metrics_row(self, symbol: str, df: pd.DataFrame) -> tuple | None:
        """由单只 ETF 日线计算一行 etf_metrics。"""
        if df is None or len(df) < 20:
            return None

        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"]
        amount = df["amount"]
        n = len(df)

        def ma(window: int) -> float | None:
            if n < window:
                return None
            return float(close.rolling(window).mean().iloc[-1])

        def ret(window: int) -> float | None:
            if n <= window or close.iloc[-1 - window] == 0:
                return None
            return float(close.iloc[-1] / close.iloc[-1 - window] - 1.0)

        last_close = float(close.iloc[-1])
        ma200 = ma(200)
        vol_amount_20 = (
            float(amount.rolling(20).mean().iloc[-1]) if n >= 20 else None
        )
        high_120 = float(close.tail(120).max()) if n >= 1 else None
        window_60 = close.tail(60)
        peak_60 = float(window_60.max()) if len(window_60) else None
        drawdown_60d = (
            float(last_close / peak_60 - 1.0) if peak_60 else None
        )

        return (
            symbol,
            str(df["date"].iloc[-1]),
            last_close,
            ma(20),
            ma(50),
            ma200,
            ret(20),
            ret(60),
            ret(120),
            vol_amount_20,
            float(amount.iloc[-1]),
            self._atr_pct(df),
            self._rsi(close),
            high_120,
            drawdown_60d,
            1 if (ma200 is not None and last_close > ma200) else 0,
            n,
        )

    def refresh_metrics(self, symbols: list[str] | None = None) -> int:
        """重新计算并写入 etf_metrics。"""
        symbols = symbols or self.get_local_symbols()
        rows: list[tuple] = []
        for symbol in symbols:
            try:
                df = self.get_ohlcv(symbol)
                row = self._compute_metrics_row(symbol, df)
                if row is not None:
                    rows.append(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{symbol}] 指标计算失败：{exc}")

        if rows:
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany(_UPSERT_METRICS_SQL, rows)
                conn.commit()
        logger.info(f"etf_metrics 刷新完成，共 {len(rows)} 只 ETF")
        return len(rows)

    def get_metrics_frame(self) -> pd.DataFrame:
        """读取 etf_metrics（关联 etf_basic 名称）为 DataFrame。

        SQLite 中的 NULL 会被强制转换为 NaN，保证数值列为数值类型，
        便于策略/报告直接做向量化比较。
        """
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                """
                SELECT m.*, COALESCE(b.name, m.symbol) AS name
                FROM etf_metrics m
                LEFT JOIN etf_basic b ON m.symbol = b.symbol
                """,
                conn,
            )
        numeric_cols = [c for c in df.columns if c not in ("symbol", "update_date", "name")]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
